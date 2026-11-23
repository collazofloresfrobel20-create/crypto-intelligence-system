"""Reconstruye los filtros de una captura congelada sin llamadas al mercado."""
import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from statistics import median
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import filters
from app.db import get_conn


def compare(data, overrides):
    if data.get("schema") != 1 or data.get("filter_hash") != filters.rule_hash():
        raise ValueError("Usa la revisión del código con la que se capturó el universo.")
    thresholds = dict(data["filters"])
    for name, value in overrides.items():
        if name not in filters.THRESHOLDS or not math.isfinite(value) or value < 0:
            raise ValueError("Umbral inválido: " + name)
        thresholds[name] = value
    config = SimpleNamespace(**thresholds)
    now = datetime.fromisoformat(data["observed_at"]).timestamp()
    gained, lost, eligible = [], [], 0
    for key, row in data["records"].items():
        ok, _, _ = filters.passes_hard_filters_with_margins(row["token"], config=config, now=now)
        eligible += int(ok)
        if ok != row["eligible"]:
            (gained if ok else lost).append(key)
    return dict(observados=len(data["records"]), elegibles=eligible, entran=sorted(gained), salen=sorted(lost))


def outcome_summary(data, change, horizon, followups, as_of):
    """Cierre bruto observado en los feeds. La ausencia de una recotización queda explícita."""
    start = datetime.fromisoformat(data["observed_at"])
    target = start + timedelta(days=horizon)
    end = target + timedelta(days=1)
    due = as_of >= end
    def price(value):
        try:
            n = float(value)
            return n if math.isfinite(n) and n > 0 else None
        except (TypeError, ValueError):
            return None
    closes = {}
    affected = set(change["entran"] + change["salen"])
    for followup in followups:
        instant = datetime.fromisoformat(followup["observed_at"])
        if not target <= instant <= min(end, as_of):
            continue
        for key in affected.intersection(followup["records"]):
            value = price(followup["records"][key]["token"].get("price"))
            if value and (key not in closes or instant < closes[key][0]):
                closes[key] = instant, value
    results = {}
    for name in ("entran", "salen"):
        returns, absent = [], 0
        for key in change[name]:
            opening = price(data["records"][key]["token"].get("price"))
            if due and opening and key in closes:
                returns.append(100 * (closes[key][1]/opening - 1))
            elif due:
                absent += 1
        results[name] = dict(afectados=len(change[name]), inmaduros=0 if due else len(change[name]),
                             sin_precio=absent, medidos=len(returns),
                             mediana_bruta_pct=median(returns) if returns else None,
                             caida_al_cierre_20pct=sum(r <= -20 + 1e-10 for r in returns))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--set", action="append", default=[], metavar="UMBRAL=VALOR")
    parser.add_argument("--days", type=int, choices=(7,30,90,180), help="Resultados observados para los contratos afectados")
    args = parser.parse_args()
    with get_conn() as conn:
        row = conn.execute("SELECT payload FROM shadow_captures WHERE run_id=?", (args.run_id,)).fetchone()
    if not row:
        parser.error("Esta corrida carece de universo congelado.")
    try:
        changes = dict(item.split("=", 1) for item in args.set)
        data = json.loads(row["payload"])
        result = compare(data, {k: float(v) for k,v in changes.items()})
        if args.days:
            target = datetime.fromisoformat(data["observed_at"]) + timedelta(days=args.days)
            with get_conn() as conn:
                rows = conn.execute("SELECT payload FROM shadow_captures WHERE observed_at BETWEEN ? AND ? "
                                    "AND status IN ('captured','complete') ORDER BY observed_at",
                                    (target.isoformat(), (target+timedelta(days=1)).isoformat())).fetchall()
            result["cierres_en_universo_observado"] = outcome_summary(data, result, args.days,
                (json.loads(row["payload"]) for row in rows), datetime.now(timezone.utc))
        print(json.dumps(result, indent=2))
    except ValueError as exc:
        parser.error(str(exc))
