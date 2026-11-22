"""Captura en sombra con APIs públicas. No llama IA ni envía alertas."""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import (binance_alpha, shadow_dexscreener as dexscreener, filters, shadow_goplus as goplus, horizon_tracking, shadow_market_regime as market_regime,
                 news_evidence, shadow_identity as pipeline, project_evidence, project_rubric, selection_policy, thesis_review)
from app.config import settings
from app.db import get_conn, init_db


def store_dossier(run,k,token,catalog,headlines):
    d = project_evidence.collect(k)
    d.update(protocol=project_evidence.protocol_context(k,catalog),news=news_evidence.investigate(token,headlines),
             market_source=token.get("source") or "binance_alpha",retrieved_at=datetime.now(timezone.utc).isoformat(),
             observed_market={f:token.get(f) for f in ("price","liquidity","marketCap","fdv","volume24h","holders","percentChange24h")},
             security_at_read=goplus.get_token_security(token.get("chainName"),token.get("contractAddress")),
             market_signals=pipeline._market_signals(k,run))
    d["thesis_review"] = thesis_review.assess(d)
    d["project_card"] = project_rubric.evaluate(d)
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO research_dossiers VALUES (?,?,?,?)",(run,k,d["retrieved_at"],json.dumps(d,ensure_ascii=False)))


def capture(limit=6):
    init_db()
    run = "shadow-" + uuid.uuid4().hex[:12]
    stamp = datetime.now(timezone.utc).isoformat()
    market = market_regime.compute_regime() or {}
    horizon_tracking.capture(btc_usdt=market.get("btc_usdt"))
    universe = {}
    errors = []
    feeds = [("binance_alpha",binance_alpha.get_alpha_token_list),
             ("dex_bsc",lambda: dexscreener.get_universe("bsc")),
             ("dex_solana",lambda: dexscreener.get_universe("solana"))]
    for source, fetch in feeds:
        try:
            for token in fetch():
                key = pipeline._token_key(token) or ""
                if key.startswith(("solana:","bsc:")):
                    universe.setdefault(key,token)
        except Exception as e:
            errors.append(source + " " + type(e).__name__)
    if not universe:
        raise RuntimeError("Sin observaciones disponibles: " + ", ".join(errors))
    eligible = {k:t for k,t in universe.items() if filters.passes_hard_filters(t)[0]}
    ranked = sorted(eligible,key=lambda k:(-filters.heuristic_score(eligible[k]),k))
    arms = selection_policy.comparison_arms(ranked,limit,run)
    version = selection_policy.version(settings) + "-public-shadow-" + str(limit)
    with get_conn() as conn:
        conn.execute("INSERT INTO run_versions VALUES (?,?)",(run,version))
        conn.execute("INSERT INTO observation_benchmarks VALUES (?,?,?)",(run,market.get("btc_usdt"),stamp))
        for k,t in eligible.items():
            conn.execute("INSERT INTO survivor_observations "
                         "(run_id,token_key,observed_at,symbol,source,price,liquidity,volume_24h,holders,eligible,shortlisted,shadow_arm) "
                         "VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",(run,k,stamp,t.get("symbol"),t.get("source") or "binance_alpha",
                         t.get("price"),t.get("liquidity"),t.get("volume24h"),t.get("holders"),int(k in arms),arms.get(k)))
    catalog = project_evidence.protocol_catalog() if arms else {}
    headlines = news_evidence.news_index() if arms else {}
    for k in arms:
        token = eligible[k]
        store_dossier(run,k,token,catalog,headlines)
    return {"run":run,"universe":len(universe),"eligible":len(eligible),"dossiers":len(arms),"feed_errors":errors}


if __name__ == "__main__":
    print(json.dumps(capture(),ensure_ascii=False))
