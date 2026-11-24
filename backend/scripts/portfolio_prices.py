"""Archiva velas cerradas y precios DEX por contrato para la cartera simulada."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import binance_alpha, shadow_dexscreener as dex
from app.db import get_conn, init_db
from app.shadow_identity import _asset_key


CHAINS = "('bsc','bnb','bnb chain','bnb smart chain','56','sol','solana')"


def valid_bars(raw, start_ms, end_ms):
    """[apertura ms, apertura, máximo, mínimo, cierre, cierre ms]; nunca una vela en curso."""
    bars = {}
    for row in raw:
        try:
            opening, closing = int(row[0]), int(row[6])
            prices = [float(row[i]) for i in (1, 2, 3, 4)]
            op, high, low, close = prices
            if (start_ms <= closing <= end_ms and closing-opening in (3_599_999,3_600_000)
                    and all(math.isfinite(x) and x > 0 for x in prices)
                    and low <= min(op,close) <= max(op,close) <= high):
                bars[opening] = [opening, *prices, closing]
        except (ValueError, TypeError, IndexError, OverflowError):
            continue
    return [bars[k] for k in sorted(bars)]


def get_path(row, now):
    start = datetime.fromisoformat(row["created_at"])
    last = start + timedelta(days=30, hours=2)
    through = min(last, now)
    end_ms = int(through.timestamp()*1000)
    try:
        rows = binance_alpha.get_klines(row["alpha_id"]+"USDT", interval="1h", limit=750, end_time_ms=end_ms)
        bars = valid_bars(rows, int(start.timestamp()*1000), end_ms)
        return bars, int(through == last and bool(bars)), None if bars else "sin_velas"
    except Exception as exc:
        return [], 0, type(exc).__name__


def refresh_paths(now, limit):
    # Durante el archivo inicial se atienden primero las observaciones recientes. Los
    # errores conservan las velas anteriores y ceden su turno a las entradas sin atender.
    with get_conn() as db:
        rows = db.execute(
            f"SELECT p.id,p.created_at,p.alpha_id FROM predictions p "
            "LEFT JOIN portfolio_paths h ON h.prediction_id=p.id "
            f"WHERE p.category='analyzed' AND lower(trim(p.chain_name)) IN {CHAINS} "
            "AND p.contract_address IS NOT NULL AND p.contract_address!='' "
            "AND p.alpha_id IS NOT NULL AND p.alpha_id!='' AND COALESCE(p.source,'binance_alpha')='binance_alpha' "
            "AND p.created_at<=? AND (h.prediction_id IS NULL OR (h.finished=0 AND h.checked_at<?)) "
            "ORDER BY h.checked_at ASC, p.created_at DESC, p.id LIMIT ?",
            (now.isoformat(), (now-timedelta(hours=6)).isoformat(), limit)).fetchall()
    accepted = errors = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for row, (bars, finished, error) in zip(rows, pool.map(lambda r: get_path(r,now), rows)):
            with get_conn() as db:
                old = db.execute("SELECT bars,checked_at FROM portfolio_paths WHERE prediction_id=?",(row["id"],)).fetchone()
                if old and old["checked_at"] > now.isoformat():
                    continue
                merged = {b[0]:b for b in json.loads(old["bars"])} if old else {}
                merged.update((b[0],b) for b in bars)
                saved = json.dumps([merged[k] for k in sorted(merged)], separators=(",",":"))
                db.execute("INSERT INTO portfolio_paths VALUES(?,?,?,?,?) ON CONFLICT(prediction_id) DO UPDATE SET "
                           "checked_at=excluded.checked_at, finished=excluded.finished, bars=excluded.bars, error=excluded.error "
                           "WHERE excluded.checked_at>=portfolio_paths.checked_at",
                           (row["id"],now.isoformat(),finished,saved,error))
            accepted += bool(bars)
            errors += bool(error)
    return {"consultas":len(rows),"con_velas":accepted,"sin_velas_o_error":errors}


def refresh_spots(now, slot):
    start = now-timedelta(days=31)
    with get_conn() as db:
        rows = db.execute(f"SELECT DISTINCT chain_name, contract_address FROM predictions "
                          f"WHERE category='analyzed' AND lower(trim(chain_name)) IN {CHAINS} "
                          "AND created_at BETWEEN ? AND ? AND contract_address IS NOT NULL AND contract_address!=''",
                          (start.isoformat(), now.isoformat())).fetchall()
    keys = {_asset_key(r["chain_name"], r["contract_address"], None) for r in rows}
    saved = 0
    for chain in ("bsc","solana"):
        addresses = sorted(k.split(":",1)[1] for k in keys if k and k.startswith(chain+":"))
        for i in range(0,len(addresses),dex.BATCH_SIZE):
            group = addresses[i:i+dex.BATCH_SIZE]
            pairs = dex._get_pairs_batch(chain,group)
            observed = datetime.now(timezone.utc).isoformat()
            with get_conn() as db:
                for address in group:
                    price = dex.price_from_pairs(pairs,chain,address)
                    if price is None:
                        continue
                    pair = dex.best_pair(pairs,chain,address)
                    db.execute("INSERT OR IGNORE INTO portfolio_quotes(token_key,observed_at,price,pool,slot) VALUES(?,?,?,?,?)",
                               (chain+":"+address,observed,price,pair.get("pairAddress"),slot))
                    saved += 1
    return {"contratos":len(keys),"cotizados":saved}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit",type=int,default=32)
    args = parser.parse_args()
    if not 1 <= args.limit <= 300:
        parser.error("--limit admite de 1 a 300 registros por ejecución")
    init_db()
    now = datetime.now(timezone.utc)
    slot = os.getenv("GITHUB_RUN_ID") or now.strftime("%Y-%m-%dT%H")
    print(json.dumps({"dex":refresh_spots(now,slot), "alpha":refresh_paths(now,args.limit)},ensure_ascii=False))
