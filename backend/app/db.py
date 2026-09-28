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

CREATE TABLE IF NOT EXISTS ml_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- 'selector' (Fase 1) | 'calibration' (Fase 3)
    trained_at TEXT NOT NULL,
    n_samples INTEGER,
    metrics TEXT,                -- JSON: precision/recall/PR-AUC/falsos negativos + benchmark
    model_blob TEXT NOT NULL     -- joblib serializado, base64 (vive en Turso: los runners de
                                  -- GitHub Actions son máquinas desechables sin disco persistente)
);
CREATE INDEX IF NOT EXISTS idx_ml_models_kind_trained ON ml_models (kind, trained_at);

-- Plan de correcciones, Fase A (2026-09-28): consumo real de tokens por llamada a un LLM, para
-- poder proyectar el costo si algún día se sale del free tier (hoy solo se contaban llamadas).
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    provider TEXT NOT NULL,      -- 'gemini' | 'groq'
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER
);

-- Análisis estadísticos precalculados en Python al final de cada ciclo (el Worker no ejecuta
-- Python ni numpy): el dashboard solo lee el más reciente de cada tipo.
CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- 'edge_report'
    computed_at TEXT NOT NULL,
    payload TEXT NOT NULL        -- JSON
);
CREATE INDEX IF NOT EXISTS idx_analytics_kind_computed ON analytics_snapshots (kind, computed_at);
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
    # Fase 1 (clasificador ML, 2026-09-24): features de descubrimiento que antes no se
    # persistían, necesarias para poder entrenar después contra el resultado real.
    "ALTER TABLE predictions ADD COLUMN listing_age_days_at_discovery REAL",
    "ALTER TABLE predictions ADD COLUMN pct_change_24h_at_discovery REAL",
    # Fase 2 (ensemble del Juez vía Groq, 2026-09-24): segunda opinión independiente, guardada
    # aparte del veredicto/scores de Gemini para poder auditar acuerdo/desacuerdo.
    "ALTER TABLE predictions ADD COLUMN secondary_judge_verdict TEXT",
    "ALTER TABLE predictions ADD COLUMN secondary_judge_opportunity_score INTEGER",
    "ALTER TABLE predictions ADD COLUMN secondary_judge_risk_score INTEGER",
    "ALTER TABLE predictions ADD COLUMN secondary_judge_confidence_score INTEGER",
    "ALTER TABLE predictions ADD COLUMN secondary_judge_earliness_score INTEGER",
    "ALTER TABLE predictions ADD COLUMN judge_agreement INTEGER",
    # Fase 3 (curva de calibración, 2026-09-24): confidence_score corregido contra el acierto
    # real histórico -- se guarda aparte para poder mostrar crudo y calibrado juntos.
    "ALTER TABLE predictions ADD COLUMN confidence_score_calibrated REAL",
    # Fase 4 (señal on-chain barata vía GoPlus extendido, 2026-09-24): features estructuradas
    # (antes solo visibles como texto crudo para los analistas LLM), necesarias como historial
    # para que el clasificador de la Fase 1 pueda aprender de ellas.
    "ALTER TABLE predictions ADD COLUMN creator_percent REAL",
    "ALTER TABLE predictions ADD COLUMN owner_percent REAL",
    "ALTER TABLE predictions ADD COLUMN lp_holders_locked_pct REAL",
    "ALTER TABLE predictions ADD COLUMN is_honeypot INTEGER",
    "ALTER TABLE predictions ADD COLUMN is_mintable INTEGER",
    # Fase 5 (alertas + dedup, 2026-09-24): guardia de idempotencia explícita para Telegram --
    # defensiva, ante futuros cambios en la lógica de exclusión de re-research.
    "ALTER TABLE predictions ADD COLUMN notified_at TEXT",
    # Plan de correcciones, Fase A (2026-09-28): qué fuentes de datos respondieron de verdad al
    # analizar (antes solo quedaba una línea en el log), qué fracción de ellas, y en qué régimen
    # de mercado (BTC) se hizo la predicción -- para condicionar el benchmark, ver H2 del plan.
    "ALTER TABLE predictions ADD COLUMN data_quality TEXT",
    "ALTER TABLE predictions ADD COLUMN data_completeness REAL",
    "ALTER TABLE predictions ADD COLUMN market_regime TEXT",
    # Plan v2, B1 (2026-09-28): feature store de precio (klines 1h de los 7 dias previos),
    # calculado al descubrir para analizados y control vivo -- 0 tokens de LLM.
    "ALTER TABLE predictions ADD COLUMN vol_hourly_pct REAL",
    "ALTER TABLE predictions ADD COLUMN momentum_7d_pct REAL",
    "ALTER TABLE predictions ADD COLUMN max_drawdown_7d_pct REAL",
    "ALTER TABLE predictions ADD COLUMN dist_from_high_pct REAL",
    "ALTER TABLE predictions ADD COLUMN dist_from_low_pct REAL",
    "ALTER TABLE predictions ADD COLUMN range_pos_7d REAL",
    "ALTER TABLE predictions ADD COLUMN vol_trend_24h_vs_7d REAL",
    # Plan v2, B2: probabilidad que cada modelo de precio asignó AL DESCUBRIR (aunque el modelo no
    # decida nada todavía) -> historial fuera de muestra que se acumula solo, sin gastar LLM.
    "ALTER TABLE predictions ADD COLUMN pm_touch20 REAL",
    "ALTER TABLE predictions ADD COLUMN pm_sustained10 REAL",
    "ALTER TABLE predictions ADD COLUMN pm_drop20 REAL",
    # Plan v2, B3: contexto de movimiento (JSON, calculado por codigo, sin LLM).
    "ALTER TABLE predictions ADD COLUMN movement_context TEXT",
    # Plan v2, C1: confidence con techo por fuentes criticas faltantes (el crudo no se toca).
    "ALTER TABLE predictions ADD COLUMN confidence_score_capped INTEGER",
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
                "rejection_margins", "metrics", "data_quality", "market_regime", "payload", "movement_context"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, json.JSONDecodeError):
                pass
    return d
