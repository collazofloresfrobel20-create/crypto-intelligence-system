"""Índice de búsqueda de la última admisión y el último descarte por contrato."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import get_conn, init_db


# Diez parámetros por contrato. Los lotes dejan margen bajo el límite de SQLite.
BATCH_SIZE = 32


def market_price(raw):
    try:
        value = float(raw)
        return value if math.isfinite(value) and value > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def index_capture(conn, run):
    snapshot = conn.execute("SELECT observed_at,payload FROM shadow_captures WHERE run_id=? "
                            "AND status IN ('captured','complete')", (run,)).fetchone()
    if snapshot is None:
        raise ValueError("La captura todavía no está lista para indexar")
    stamp = snapshot["observed_at"]
    values = []
    for key, record in json.loads(snapshot["payload"])["records"].items():
        token = record["token"]
        price = market_price(token.get("price"))
        values.append((key, int(record["eligible"]), stamp, run, token.get("symbol"), token.get("name"),
                       json.dumps(record["reasons"], ensure_ascii=False), stamp, price, price))
    insert = "INSERT INTO search_archive(token_key,eligible,observed_at,run_id,symbol,name,reasons,first_at,first_price,last_price) VALUES "
    for start in range(0, len(values), BATCH_SIZE):
        batch = values[start:start+BATCH_SIZE]
        placeholders = ",".join(["(?,?,?,?,?,?,?,?,?,?)"] * len(batch))
        conn.execute(insert + placeholders + """
        ON CONFLICT(token_key,eligible) DO UPDATE SET
            first_price=CASE WHEN first_at IS NULL OR excluded.first_at<first_at THEN excluded.first_price ELSE first_price END,
            first_at=min(coalesce(first_at,excluded.first_at),excluded.first_at),
            run_id=CASE WHEN excluded.observed_at>=observed_at THEN excluded.run_id ELSE run_id END,
            symbol=CASE WHEN excluded.observed_at>=observed_at THEN excluded.symbol ELSE symbol END,
            name=CASE WHEN excluded.observed_at>=observed_at THEN excluded.name ELSE name END,
            reasons=CASE WHEN excluded.observed_at>=observed_at THEN excluded.reasons ELSE reasons END,
            last_price=CASE WHEN excluded.observed_at>=observed_at THEN excluded.last_price ELSE last_price END,
            observed_at=max(observed_at,excluded.observed_at)
        """, [value for row in batch for value in row])



if __name__ == "__main__":
    init_db()
    with get_conn() as conn:
        runs = conn.execute("SELECT run_id FROM shadow_captures WHERE status IN ('captured','complete') ORDER BY observed_at").fetchall()
        for row in runs:
            index_capture(conn, row["run_id"])
        print("Contratos indexados:", conn.execute("SELECT COUNT(DISTINCT token_key) AS n FROM search_archive").fetchone()["n"])
