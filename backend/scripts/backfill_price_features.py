"""
Plan v2, B1.4 -- backfill ÚNICO de las features de precio (vol_hourly_pct, momentum_7d_pct, ...)
para filas históricas de `predictions`, usando klines de 1h con endTime = momento de la
predicción (las 168 velas de los 7 días PREVIOS: exactamente lo que el sistema habría visto).

Solo escribe las columnas nuevas de features, y solo donde están NULL (nunca pisa datos).
Reanudable: al relanzar, salta lo que ya tiene features. Sin LLM.

Uso (desde backend/):
    python scripts/backfill_price_features.py --dry-run --limit 20   # no escribe nada
    python scripts/backfill_price_features.py                        # todo lo pendiente (~30 min)
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn, init_db
from app.market_stats import compute_market_stats, price_features, PRICE_FEATURE_COLUMNS

COLS = list(PRICE_FEATURE_COLUMNS.keys())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="calcula y muestra, NO escribe en la base")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.08)
    args = ap.parse_args()

    if not args.dry_run:
        init_db()  # migraciones aditivas: crea las columnas de features si aun no existen

    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, alpha_id, created_at FROM predictions "
            "WHERE vol_hourly_pct IS NULL AND alpha_id IS NOT NULL AND source = 'binance_alpha' "
            "AND status IN ('evaluated', 'pending') ORDER BY id"
        ).fetchall()]
    if args.limit:
        rows = rows[: args.limit]
    print(f"{len(rows)} filas sin features de precio{' (dry-run: no se escribe)' if args.dry_run else ''}.")

    done = skipped = 0
    t0 = time.time()
    for i, r in enumerate(rows, start=1):
        end_ms = int(datetime.fromisoformat(r["created_at"]).timestamp() * 1000)
        feats = price_features(compute_market_stats(r["alpha_id"], end_time_ms=end_ms))
        if feats["vol_hourly_pct"] is None:  # sin klines suficientes: se deja NULL
            skipped += 1
        else:
            done += 1
            if not args.dry_run:
                with get_conn() as conn:
                    conn.execute(
                        f"UPDATE predictions SET {', '.join(c + ' = ?' for c in COLS)} "
                        "WHERE id = ? AND vol_hourly_pct IS NULL",
                        [feats[c] for c in COLS] + [r["id"]],
                    )
            if args.dry_run and done <= 3:
                print("  ejemplo id", r["id"], {k: v for k, v in feats.items()})
        if i % 100 == 0:
            print(f"  [{i}/{len(rows)}] con features: {done}, sin klines suficientes: {skipped}, {time.time() - t0:.0f}s")
        time.sleep(args.sleep)
    print(f"Listo: {done} con features, {skipped} sin klines suficientes (quedan NULL), {time.time() - t0:.0f}s.")


if __name__ == "__main__":
    main()
