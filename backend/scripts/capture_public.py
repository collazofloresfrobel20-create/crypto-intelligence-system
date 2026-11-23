"""Captura en sombra con APIs públicas. No llama IA ni envía alertas."""
import hashlib
import json
import os
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
    d.update(pair_activity=token.get("pair_activity"), protocol=project_evidence.protocol_context(k,catalog),news=news_evidence.investigate(token,headlines),
             market_source=token.get("source") or "binance_alpha",retrieved_at=datetime.now(timezone.utc).isoformat(),
             observed_market={f:token.get(f) for f in ("price","liquidity","marketCap","fdv","volume24h","holders","percentChange24h")},
             security_at_read=goplus.get_token_security(token.get("chainName"),token.get("contractAddress")),
             market_signals=pipeline._market_signals(k,run))
    d["thesis_review"] = thesis_review.assess(d)
    d["project_card"] = project_rubric.evaluate(d)
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO research_dossiers VALUES (?,?,?,?)",(run,k,d["retrieved_at"],json.dumps(d,ensure_ascii=False)))


TOKEN_FIELDS = (
    "symbol", "name", "chainName", "contractAddress", "alphaId", "source", "dexId",
    "price", "liquidity", "volume24h", "holders", "marketCap", "fdv",
    "listingTime", "percentChange24h", "offline",
)


def collect_inputs(run, limit):
    records, feeds = {}, {}
    market = market_regime.compute_regime() or {}
    sources = {
        "binance_alpha": binance_alpha.get_alpha_token_list,
        "dex_bsc": lambda: dexscreener.get_universe("bsc", audit=feeds["dex_bsc"]),
        "dex_solana": lambda: dexscreener.get_universe("solana", audit=feeds["dex_solana"]),
    }
    for name, fetch in sources.items():
        audit = feeds[name] = {"returned": 0, "in_scope": 0, "errors": []}
        try:
            for raw in fetch():
                audit["returned"] += 1
                key = pipeline._token_key(raw) or ""
                if key.startswith(("solana:", "bsc:")):
                    audit["in_scope"] += 1
                    if key not in records:
                        records[key] = {"token": {f: raw.get(f) for f in TOKEN_FIELDS}, "sources": []}
                    if raw.get("pairActivity"):
                        records[key]["pair_activity"] = raw["pairActivity"]
                    if name not in records[key]["sources"]:
                        records[key]["sources"].append(name)
        except Exception as exc:
            audit["errors"].append(type(exc).__name__)
    if not records:
        raise RuntimeError("Ningún contrato observado: " + json.dumps(feeds))

    at = datetime.now(timezone.utc)
    for row in records.values():
        ok, reasons, margins = filters.passes_hard_filters_with_margins(row["token"], now=at.timestamp())
        row.update(eligible=ok, reasons=reasons, margins=margins)
    ranked = sorted((key for key, row in records.items() if row["eligible"]),
                    key=lambda key: (-filters.heuristic_score(records[key]["token"]), key))
    stamp = at.isoformat()
    arms = selection_policy.comparison_arms(ranked, limit, run)
    selected = {chain: sum(key.startswith(chain + ":") for key in arms) for chain in ("bsc", "solana")}
    return dict(records=records, feeds=feeds, arms=arms, event=os.getenv("GITHUB_EVENT_NAME", "local"),
                full_quota=len(arms) == limit and min(selected.values()) >= limit // 2,
                feeds_ok=all(not feed["errors"] and feed["returned"] > 0 for feed in feeds.values()),
                limit=limit, btc_usdt=market.get("btc_usdt"), observed_at=stamp, schema=1,
                filters={name: getattr(settings, name) for name in filters.THRESHOLDS},
                filter_hash=filters.rule_hash())


def read_capture(conn, run):
    return conn.execute("SELECT * FROM shadow_captures WHERE run_id=?", (run,)).fetchone()


def capture(limit=6, run_id=None):
    init_db()
    job = os.getenv("GITHUB_RUN_ID")
    run = run_id or ("shadow-gh-" + job if job else "shadow-" + uuid.uuid4().hex)
    with get_conn() as conn:
        saved = read_capture(conn, run)
    if saved is None:
        data = collect_inputs(run, limit)
        version = selection_policy.version(settings) + "-public-" + str(limit) + "-" + hashlib.sha256(
            Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]
        with get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO shadow_captures VALUES (?,?,?,?,?)",
                         (run, data["observed_at"], version, "pending", json.dumps(data, separators=(",", ":"))))
            saved = read_capture(conn, run)
    data = json.loads(saved["payload"])
    stamp, records, arms = saved["observed_at"], data["records"], data["arms"]
    eligible = {k: r["token"] for k, r in records.items() if r["eligible"]}
    summary = dict(run=run, universe=len(records), eligible=len(eligible), dossiers=len(arms), feeds=data["feeds"])
    if saved["status"] == "complete":
        return dict(summary, reused=True)
    if saved["status"] == "pending":
        horizon_tracking.capture(btc_usdt=data["btc_usdt"] if (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() < 300 else None)
        with get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO run_versions VALUES (?,?)", (run, saved["version"]))
            conn.execute("INSERT OR IGNORE INTO observation_benchmarks VALUES (?,?,?)", (run, data["btc_usdt"], stamp))
            for k, t in eligible.items():
                conn.execute("INSERT OR IGNORE INTO survivor_observations "
                             "(run_id,token_key,observed_at,symbol,source,price,liquidity,volume_24h,holders,eligible,shortlisted,shadow_arm) "
                             "VALUES (?,?,?,?,?,?,?,?,?,1,?,?)", (run,k,stamp,t["symbol"],t["source"] or "binance_alpha",
                             t["price"],t["liquidity"],t["volume24h"],t["holders"],int(k in arms),arms.get(k)))
            done = {r["token_key"] for r in conn.execute(
                "SELECT token_key FROM research_dossiers WHERE run_id=?", (run,)).fetchall()}
        missing = [key for key in arms if key not in done]
        if missing:
            catalog, headlines = project_evidence.protocol_catalog(), news_evidence.news_index()
            failures = []
            for key in missing:
                try:
                    store_dossier(run, key, dict(eligible[key], pair_activity=records[key].get("pair_activity")), catalog, headlines)
                except Exception as exc:
                    failures.append([key, type(exc).__name__])
            if failures:
                raise RuntimeError("Expedientes pendientes: " + json.dumps(failures))
        with get_conn() as conn:
            conn.execute("UPDATE shadow_captures SET status='captured' WHERE run_id=? AND status='pending'", (run,))
    from scripts.cohort_report import publish_snapshot
    publish_snapshot()
    with get_conn() as conn:
        conn.execute("UPDATE shadow_captures SET status='complete' WHERE run_id=?", (run,))
    return dict(summary, reused=False)


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False))
