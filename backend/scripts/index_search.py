"""Índice de búsqueda de la última admisión y el último descarte por contrato."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import get_conn, init_db


def index_capture(conn, run):
    conn.execute("""
        INSERT INTO search_archive(token_key,eligible,observed_at,run_id,symbol,name,reasons,first_at,first_price,last_price)
        SELECT j.key, json_extract(j.value,'$.eligible'), c.observed_at, c.run_id,
               json_extract(j.value,'$.token.symbol'), json_extract(j.value,'$.token.name'),
               json_extract(j.value,'$.reasons'), c.observed_at, CAST(json_extract(j.value,'$.token.price') AS REAL),
               CAST(json_extract(j.value,'$.token.price') AS REAL)
        FROM shadow_captures c, json_each(c.payload,'$.records') j
        WHERE c.run_id=? AND c.status IN ('captured','complete')
        ON CONFLICT(token_key,eligible) DO UPDATE SET
            first_price=CASE WHEN excluded.first_at<first_at THEN excluded.first_price ELSE first_price END,
            first_at=min(first_at,excluded.first_at),
            run_id=CASE WHEN excluded.observed_at>=observed_at THEN excluded.run_id ELSE run_id END,
            symbol=CASE WHEN excluded.observed_at>=observed_at THEN excluded.symbol ELSE symbol END,
            name=CASE WHEN excluded.observed_at>=observed_at THEN excluded.name ELSE name END,
            reasons=CASE WHEN excluded.observed_at>=observed_at THEN excluded.reasons ELSE reasons END,
            last_price=CASE WHEN excluded.observed_at>=observed_at THEN excluded.last_price ELSE last_price END,
            observed_at=max(observed_at,excluded.observed_at)
    """, (run,))


if __name__ == "__main__":
    init_db()
    with get_conn() as conn:
        runs = conn.execute("SELECT run_id FROM shadow_captures WHERE status IN ('captured','complete') ORDER BY observed_at").fetchall()
        for row in runs:
            index_capture(conn, row["run_id"])
        print("Contratos indexados:", conn.execute("SELECT COUNT(DISTINCT token_key) AS n FROM search_archive").fetchone()["n"])
