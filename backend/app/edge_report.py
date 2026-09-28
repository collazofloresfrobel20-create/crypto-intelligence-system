"""
Panel "Edge vs control" (Plan de correcciones, Fase A, 2026-09-28) -- puntos 3.3, 4.1 y 4.2.

Responde, con honestidad estadística, la pregunta que el acierto global no responde: ¿el
sistema rinde mejor que lo que habría rendido elegir tokens sin análisis, EN LAS MISMAS
CONDICIONES DE MERCADO? Reglas aprendidas midiendo el historial real:

  * El control es el grupo de descartados del MISMO ciclo (mismo momento de mercado). El acierto
    del control cambió de 21% a 43% entre semanas: comparar contra un promedio global engaña.
  * Un ciclo solo cuenta si tiene al menos MIN_CONTROLS_PER_RUN controles evaluados. Con 1 o 2
    el "control" es ruido (con esos ciclos salió un -18 pp falso que desapareció al excluirlos).
  * Las filas de un mismo token NO son observaciones independientes (164 filas eran 54 tokens):
    los intervalos se calculan remuestreando TOKENS, y siempre se muestra el n de tokens.
  * Tres etiquetas, porque "tocó +20%" premia la volatilidad (la volatilidad predice igual tocar
    +20% que caer 20%): toca +20%, cierra el horizonte >= +10%, y cayó >= 20% en algún momento.
  * Las acciones tokenizadas se excluyen (otra clase de activo: ~3% de acierto vs ~28%).

Se calcula en Python al final de cada ciclo y se guarda en analytics_snapshots; el Worker solo
lo lee. Todo mejor esfuerzo: si algo falla, el ciclo no se ve afectado.
"""
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

from .db import get_conn

MIN_CONTROLS_PER_RUN = 10
BOOT_ITERATIONS = 1000
HIT_PCT = 20.0
SUSTAINED_PCT = 10.0
DROP_PCT = -20.0

LABELS = {
    "touch20": "Tocó +20% en algún momento",
    "sustained10": "Cerró el horizonte en +10% o más",
    "drop20": "Cayó 20% o más en algún momento",
}


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _auc(y, s):
    """AUC por rangos con empates promediados (equivale a sklearn.roc_auc_score)."""
    pos = [v for yy, v in zip(y, s) if yy == 1]
    neg = [v for yy, v in zip(y, s) if yy == 0]
    if not pos or not neg:
        return float("nan")
    order = sorted(range(len(s)), key=lambda i: s[i])
    ranks = [0.0] * len(s)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    rank_sum_pos = sum(r for r, yy in zip(ranks, y) if yy == 1)
    return (rank_sum_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def _cluster_ci(items, stat, iterations=BOOT_ITERATIONS, seed=3):
    """IC95 por bootstrap remuestreando TOKENS enteros. items: lista de dicts con 'token'."""
    groups = defaultdict(list)
    for it in items:
        groups[it["token"]].append(it)
    glist = list(groups.values())
    if len(glist) < 3:
        return None, None
    rnd = random.Random(seed)
    vals = []
    for _ in range(iterations):
        sample = [it for g in (rnd.choice(glist) for _ in glist) for it in g]
        v = stat(sample)
        if v == v:  # descarta NaN
            vals.append(v)
    if len(vals) < 20:
        return None, None
    vals.sort()
    return vals[int(len(vals) * 0.025)], vals[int(len(vals) * 0.975) - 1]


def _r(x, nd=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, nd)


def _load_rows(stock_ids: set) -> list[dict]:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT run_id, category, symbol, alpha_id, verdict, created_at, max_return_pct,
                   return_pct, max_drawdown_pct, opportunity_score, confidence_score,
                   volume_24h, market_cap, data_completeness, market_regime, confidence_score_calibrated,
                   premortem_risk, verdict_v2
            FROM predictions WHERE status = 'evaluated' AND max_return_pct IS NOT NULL
            """
        ).fetchall()]
    out = []
    for r in rows:
        if r["alpha_id"] in stock_ids:
            continue
        r["token"] = r["alpha_id"] or r["symbol"]
        r["touch20"] = int(r["max_return_pct"] >= HIT_PCT)
        r["sustained10"] = int(r["return_pct"] is not None and r["return_pct"] >= SUSTAINED_PCT)
        r["drop20"] = int(r["max_drawdown_pct"] is not None and r["max_drawdown_pct"] <= DROP_PCT)
        out.append(r)
    return out


MIN_SAMPLE_ROWS = 20          # mismo umbral que backtesting.MIN_SAMPLE_FOR_CONFIDENCE
CONF_BUCKETS = [(0, 40), (40, 60), (60, 75), (75, 101)]
RELIABILITY_BINS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]


def _rates(rows: list[dict]) -> dict:
    """Tasa de cada desenlace con IC95 por token; sin IC si hay menos de 15 tokens distintos."""
    tokens = {r["token"] for r in rows}
    out = {"n_rows": len(rows), "n_tokens": len(tokens), "insufficient": len(rows) < MIN_SAMPLE_ROWS}
    for lab in LABELS:
        rate = _mean([r[lab] for r in rows]) if rows else None
        lo = hi = None
        if len(tokens) >= 15:
            lo, hi = _cluster_ci(rows, lambda s, lab=lab: _mean([x[lab] for x in s]), iterations=300)
        out[lab] = {"rate": _r(rate), "ci95": [_r(lo), _r(hi)]}
    return out


def _brier(ps: list[float], ys: list[int]) -> dict | None:
    if len(ps) < 10:
        return None
    b = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)
    base = sum(ys) / len(ys)
    b_ref = sum((base - y) ** 2 for y in ys) / len(ys)      # predecir siempre la tasa base
    return {"brier": _r(b, 4), "brier_reference_constant": _r(b_ref, 4),
            "skill": _r(1 - b / b_ref, 3) if b_ref > 0 else None, "n": len(ps)}


def _score_context(analyzed: list[dict]) -> dict:
    out = {"by_confidence": {}, "by_verdict": {}, "calibration": {}, "data_completeness": None}
    for lo, hi in CONF_BUCKETS:
        sub = [r for r in analyzed if r["confidence_score"] is not None and lo <= r["confidence_score"] < hi]
        out["by_confidence"][f"{lo}-{min(hi, 100)}"] = _rates(sub)
    for v in sorted({r["verdict"] for r in analyzed if r["verdict"]}):
        out["by_verdict"][v] = _rates([r for r in analyzed if r["verdict"] == v])
    for lab in ("touch20", "sustained10"):
        cal = {}
        for name, key, scale in (("confidence_score (crudo)", "confidence_score", 100.0),
                                 ("confidence_score calibrado", "confidence_score_calibrated", 100.0)):
            sub = [r for r in analyzed if r.get(key) is not None]
            ps = [min(1.0, max(0.0, r[key] / scale)) for r in sub]
            ys = [r[lab] for r in sub]
            rel = []
            for lo, hi in RELIABILITY_BINS:
                idx = [i for i, p in enumerate(ps) if lo <= p * 100 < hi]
                if idx:
                    rel.append({"bin": f"{lo}-{min(hi, 100)}", "n": len(idx),
                                "mean_predicted": _r(sum(ps[i] for i in idx) / len(idx)),
                                "observed_rate": _r(sum(ys[i] for i in idx) / len(idx))})
            cal[name] = {"brier": _brier(ps, ys), "reliability": rel}
        out["calibration"][lab] = cal
    v2rows = [r for r in analyzed if r.get("verdict_v2")]
    out["by_verdict_v2"] = {v: _rates([r for r in v2rows if r["verdict_v2"] == v])
                            for v in sorted({r["verdict_v2"] for r in v2rows})}
    both = [r for r in v2rows if r.get("verdict")]
    out["verdict_v2_agreement"] = ({"n": len(both), "agree_rate": _r(sum(1 for r in both if r["verdict"] == r["verdict_v2"]) / len(both))}
                                   if both else {"n": 0})
    pre = [r for r in analyzed if r.get("premortem_risk") is not None]
    if len(pre) >= 30:
        out["premortem"] = {"n": len(pre), "n_tokens": len({r["token"] for r in pre}),
                            "auc_vs_drop20": _r(_auc([r["drop20"] for r in pre], [r["premortem_risk"] for r in pre])),
                            "auc_vs_touch20": _r(_auc([r["touch20"] for r in pre], [r["premortem_risk"] for r in pre]))}
    else:
        out["premortem"] = {"n": len(pre), "note": "muestra insuficiente (se necesitan >= 30 análisis evaluados con pre-mortem; solo alimentará al Juez si su AUC contra la caída supera 0.5 con intervalo agrupado por token)"}
    comp = [r for r in analyzed if r.get("data_completeness") is not None]
    if len(comp) >= 30:
        out["data_completeness"] = {"n": len(comp), "auc_vs_touch20": _r(_auc([r["touch20"] for r in comp], [r["data_completeness"] for r in comp])),
                                    "auc_vs_sustained10": _r(_auc([r["sustained10"] for r in comp], [r["data_completeness"] for r in comp]))}
    else:
        out["data_completeness"] = {"n": len(comp), "note": "muestra insuficiente (se necesitan >= 30 análisis evaluados con calidad de datos registrada)"}
    return out


def _regime_trend(r):
    try:
        return (json.loads(r["market_regime"]) or {}).get("trend") if r.get("market_regime") else None
    except (TypeError, ValueError):
        return None


def compute(universe: list[dict] | None = None) -> dict:
    stocks_excluded = True
    if universe is None:
        try:
            from . import binance_alpha
            universe = binance_alpha.get_alpha_token_list()
        except Exception:
            universe, stocks_excluded = [], False
    stock_ids = {t.get("alphaId") for t in universe if t.get("stockState") or t.get("rwaInfo")}

    rows = _load_rows(stock_ids)
    analyzed = [r for r in rows if r["category"] == "analyzed"]
    control = [r for r in rows if r["category"] == "discarded"]

    by_run_control = defaultdict(list)
    for r in control:
        by_run_control[r["run_id"]].append(r)
    valid_runs = {rid for rid, v in by_run_control.items() if len(v) >= MIN_CONTROLS_PER_RUN}
    an_matched = [r for r in analyzed if r["run_id"] in valid_runs]

    labels_out = {}
    for lab, title in LABELS.items():
        diffs = [{"token": r["token"], "d": r[lab] - _mean([c[lab] for c in by_run_control[r["run_id"]]])}
                 for r in an_matched]
        edge = _mean([d["d"] for d in diffs]) if diffs else None
        lo, hi = _cluster_ci(diffs, lambda s: _mean([x["d"] for x in s])) if diffs else (None, None)
        labels_out[lab] = {
            "title": title,
            "analyzed_rate": _r(_mean([r[lab] for r in analyzed])) if analyzed else None,
            "control_rate": _r(_mean([r[lab] for r in control])) if control else None,
            "matched_edge": _r(edge), "matched_ci95": [_r(lo), _r(hi)],
            "matched_rows": len(diffs), "matched_tokens": len({d["token"] for d in diffs}),
            "distinguishable_from_zero": (lo is not None and (lo > 0 or hi < 0)),
        }

    # Potencia: cuántas predicciones independientes harían falta para ver un edge de 5 / 10 pp
    power = None
    touch_diffs = [r["touch20"] - _mean([c["touch20"] for c in by_run_control[r["run_id"]]]) for r in an_matched]
    if len(touch_diffs) > 5:
        m = _mean(touch_diffs)
        sd = math.sqrt(sum((x - m) ** 2 for x in touch_diffs) / (len(touch_diffs) - 1))
        need = {f"{int(d * 100)}pp": int(((1.96 + 0.84) * sd / d) ** 2) for d in (0.05, 0.10)}
        power = {"sd_of_matched_difference": _r(sd), "independent_predictions_needed": need,
                 "tokens_available_now": len({r["token"] for r in an_matched})}

    # Por veredicto (edge con la etiqueta 'toca +20%')
    by_verdict = {}
    for v in sorted({r["verdict"] for r in an_matched if r["verdict"]}):
        diffs = [{"token": r["token"], "d": r["touch20"] - _mean([c["touch20"] for c in by_run_control[r["run_id"]]])}
                 for r in an_matched if r["verdict"] == v]
        lo, hi = _cluster_ci(diffs, lambda s: _mean([x["d"] for x in s]))
        by_verdict[v] = {"edge": _r(_mean([d["d"] for d in diffs])), "ci95": [_r(lo), _r(hi)],
                         "rows": len(diffs), "tokens": len({d["token"] for d in diffs})}

    # Acierto por semana (evidencia del beta de mercado)
    weekly = defaultdict(lambda: {"control": [], "analyzed": []})
    for r in rows:
        wk = datetime.fromisoformat(r["created_at"]).strftime("%G-W%V")
        weekly[wk]["control" if r["category"] == "discarded" else "analyzed"].append(r["touch20"])
    weekly_out = {wk: {"control_rate": _r(_mean(v["control"])) if v["control"] else None, "control_n": len(v["control"]),
                       "analyzed_rate": _r(_mean(v["analyzed"])) if v["analyzed"] else None, "analyzed_n": len(v["analyzed"])}
                  for wk, v in sorted(weekly.items())}

    # Por régimen de mercado (solo filas con etiqueta; se irá llenando desde este ciclo)
    regime = defaultdict(lambda: {"control": [], "analyzed": []})
    for r in rows:
        t = _regime_trend(r)
        if t:
            regime[t]["control" if r["category"] == "discarded" else "analyzed"].append(r["touch20"])
    regime_out = {t: {"control_rate": _r(_mean(v["control"])) if v["control"] else None, "control_n": len(v["control"]),
                      "analyzed_rate": _r(_mean(v["analyzed"])) if v["analyzed"] else None, "analyzed_n": len(v["analyzed"])}
                  for t, v in regime.items()}

    # Baselines gratuitos sobre las mismas filas analizadas: ¿el Juez supera a vol/mcap?
    baselines = {}
    an_feat = [r for r in analyzed if r["volume_24h"] and r["market_cap"] and r["opportunity_score"] is not None]
    for lab in ("touch20", "sustained10"):
        feats = {
            "opportunity_score (Juez)": lambda r: r["opportunity_score"],
            "confidence_score (Juez)": lambda r: r["confidence_score"] or 0,
            "volumen / market cap (gratis)": lambda r: r["volume_24h"] / r["market_cap"],
        }
        baselines[lab] = {}
        for name, fn in feats.items():
            stat = lambda s, fn=fn, lab=lab: _auc([x[lab] for x in s], [fn(x) for x in s])
            lo, hi = _cluster_ci(an_feat, stat)
            baselines[lab][name] = {"auc": _r(stat(an_feat)), "ci95": [_r(lo), _r(hi)]}

    completeness_rows = [r for r in analyzed if r["data_completeness"] is not None]

    # Plan v2, B4/C2: contexto histórico de los scores (frecuencias naturales con n e IC por token)
    # y calibración medida (Brier + tabla de fiabilidad) del confidence_score del Juez.
    score_context = _score_context(analyzed)

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "stocks_excluded": stocks_excluded,
        "min_controls_per_run": MIN_CONTROLS_PER_RUN,
        "analyzed": {"rows": len(analyzed), "tokens": len({r["token"] for r in analyzed})},
        "control": {"rows": len(control), "tokens": len({r["token"] for r in control})},
        "matched": {"runs": len(valid_runs), "analyzed_rows": len(an_matched),
                    "analyzed_tokens": len({r["token"] for r in an_matched})},
        "labels": labels_out, "power": power, "by_verdict": by_verdict,
        "weekly": weekly_out, "by_regime": regime_out, "baselines_auc": baselines,
        "data_completeness_rows_evaluated": len(completeness_rows),
        "score_context": score_context,
    }


def compute_and_store(universe: list[dict] | None = None) -> dict:
    payload = compute(universe)
    with get_conn() as conn:
        conn.execute("INSERT INTO analytics_snapshots (kind, computed_at, payload) VALUES (?,?,?)",
                     ("edge_report", payload["computed_at"], json.dumps(payload, ensure_ascii=False)))
        conn.execute(
            "DELETE FROM analytics_snapshots WHERE kind = 'edge_report' AND id NOT IN "
            "(SELECT id FROM analytics_snapshots WHERE kind = 'edge_report' ORDER BY id DESC LIMIT 20)"
        )
    return payload
