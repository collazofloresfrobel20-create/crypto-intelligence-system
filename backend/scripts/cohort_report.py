"""Retornos por contrato dentro de la cohorte. Solo lectura, incluida Turso."""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import get_conn


def amm_net(growth, entry_depth, exit_depth, ticket, fee):
    if not all(valid_price(v) for v in (growth,entry_depth,exit_depth,ticket)) or not 0 <= fee < 1:
        return None
    # Pool x*y=k, reservas 50/50. Impuestos y gas desconocidos quedan fuera.
    entry = ticket*(1-fee)
    exit_notional = entry/(1+entry/(entry_depth/2))*growth*(1-fee)
    return 100*(exit_notional/(1+exit_notional/(exit_depth/2))/ticket-1)


def summarize(entries, horizon, as_of, independent_prices=(), ticket=100., fee=.003, universe=False):
    entries = [dict(row) for row in entries]
    independent_prices = [dict(row) for row in independent_prices]
    costs, excess = defaultdict(list), defaultdict(list)
    deadline = lambda row: datetime.fromisoformat(row["observed_at"]) + timedelta(days=horizon)
    by_key = defaultdict(list)
    for row in entries:
        if datetime.fromisoformat(row["observed_at"]) <= as_of:
            by_key[row["token_key"]].append(row)
    prices = {row["token_key"]: row for row in independent_prices
              if datetime.fromisoformat(row["observed_at"]) <= as_of and row["days"] == horizon}
    groups = defaultdict(list)
    for observations in by_key.values():
        observations.sort(key=lambda row: row["observed_at"])
        first = next((row for row in observations if row["eligible"]), None)
        if first is None:
            continue
        group = "alternativa"
        if first["shortlisted"]:
            group = {"ranked":"ranking","random":"sorteo"}.get(first.get("shadow_arm"),"preseleccionado") if first["run_id"].startswith("shadow-") else "preseleccionado"
        if universe:
            group = "todos_los_elegibles"
        chain = first["token_key"].split(":", 1)[0]
        values = groups[(chain, first["config_hash"], group)]
        target = deadline(first)
        # Dejar que cierre la ventana antes de contar como perdido un seguimiento.
        if target + timedelta(hours=24) > as_of:
            values.append(("inmaduro", None))
            continue
        closing = next((row for row in observations if target <= datetime.fromisoformat(row["observed_at"])
                        <= target + timedelta(hours=24) and valid_price(row["price"])), None)
        independent = prices.get(first["token_key"])
        if independent and target <= datetime.fromisoformat(independent["observed_at"]) <= target + timedelta(hours=24):
            closing = independent
        if not closing or not valid_price(first["price"]):
            values.append(("sin_precio", None))
        else:
            growth = float(closing["price"])/float(first["price"])
            values.append(("medido", 100*(growth-1)))
            key = chain,first["config_hash"],group
            net = amm_net(growth,first.get("liquidity"),closing.get("liquidity"),ticket,fee)
            if net is not None:
                costs[key].append(net)
                if valid_price(first.get("btc_usdt")) and valid_price(closing.get("btc_usdt")):
                    excess[key].append(net-100*(closing["btc_usdt"]/first["btc_usdt"]-1))
    report = []
    for (chain, version, group), values in sorted(groups.items()):
        returns = [value for state, value in values if state == "medido"]
        missing = sum(state == "sin_precio" for state, _ in values)
        stress = returns + [-100] * missing
        net = costs[(chain,version,group)]
        btc = excess[(chain,version,group)]
        report.append(dict(ticket_usd=ticket, comision_por_lado=fee, con_costos=len(net),
                           mediana_neta_amm_pct=statistics.median(net) if net else None,
                           con_benchmark_btc=len(btc), exceso_mediano_vs_btc_pp=statistics.median(btc) if btc else None,
                           cadena=chain, version=version, grupo=group, contratos=len(values),
                           con_precio=len(returns), sin_precio=missing,
                           inmaduros=len(values)-len(returns)-missing,
                           mediana_bruta_pct=statistics.median(returns) if returns else None,
                           mediana_estres_pct=statistics.median(stress) if stress else None))
    return report


def valid_price(value):
    try:
        price = float(value)
        return math.isfinite(price) and price > 0
    except (ValueError, TypeError):
        return False


def inputs():
    with get_conn() as conn:
        rows = conn.execute("SELECT s.*, v.config_hash,b.btc_usdt FROM survivor_observations s LEFT JOIN observation_benchmarks b ON b.run_id=s.run_id "
                            "JOIN run_versions v ON s.run_id=v.run_id LEFT JOIN shadow_captures cap ON cap.run_id=s.run_id "
                            "WHERE cap.run_id IS NULL OR cap.status IN ('captured','complete') ORDER BY s.observed_at, s.run_id").fetchall()
        independent = conn.execute("SELECT h.*,c.liquidity,c.btc_usdt FROM horizon_prices h LEFT JOIN horizon_context c ON c.token_key=h.token_key AND c.days=h.days AND c.observed_at=h.observed_at").fetchall()
    return rows, independent


def coverage_status(rows, now):
    """Catorce días UTC cerrados, con las dos capturas de calendario y cupos por cadena."""
    sessions = defaultdict(list)
    for row in rows:
        day = datetime.fromisoformat(row["observed_at"]).astimezone(timezone.utc).date()
        if row["event"] == "schedule":
            sessions[day].append(row)
    dates = []
    for back in range(14, 0, -1):
        day = now.date() - timedelta(days=back)
        group = sessions[day]
        valid = sum(1 for row in group if row["status"] in ("captured", "complete")
                    and row["full_quota"] == 1 and row["feeds_ok"] == 1)
        dates.append(dict(fecha=day.isoformat(), programadas=len(group), validas=valid,
                          cumple=len(group) >= 2 and valid == len(group)))
    streak = 0
    for item in reversed(dates):
        if not item["cumple"]:
            break
        streak += 1
    return dict(dias_consecutivos=streak, objetivo_dias=14, cumple=streak == 14, fechas=dates)


def research_coverage(db, now):
    """Contratos elegibles distintos investigados en sus propias corridas programadas."""
    sql = """
        SELECT substr(s.token_key,1,instr(s.token_key,':')-1) AS chain,
               count(DISTINCT s.token_key) AS eligible,
               count(DISTINCT d.token_key) AS researched
          FROM shadow_captures c
          JOIN survivor_observations s ON s.run_id=c.run_id AND s.eligible=1
          LEFT JOIN research_dossiers d ON d.run_id=s.run_id AND d.token_key=s.token_key
         WHERE c.status IN ('captured','complete') AND json_extract(c.payload,'$.event')='schedule'
           AND c.observed_at>=? AND c.observed_at<=?
         GROUP BY chain
    """
    by_chain = {r["chain"]:r for r in db.execute(sql, ((now-timedelta(days=14)).isoformat(),now.isoformat())).fetchall()}
    rows = []
    for chain in ("bsc","solana"):
        row = by_chain.get(chain, {"eligible":0,"researched":0})
        eligible, researched = row["eligible"], row["researched"]
        rows.append(dict(cadena=chain,elegibles=eligible,investigados=researched,
                         porcentaje=round(100*researched/eligible,1) if eligible else None))
    return rows


def publish_snapshot():
    rows, independent = inputs()
    now = datetime.now(timezone.utc)
    results = {str(days):summarize(rows,days,now,independent) for days in (7,30,90,180)}
    from app import volume_study
    results["estudio_volumen"] = volume_study.evaluate(rows, independent, now)
    results["universo_elegible"] = {str(days): summarize(rows, days, now, independent, universe=True)
                                  for days in (7,30,90,180)}
    with get_conn() as db:
        sessions = db.execute("SELECT observed_at,status,json_extract(payload,'$.event') AS event, "
                              "json_extract(payload,'$.full_quota') AS full_quota, json_extract(payload,'$.feeds_ok') AS feeds_ok "
                              "FROM shadow_captures WHERE observed_at>=?", ((now-timedelta(days=15)).isoformat(),)).fetchall()
        from app import matched_controls
        results["pares_por_liquidez"] = matched_controls.report(db, now, amm_net)
        results["cobertura"] = coverage_status(sessions, now)
        results["cobertura_investigacion"] = research_coverage(db, now)
        db.execute("INSERT INTO analytics_snapshots(kind,computed_at,payload) VALUES(?,?,?)",
                   ("shadow_cohorts",now.isoformat(),json.dumps(results,ensure_ascii=False)))
    return results


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--days", type=int, choices=(7,30,90,180), default=7)
    cli.add_argument("--ticket-usd",type=float,default=100)
    cli.add_argument("--fee",type=float,default=.003)
    args = cli.parse_args()
    if not valid_price(args.ticket_usd) or not 0 <= args.fee < 1:
        cli.error("Monto o comisión inválidos")
    rows, independent = inputs()
    print(json.dumps(summarize(rows,args.days,datetime.now(timezone.utc),independent,args.ticket_usd,args.fee),indent=2))
