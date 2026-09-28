"""
Contexto de movimiento (Plan v2, B3, 2026-09-28): bloque DESCRIPTIVO, calculado por código (sin LLM),
que acompaña a cada análisis. Sustituye a la "sección 16" de niveles técnicos de la crítica externa:
no hay zonas de entrada, objetivos ni niveles de invalidación (un LLM flash-lite inventaría cifras con
apariencia de precisión, y en los datos reales la posición en el rango de 7 días no predice: AUC 0.536,
IC que incluye 0.5). En su lugar:

  1. las métricas duras de los 7 días previos (volatilidad horaria, momentum, drawdown máximo,
     distancia a máximo/mínimo), y
  2. lo que HISTÓRICAMENTE pasó con tokens de volatilidad parecida, en frecuencias naturales
     ("de cada 100 ..."), con tamaño de muestra y margen de error calculado por token.

La volatilidad es la feature con más poder en el historial, pero predice IGUAL subir que caer, por
eso los tres desenlaces se muestran juntos y sin interpretación direccional. Es contexto de
investigación, no una recomendación ni una predicción.
"""
import json

from .db import get_conn
from . import price_models

MIN_TOKENS_FOR_COHORTS = 60      # menos tokens con resultado y no se muestran tasas base
MIN_TOKENS_PER_COHORT = 15
BOOT_ITERATIONS = 300
OUTCOMES = {
    "touch20": "tocaron +20% en algún momento",
    "sustained10": "cerraron los 7 días en +10% o más",
    "drop20": "cayeron 20% o más en algún momento",
}
DISCLAIMER = ("Contexto descriptivo de investigación calculado a partir de datos históricos de este "
              "mismo sistema. No es una recomendación de inversión ni una predicción; los desenlaces "
              "pasados no garantizan resultados y la muestra es pequeña.")

_COHORTS: dict | None = None


def reset_cache():
    global _COHORTS
    _COHORTS = None


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _quartile_cuts(values: list[float]) -> list[float]:
    v = sorted(values)
    n = len(v)
    return [v[int(n * 0.25)], v[int(n * 0.5)], v[int(n * 0.75)]]


def _quartile_of(x: float, cuts: list[float]) -> int:
    return 1 + sum(1 for c in cuts if x > c)


def load_cohorts() -> dict | None:
    """Cohortes por cuartil de volatilidad horaria sobre TODO el historial evaluado con features
    (analizados + control): tasa de cada desenlace con IC95 remuestreando tokens. Una vez por
    proceso (un ciclo)."""
    global _COHORTS
    if _COHORTS is not None:
        return _COHORTS or None
    from .edge_report import _cluster_ci
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT alpha_id, symbol, vol_hourly_pct, max_return_pct, return_pct, max_drawdown_pct "
            "FROM predictions WHERE status = 'evaluated' AND vol_hourly_pct IS NOT NULL"
        ).fetchall()]
    for r in rows:
        r["token"] = r["alpha_id"] or r["symbol"]
    if len({r["token"] for r in rows}) < MIN_TOKENS_FOR_COHORTS:
        _COHORTS = {}
        return None
    cuts = _quartile_cuts([r["vol_hourly_pct"] for r in rows])
    out = {"cuts": cuts, "quartiles": {}}
    for q in (1, 2, 3, 4):
        sub = [r for r in rows if _quartile_of(r["vol_hourly_pct"], cuts) == q]
        tokens = {r["token"] for r in sub}
        cell = {"n_rows": len(sub), "n_tokens": len(tokens), "outcomes": {}}
        for label in OUTCOMES:
            items = [{"token": r["token"], "y": price_models.label_of(r, label)} for r in sub]
            items = [i for i in items if i["y"] is not None]
            if not items:
                continue
            rate = _mean([i["y"] for i in items])
            lo, hi = (None, None)
            if len(tokens) >= MIN_TOKENS_PER_COHORT:
                lo, hi = _cluster_ci(items, lambda s: _mean([i["y"] for i in s]), iterations=BOOT_ITERATIONS)
            cell["outcomes"][label] = {"rate": round(rate, 3),
                                       "ci95": [None if lo is None else round(lo, 3), None if hi is None else round(hi, 3)]}
        out["quartiles"][q] = cell
    _COHORTS = out
    return out


def build(features: dict) -> dict:
    """features: salida de market_stats.price_features(). Siempre devuelve un dict (con 'available':
    False si el token no tiene klines suficientes): nunca se inventan métricas."""
    if not features or features.get("vol_hourly_pct") is None:
        return {"available": False,
                "text": ["Sin datos de precio suficientes (menos de ~4 días de historial horario o sin klines): "
                         "no se calcula el contexto de movimiento."],
                "disclaimer": DISCLAIMER}
    metrics = {k: features.get(k) for k in ("vol_hourly_pct", "momentum_7d_pct", "max_drawdown_7d_pct",
                                              "dist_from_high_pct", "dist_from_low_pct", "range_pos_7d")}
    text = [
        f"Volatilidad horaria de los últimos 7 días: {metrics['vol_hourly_pct']:.2f}%. "
        f"Variación en 7 días: {metrics['momentum_7d_pct']:+.1f}%. "
        f"Caída máxima desde un pico dentro de esos 7 días: {metrics['max_drawdown_7d_pct']:.1f}%.",
        f"Precio actual a {metrics['dist_from_low_pct']:.1f}% sobre el mínimo y {metrics['dist_from_high_pct']:.1f}% "
        "bajo el máximo de 7 días.",
    ]
    out = {"available": True, "metrics": metrics, "text": text, "disclaimer": DISCLAIMER}
    cohorts = load_cohorts()
    if not cohorts:
        text.append("Aún no hay suficientes tokens con resultado para mostrar tasas históricas de referencia.")
        return out
    q = _quartile_of(metrics["vol_hourly_pct"], cohorts["cuts"])
    cell = cohorts["quartiles"][q]
    out["cohort"] = {"volatility_quartile": q, **cell}
    lines = []
    for label, desc in OUTCOMES.items():
        o = cell["outcomes"].get(label)
        if not o:
            continue
        lo, hi = o["ci95"]
        margin = f" (entre {round(lo * 100)} y {round(hi * 100)})" if lo is not None else ""
        lines.append(f"~{round(o['rate'] * 100)}{margin} {desc}")
    text.append(f"De cada 100 tokens con volatilidad parecida (cuartil {q} de 4; {cell['n_tokens']} tokens distintos, "
                f"{cell['n_rows']} observaciones): " + "; ".join(lines) + ".")
    if q == 4:
        text.append("Volatilidad alta: el historial muestra que estos tokens suben y caen con frecuencias altas al mismo tiempo.")
    return out


def build_json(features: dict) -> str:
    return json.dumps(build(features), ensure_ascii=False)
