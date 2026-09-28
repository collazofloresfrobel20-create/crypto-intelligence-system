"""
Banco de comparación de la Fase 1 (PROPUESTA_JEV_CIS.md §4.6 y §5) -- SOLO LECTURA.

Une las etiquetas de Jev (archivo local de jev_backfill.py) con los resultados reales ya
evaluados en la base (SELECT de columnas mínimas, no escribe nada) y mide, con bootstrap AGRUPADO
POR TOKEN (las filas de un mismo token no son observaciones independientes):

  1. AUC de cada feature de Jev por sí sola contra "tocó +20% en 7 días", con IC95 y con IC
     ajustado por comparaciones múltiples (Bonferroni: son ~14 features, mirar todas y quedarse
     con la mejor garantiza falsos descubrimientos).
  2. Modelo anidado: logística con solo volumen/market cap vs. la misma + features de Jev, en 200
     particiones aleatorias AGRUPADAS por token (70/30, para que un token nunca esté en train y
     test a la vez -- las etiquetas de Jev son estáticas por token y de otro modo el modelo
     podría memorizar tokens). Criterio de éxito fijado de antemano: límite inferior del IC de
     dAUC > 0 Y dAUC medio >= +0.03.

Excluye acciones tokenizadas (otra clase de activo: ~3% de acierto contra ~28%).

Uso (desde backend/):  python scripts/compare_signals.py [--mock]
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_conn
from app.jev_questions import TAGS_V1, NARRATIVE_OPTIONS

OUT_DIR = Path(__file__).resolve().parent.parent / "jev_out"
HIT = 20.0
DELTA_MIN = 0.03


def _auc(y, s):
    y = np.asarray(y); s = np.asarray(s, dtype=float)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    # rangos con empates promediados
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    r = np.empty(len(allv)); r[order] = np.arange(1, len(allv) + 1)
    for v in np.unique(allv):
        m = allv == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return (r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def load(mock: bool):
    tags = json.loads((OUT_DIR / (f"{TAGS_V1}.mock.json" if mock else f"{TAGS_V1}.json")).read_text(encoding="utf-8"))
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT alpha_id, symbol, created_at, max_return_pct, volume_24h, market_cap "
            "FROM predictions WHERE status='evaluated'"
        ).fetchall()]
    feats, data = {}, []
    for r in rows:
        t = tags.get(r["alpha_id"])
        if not t or t["is_stock"] or not r["market_cap"] or not r["volume_24h"] or r["max_return_pct"] is None:
            continue
        a = t["answers"]
        f = {
            "p_impersonates": a["impersonates_known"]["noul"], "p_promises": a["promises_returns"]["noul"],
            "p_wrapper": a["is_wrapper"]["noul"], "p_joke": a["joke_or_shitpost_name"]["noul"],
        }
        for k in NARRATIVE_OPTIONS:
            f["narr_" + k] = a["narrative"]["probabilities"].get(k, 0.0)
        data.append({"token": r["alpha_id"], "y": int(r["max_return_pct"] >= HIT),
                     "base": math.log10(r["volume_24h"] / r["market_cap"] + 1e-9), "f": f})
    return data


def boot_ci(data, stat, n=1000, alpha=0.05, seed=1):
    by = {}
    for d in data:
        by.setdefault(d["token"], []).append(d)
    groups = list(by.values())
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        s = [d for g in (rnd.choice(groups) for _ in groups) for d in g]
        v = stat(s)
        if not math.isnan(v):
            vals.append(v)
    vals.sort()
    return vals[int(len(vals) * alpha / 2)], vals[int(len(vals) * (1 - alpha / 2)) - 1]


def nested(data, splits=200, seed=7):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    tokens = sorted({d["token"] for d in data})
    keys = list(data[0]["f"].keys())
    rnd = random.Random(seed)
    deltas = []
    for _ in range(splits):
        test_tok = set(rnd.sample(tokens, max(1, int(len(tokens) * 0.3))))
        tr = [d for d in data if d["token"] not in test_tok]; te = [d for d in data if d["token"] in test_tok]
        ytr = [d["y"] for d in tr]; yte = [d["y"] for d in te]
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        def fit(cols):
            X = lambda ds: [[d["base"]] + [d["f"][k] for k in cols] for d in ds]
            m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=0.3)).fit(X(tr), ytr)
            return _auc(yte, m.predict_proba(X(te))[:, 1])
        deltas.append(fit(keys) - fit([]))
    deltas.sort()
    return float(np.mean(deltas)), deltas[int(len(deltas) * 0.025)], deltas[int(len(deltas) * 0.975) - 1], len(deltas)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--mock", action="store_true"); args = ap.parse_args()
    data = load(args.mock)
    n_tok = len({d["token"] for d in data})
    print(f"Filas evaluadas con etiqueta Jev (sin acciones): {len(data)} | tokens distintos: {n_tok} | acierto: {np.mean([d['y'] for d in data]):.3f}")
    print(f"AUC solo vol/mcap: {_auc([d['y'] for d in data], [d['base'] for d in data]):.3f}\n")
    keys = list(data[0]["f"].keys())
    bonf = 0.05 / len(keys)
    print(f"{'feature':38s} {'AUC':>6s}  {'IC95':>15s}  {'IC Bonferroni':>15s}")
    for k in keys:
        stat = lambda ds, k=k: _auc([d["y"] for d in ds], [d["f"][k] for d in ds])
        a = stat(data); lo, hi = boot_ci(data, stat); blo, bhi = boot_ci(data, stat, alpha=bonf)
        sig = "  <-- fuera de 0.5 tras Bonferroni" if (blo > 0.5 or bhi < 0.5) else ""
        print(f"{k:38s} {a:6.3f}  [{lo:.3f},{hi:.3f}]  [{blo:.3f},{bhi:.3f}]{sig}")
    mean, lo, hi, n = nested(data)
    ok = lo > 0 and mean >= DELTA_MIN
    print(f"\nModelo anidado (vol/mcap + Jev) - (vol/mcap): dAUC = {mean:+.3f}  IC95 [{lo:+.3f},{hi:+.3f}]  ({n} particiones por token)")
    print(f"Criterio pre-registrado (IC inferior > 0 y dAUC >= +{DELTA_MIN}): {'CUMPLE' if ok else 'NO CUMPLE'}")
    if args.mock:
        print("\n*** DATOS MOCK: esto solo prueba la tubería, no dice nada de Jev. ***")


if __name__ == "__main__":
    main()
