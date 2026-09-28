"""
verdict_v2 (Plan v2, C4, 2026-09-28): veredicto por REGLAS EXPLÍCITAS y deterministas, en SHADOW.

Por qué existe: el Juez LLM colapsó a dos etiquetas (212 análisis: 117 "Insufficient Evidence" y 95
"High Risk / Speculative"; cero Strong/Watchlist/Reject), así que su veredicto casi no informa y las
alertas de Telegram nunca han disparado. Aquí se traduce lo que sí está medido (scores del Juez, calidad
de datos por código, modelo de riesgo de caída) a un veredicto que usa las 5 categorías, y se compara
PAREADO contra el veredicto del Juez con los resultados reales.

Los umbrales son HIPÓTESIS de partida, fijadas antes de mirar resultados de verdict_v2 (ver la adenda
en PREREGISTRO.md) y no se ajustan mirando resultados. Es una función pura (sin LLM, sin red, sin
base de datos): mismas entradas, mismo veredicto. No cambia ningún veredicto mostrado ni disparado a
Telegram; reemplazar al Juez exige la compuerta prerregistrada (>= 60 días en shadow, >= 150 tokens
distintos y comparación pareada sin que resulte peor).
"""
RULES_VERSION = "v2.0"

MIN_COMPLETENESS = 0.5
REJECT_RISK_SCORE = 75
REJECT_PM_DROP20 = 0.6
STRONG_MIN_OPPORTUNITY = 70
STRONG_MAX_RISK = 45
STRONG_MIN_EARLINESS = 50
STRONG_MAX_PM_DROP20 = 0.4
WATCH_MIN_OPPORTUNITY = 55
WATCH_MAX_RISK = 60


def _n(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def compute(opportunity, risk, confidence_capped, earliness, data_quality: dict | None,
            pm_drop20, min_confidence_for_strong: int) -> str:
    """Primera regla que aplique, en este orden."""
    opp, rk, conf, earl, pm = _n(opportunity), _n(risk), _n(confidence_capped), _n(earliness), _n(pm_drop20)
    dq = data_quality or {}
    # 1. Sin datos suficientes para concluir: es un resultado tan válido como cualquier otro.
    if dq.get("critical_missing") or (dq.get("completeness") is not None and dq["completeness"] < MIN_COMPLETENESS):
        return "Insufficient Evidence"
    if None in (opp, rk):
        return "Insufficient Evidence"
    # 2. Riesgo dominante.
    if rk >= REJECT_RISK_SCORE or (pm is not None and pm >= REJECT_PM_DROP20):
        return "Reject"
    # 3. Configuración favorable con datos completos y riesgo de caída contenido.
    if (opp >= STRONG_MIN_OPPORTUNITY and rk <= STRONG_MAX_RISK
            and conf is not None and conf >= min_confidence_for_strong
            and earl is not None and earl >= STRONG_MIN_EARLINESS
            and (pm is None or pm < STRONG_MAX_PM_DROP20)):
        return "Strong Opportunity"
    # 4. Interesante pero sin cumplir todo lo anterior.
    if opp >= WATCH_MIN_OPPORTUNITY and rk <= WATCH_MAX_RISK:
        return "Watchlist"
    # 5. El resto: potencial dudoso con riesgo apreciable.
    return "High Risk / Speculative"
