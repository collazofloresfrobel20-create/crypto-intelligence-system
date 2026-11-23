"""Seguimiento de contratos aunque ya no aparezcan en el feed de descubrimiento."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import shadow_dexscreener as dexscreener
from .db import get_conn


def capture(as_of=None, btc_usdt=None):
    as_of = as_of or datetime.now(timezone.utc)
    groups = defaultdict(list)
    for days in (7, 30, 90, 180):
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT s.token_key, MIN(s.observed_at) AS first_seen "
                "FROM survivor_observations s LEFT JOIN shadow_captures cap ON s.run_id=cap.run_id "
                "WHERE (cap.run_id IS NULL OR cap.status IN ('captured','complete')) AND s.eligible=1 "
                "AND (s.token_key LIKE 'bsc:%' OR s.token_key LIKE 'solana:%') "
                "AND NOT EXISTS (SELECT 1 FROM horizon_prices h WHERE h.token_key=s.token_key AND h.days=?) "
                "GROUP BY s.token_key HAVING MIN(s.observed_at) BETWEEN ? AND ? ORDER BY first_seen",
                (days, (as_of-timedelta(days=days+1)).isoformat(), (as_of-timedelta(days=days)).isoformat())
            ).fetchall()
        for row in rows:
            chain, address = row["token_key"].split(":", 1)
            groups[chain].append((address, days, datetime.fromisoformat(row["first_seen"])))
    saved = 0
    for chain, group in groups.items():
        for i in range(0, len(group), dexscreener.BATCH_SIZE):
            batch = group[i:i+dexscreener.BATCH_SIZE]
            pairs = dexscreener._get_pairs_batch(chain, [a for a, _, _ in batch])
            captured_at = datetime.now(timezone.utc)
            with get_conn() as conn:
                for address, days, start in batch:
                    price = dexscreener.price_from_pairs(pairs, chain, address)
                    if price is None or captured_at > start + timedelta(days=days+1):
                        continue
                    conn.execute("INSERT OR IGNORE INTO horizon_prices (token_key, days, observed_at, price) "
                                 "VALUES (?, ?, ?, ?)", (f"{chain}:{address}", days, captured_at.isoformat(), price))
                    pool = dexscreener.best_pair(pairs, chain, address)
                    conn.execute("INSERT OR IGNORE INTO horizon_context VALUES (?, ?, ?, ?, ?)",
                                 (f"{chain}:{address}", days, captured_at.isoformat(),
                                  (pool.get("liquidity") or {}).get("usd"), btc_usdt))
                    saved += 1
    return saved
