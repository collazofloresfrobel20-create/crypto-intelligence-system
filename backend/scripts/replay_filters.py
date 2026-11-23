"""Reconstruye los filtros de una captura congelada sin llamadas al mercado."""
import argparse
import json
import math
import sys
from datetime import datetime
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--set", action="append", default=[], metavar="UMBRAL=VALOR")
    args = parser.parse_args()
    with get_conn() as conn:
        row = conn.execute("SELECT payload FROM shadow_captures WHERE run_id=?", (args.run_id,)).fetchone()
    if not row:
        parser.error("Esta corrida carece de universo congelado.")
    try:
        changes = dict(item.split("=", 1) for item in args.set)
        print(json.dumps(compare(json.loads(row["payload"]), {k: float(v) for k,v in changes.items()}), indent=2))
    except ValueError as exc:
        parser.error(str(exc))
