"""Estudio prospectivo de rotación y pérdida al cierre de siete días."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import random

# Inicio posterior a las pruebas manuales de instalación. No incluir datos previos.
START = datetime(2026, 11, 24, tzinfo=timezone.utc)
HORIZON = timedelta(days=7)
WINDOW = timedelta(days=1)
END = START + timedelta(days=60)
FINAL = END + HORIZON + WINDOW


def positive(value):
    try:
        x = float(value)
        return x if x > 0 and math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def observations(entries, prices, now):
    by_key = defaultdict(list)
    for entry in entries:
        if entry["run_id"].startswith("shadow-"):
            by_key[entry["token_key"]].append(dict(entry))
    closes = {row["token_key"]: dict(row) for row in prices if row["days"] == 7}
    samples, missing, immature, no_signal = [], 0, 0, 0
    for key, group in by_key.items():
        group.sort(key=lambda row: (row["observed_at"], row["run_id"]))
        first = next((row for row in group if row["eligible"]), None)
        if not first:
            continue
        stamp = datetime.fromisoformat(first["observed_at"])
        if not START <= stamp < END or stamp > now:
            continue
        depth = positive(first.get("liquidity"))
        vol = positive(first.get("volume_24h"))
        if not depth or vol is None:
            no_signal += 1
            continue
        target = stamp + HORIZON
        if target + WINDOW > now:
            immature += 1
            continue
        available = group + ([closes[key]] if key in closes else [])
        available = [row for row in available if target <= datetime.fromisoformat(row["observed_at"]) <= target+WINDOW
                     and positive(row["price"]) is not None]
        opening = positive(first.get("price"))
        if not available or not opening:
            missing += 1
        end = min(available, key=lambda row: row["observed_at"]) if available else None
        loss = (float(end["price"]) <= opening*0.8) if end and opening else None
        stratum = (key.split(":",1)[0], first.get("config_hash"), bisect_right((100_000, 1_000_000), depth))
        week = (stamp - START).days // 7
        samples.append((stratum, week, vol/depth, loss))
    return samples, missing, immature, no_signal


def strata_metrics(samples, stress=False):
    groups = defaultdict(list)
    for stratum, _, score, loss in samples:
        groups[stratum].append((score, loss))
    total_weight = high_events = low_events = ties_adjusted = pairs = 0.
    for group in groups.values():
        scores = sorted(score for score, _ in group)
        size = len(scores)//5
        if not size:
            continue
        low, high = scores[size-1], scores[-size]
        if low >= high:
            continue
        # El escenario adverso asigna los cierres desconocidos contra la hipótesis.
        hi = [int(loss) if loss is not None else 0 for score,loss in group if score>=high and (stress or loss is not None)]
        lo = [int(loss) if loss is not None else 1 for score,loss in group if score<=low and (stress or loss is not None)]
        if not hi or not lo:
            continue
        weight = min(len(hi),len(lo))
        total_weight += weight
        high_events += weight*sum(hi)/len(hi)
        low_events += weight*sum(lo)/len(lo)
        negatives = sorted(x for x,lost in group if lost is False)
        for value, lost in group:
            if lost is True:
                ties_adjusted += (bisect_left(negatives,value) + bisect_right(negatives,value))/2
                pairs += len(negatives)
    return (high_events/low_events if total_weight and low_events else None,
            ties_adjusted/pairs if pairs else None)


def evaluate(entries, prices, now):
    now = min(now, FINAL)
    samples, missing, immature, no_signal = observations(entries, prices, now)
    weeks = defaultdict(list)
    for row in samples:
        weeks[row[1]].append(row)
    measured = len(samples)-missing
    result = dict(inicio=START.date().isoformat(), cierre=FINAL.date().isoformat(), dias=min(60,max(0,(now-START).days)), medidos=measured,
                  sin_cierre=missing, inmaduros=immature, sin_senal=no_signal, semanas=len(weeks),
                  estado="en_observacion", rr=None, ic95_rr=None, auc=None, rr_estres=None)
    if now < FINAL:
        return result
    rr, auc = strata_metrics(samples)
    stress, _ = strata_metrics(samples, stress=True)
    result.update(rr=rr, auc=auc, rr_estres=stress, estado="evidencia_insuficiente")
    if rr is None or auc is None or len(weeks)<8:
        return result
    rng = random.Random(20261124)
    blocks = list(weeks.values())
    estimates = []
    for _ in range(500):
        drawn = [s for _ in blocks for s in rng.choice(blocks)]
        rep, _ = strata_metrics(drawn)
        if rep is not None:
            estimates.append(rep)
    if len(estimates)<490:
        return result
    estimates.sort()
    ci = [estimates[int((len(estimates)-1)*q)] for q in (.025,.975)]
    passed = rr>=1.5 and ci[0]>1 and auc>=.58 and stress is not None and stress>=1.5
    result.update(ic95_rr=ci, estado="candidata_a_revision" if passed else "solo_descriptiva")
    return result
