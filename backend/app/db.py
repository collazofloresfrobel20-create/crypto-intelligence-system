"""
Capa de base de datos con dos backends intercambiables, detectados automáticamente:

  - Turso (libSQL remoto, vía HTTP) si TURSO_DATABASE_URL está configurado. Es el modo que
    usa GitHub Actions (cada corrida es una máquina desechable sin disco persistente) y el
    Worker de Cloudflare para leer los mismos datos.
  - SQLite local (archivo en disco) si no hay TURSO_DATABASE_URL. Es el modo de desarrollo
    local de siempre, sin depender de ninguna cuenta externa.

El resto del código (pipeline.py, backtesting.py, auth.py, etc.) no sabe ni le importa cuál
de los dos está activo: ambos exponen `get_conn()` como context manager que entrega un objeto
con `.execute(sql, params).fetchone()/.fetchall()`, igual que sqlite3.
"""
import os
import json
import threading
from contextlib import contextmanager

from .config import settings

_lock = threading.Lock()

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'analyzed',  -- 'analyzed' (paso filtros + research LLM) | 'discarded' (rechazado por hard filters, solo tracking)
    symbol TEXT NOT NULL,
    name TEXT,
    alpha_id TEXT,
    chain_name TEXT,
    contract_address TEXT,
    price_at_prediction REAL,
    market_cap REAL,
    liquidity REAL,
    volume_24h REAL,
    holders INTEGER,
    rejection_reasons TEXT,  -- JSON list, solo para category='discarded'

    opportunity_score INTEGER,
    risk_score INTEGER,
    confidence_score INTEGER,
    earliness_score INTEGER,
    evidence_tier TEXT,
    verdict TEXT,

    bull_case TEXT,
    bear_case TEXT,
    mediator_notes TEXT,
    key_evidence TEXT,       -- JSON list
    main_risks TEXT,         -- JSON list
    system_note TEXT,
    agent_findings TEXT,     -- JSON: raw findings por analista (auditabilidad)

    horizon_days INTEGER,
    created_at TEXT NOT NULL,
    evaluated_at TEXT,

    -- Evaluación post-horizonte (backtesting), aplica tanto a analyzed como a discarded
    price_after REAL,
    max_price_reached REAL,
    max_return_pct REAL,
    return_pct REAL,
    max_drawdown_pct REAL,
    time_to_max_hours REAL,
    thesis_result TEXT,      -- 'yes' | 'no' | 'partial' | NULL si aún no evaluado
    status TEXT DEFAULT 'pending'  -- pending | evaluated
);

CREATE INDEX IF NOT EXISTS idx_predictions_symbol_created ON predictions (symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_predictions_category_status ON predictions (category, status);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'update',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',  -- running | done | error
    log TEXT DEFAULT '[]'  -- JSON list de strings de progreso
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    based_on_evaluated INTEGER,
    patterns_found TEXT,          -- JSON list
    proposed_adjustments TEXT,    -- JSON list
    summary TEXT,
    applied_adjustments TEXT,     -- JSON list, NULL hasta que se aplique
    status TEXT DEFAULT 'proposed'  -- proposed | applied | dismissed
);

CREATE TABLE IF NOT EXISTS gemini_usage (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    client_key TEXT PRIMARY KEY,
    failed_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);

CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_SCHEMA_STATEMENTS = [s.strip() for s in SCHEMA.split(";") if s.strip()]

# Migraciones aditivas simples (ALTER TABLE ADD COLUMN). SQLite/libSQL no soportan
# "ADD COLUMN IF NOT EXISTS", así que cada una se ejecuta suelta y se ignora si la columna ya
# existe -- permite correr init_db() en cada arranque sin mantener un historial de migraciones.
_MIGRATIONS = [
    "ALTER TABLE predictions ADD COLUMN current_price REAL",
    "ALTER TABLE predictions ADD COLUMN current_return_pct REAL",
    "ALTER TABLE predictions ADD COLUMN price_checked_at TEXT",
    "ALTER TABLE predictions ADD COLUMN project_explainer TEXT",
    # Batch 1 de "Mejoras cis.pdf" (2026-09-24):
    "ALTER TABLE predictions ADD COLUMN return_at_day1_pct REAL",
    "ALTER TABLE predictions ADD COLUMN return_at_day3_pct REAL",
    "ALTER TABLE predictions ADD COLUMN volatility_pct REAL",
    "ALTER TABLE predictions ADD COLUMN top10_holder_concentration_pct REAL",
    "ALTER TABLE predictions ADD COLUMN source TEXT DEFAULT 'binance_alpha'",
    "ALTER TABLE predictions ADD COLUMN running_max_price REAL",
    "ALTER TABLE predictions ADD COLUMN running_min_price REAL",
    # Fase 0 (Grupo 3, 2026-09-24):
    "ALTER TABLE predictions ADD COLUMN rejection_margins TEXT",
    "ALTER TABLE predictions ADD COLUMN mediator_contradictions_count INTEGER",
]


if TURSO_DATABASE_URL:
    import libsql_client

    class _RemoteResult:
        """Envuelve un ResultSet de libsql_client para que se vea como un cursor sqlite3."""

        def __init__(self, rs):
            self._rows = [dict(zip(rs.columns, row.astuple())) for row in rs.rows]
            self.lastrowid = rs.last_insert_rowid

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return self._rows

    class _RemoteConn:
        def __init__(self, client):
            self._client = client

        def execute(self, sql, params=()):
            rs = self._client.execute(sql, list(params) if params else [])
            return _RemoteResult(rs)

        def executescript(self, script):
            statements = [s.strip() for s in script.split(";") if s.strip()]
            self._client.batch(statements)

        def commit(self):
            pass  # cada .execute()/.batch() ya es su propia transacción en modo HTTP

    @contextmanager
    def get_conn():
        # libsql_client mapea el esquema "libsql://" siempre a WebSocket (wss://) vía el
        # protocolo Hrana. En pruebas reales ese handshake WS falló (400 Invalid response
        # status) contra Turso. "https://" fuerza el transporte HTTP plano, más compatible y
        # sin necesidad de mantener una conexión persistente — mejor para llamadas puntuales
        # como las de este proyecto.
        http_url = TURSO_DATABASE_URL.replace("libsql://", "https://", 1)
        client = libsql_client.create_client_sync(url=http_url, auth_token=TURSO_AUTH_TOKEN)
        try:
            yield _RemoteConn(client)
        finally:
            client.close()

else:
    import sqlite3

    @contextmanager
    def get_conn():
        conn = sqlite3.connect(settings.DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            with get_conn() as conn:
                conn.execute(stmt)
        except Exception:
            pass  # columna ya existe


def row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("key_evidence", "main_risks", "agent_findings", "rejection_reasons",
                "patterns_found", "proposed_adjustments", "applied_adjustments",
                "rejection_margins"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, json.JSONDecodeError):
                pass
    return d
