import json

from ..config import settings
from ..gemini_client import generate_json
from .. import groq_client
from .schemas import DEBATE_SCHEMA, MEDIATOR_SCHEMA, JUDGE_SCHEMA, DIAGNOSIS_SCHEMA

_JSON_RULE = "Responde siempre en español. Devuelve solo el JSON pedido, sin texto adicional."

# Fase 2 (2026-09-24): lo mínimo que le pedimos a Groq para poder comparar su veredicto contra
# el de Gemini -- no le pedimos el resto del JUDGE_SCHEMA (bull_case_summary, key_evidence,
# etc.), eso ya lo tiene Gemini y no aporta nada tener una segunda versión de la misma prosa.
_GROQ_JUDGE_KEYS = ["opportunity_score", "risk_score", "confidence_score", "earliness_score", "verdict"]


def bull_agent(token_symbol: str, findings_json: str) -> dict:
    return generate_json(
        model=settings.GEMINI_MODEL_SMART,
        system_instruction=(
            "Eres el Bull Agent. Construye la tesis MÁS FUERTE Y HONESTA posible a favor de "
            f"{token_symbol}, basada exclusivamente en los hallazgos de los analistas que se te "
            "dan como contexto (no inventes datos nuevos). " + _JSON_RULE
        ),
        prompt=f"Hallazgos de los analistas (JSON):\n{findings_json}",
        response_schema=DEBATE_SCHEMA,
    )


def bear_agent(token_symbol: str, findings_json: str, bull_case_json: str) -> dict:
    return generate_json(
        model=settings.GEMINI_MODEL_SMART,
        system_instruction=(
            "Eres el Bear Agent. Tu trabajo NO es solo buscar algo malo: debes intentar "
            f"DESTRUIR LA LÓGICA del Bull Case de {token_symbol} usando los hallazgos de los "
            "analistas. Señala qué argumentos del bull case son especulación, qué evidencia "
            "contradictoria existe, y qué riesgos se están subestimando. " + _JSON_RULE
        ),
        prompt=(
            f"Hallazgos de los analistas (JSON):\n{findings_json}\n\n"
            f"Bull Case a refutar (JSON):\n{bull_case_json}"
        ),
        response_schema=DEBATE_SCHEMA,
    )


def mediator_agent(token_symbol: str, findings_json: str, bull_json: str, bear_json: str) -> dict:
    return generate_json(
        model=settings.GEMINI_MODEL_SMART,
        system_instruction=(
            f"Eres el Mediador para {token_symbol}. Recibes evidencia original + Bull Case + "
            "Bear Case. Responde: ¿qué argumentos están realmente respaldados por evidencia? "
            "¿cuáles son especulación? ¿hay datos contradictorios? ¿qué información falta? "
            "¿qué conclusión sobrevive al debate? Sé imparcial, no favorezcas al bull ni al "
            "bear por defecto. " + _JSON_RULE
        ),
        prompt=(
            f"Evidencia original (JSON):\n{findings_json}\n\n"
            f"Bull Case (JSON):\n{bull_json}\n\nBear Case (JSON):\n{bear_json}"
        ),
        response_schema=MEDIATOR_SCHEMA,
    )


def _groq_second_opinion(token_symbol: str, findings_json: str, bull_json: str, bear_json: str, mediator_json: str) -> dict | None:
    """Fase 2: segunda opinión independiente vía Groq/Llama, best-effort -- si falla (sin key,
    error de red, rate limit, JSON inválido tras reintentos), devuelve None y el caller sigue
    con solo el veredicto de Gemini. Nunca debe bloquear el ciclo por una caída de un tercero."""
    try:
        return groq_client.generate_json(
            model=settings.GROQ_MODEL,
            system_instruction=(
                f"Eres un juez independiente evaluando la oportunidad de inversión temprana "
                f"{token_symbol}. Evalúas de forma independiente 4 métricas 0-100 (opportunity_score, "
                "risk_score, confidence_score, earliness_score) y emites un verdict de estos 5 "
                "posibles exactamente: 'Strong Opportunity', 'Watchlist', 'High Risk / Speculative', "
                "'Reject', 'Insufficient Evidence'. Regla dura: si confidence_score < "
                f"{settings.MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY}, verdict NO puede ser "
                "'Strong Opportunity'. Basa tu evaluación solo en la evidencia que se te da, no "
                "inventes datos. Responde siempre en español."
            ),
            prompt=(
                f"Evidencia original de los analistas (JSON):\n{findings_json}\n\n"
                f"Bull Case (JSON):\n{bull_json}\n\nBear Case (JSON):\n{bear_json}\n\n"
                f"Conclusión del Mediador (JSON):\n{mediator_json}"
            ),
            required_keys=_GROQ_JUDGE_KEYS,
        )
    except Exception:
        return None


def judge_agent(
    token_symbol: str,
    findings_json: str,
    bull_json: str,
    bear_json: str,
    mediator_json: str,
) -> dict:
    """Además del veredicto de Gemini (siempre), intenta una segunda opinión independiente vía
    Groq (Fase 2, 2026-09-24) -- se adjunta como secondary_judge_* / judge_agreement, pero NO
    se aplica ninguna regla sobre el veredicto final aquí (eso es una decisión de
    ENSEMBLE_JUDGE_MODE que vive en pipeline.py, mismo patrón que ML_SCORING_MODE de la Fase 1
    -- este agente se queda "puro": solo llama LLMs, no decide política de negocio)."""
    result = generate_json(
        model=settings.GEMINI_MODEL_SMART,
        system_instruction=(
            f"Eres el Juez final para {token_symbol}. NO preguntas '¿me gusta este token?'. "
            "Evalúas de forma independiente 4 métricas (Opportunity, Risk, Confidence, "
            "Earliness son INDEPENDIENTES entre sí — un token puede tener Opportunity alto y "
            "Confidence bajo al mismo tiempo, eso es válido y debe reflejarse) más la calidad "
            "de evidencia predominante (Tier). Con eso emites un veredicto de estos 5 posibles: "
            "'Strong Opportunity' (configuración favorable con evidencia sólida), 'Watchlist' "
            "(interesante pero falta confirmación), 'High Risk / Speculative' (potencial alto "
            "pero riesgo elevado), 'Reject' (no cumple criterios mínimos), o 'Insufficient "
            "Evidence' (no hay información suficiente para concluir — este veredicto es TAN "
            "VÁLIDO como Strong Opportunity, úsalo cuando corresponda). Regla dura: si "
            f"confidence_score < {settings.MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY}, el veredicto "
            "NO puede ser 'Strong Opportunity'. "
            "Además, redacta 'project_explainer': 1-2 oraciones en español simple, SIN jerga "
            "técnica ni de cripto, que expliquen qué ES este proyecto y qué problema dice "
            "resolver -- como se lo explicarías a alguien que nunca ha usado cripto. Basado en "
            "los hallazgos de los analistas, no en tu opinión. Esto es pura descripción neutral "
            "(qué es), no una evaluación de si es bueno o malo -- eso ya lo cubren el veredicto "
            "y los demás campos. Si los analistas no encontraron información suficiente para "
            "explicar qué hace el proyecto, dilo explícitamente en vez de inventar. "
            "Horizonte objetivo: ~7 días, movimiento buscado +20-30% (nunca prometido). "
            + _JSON_RULE
        ),
        prompt=(
            f"Evidencia original de los analistas (JSON):\n{findings_json}\n\n"
            f"Bull Case (JSON):\n{bull_json}\n\nBear Case (JSON):\n{bear_json}\n\n"
            f"Conclusión del Mediador (JSON):\n{mediator_json}"
        ),
        response_schema=JUDGE_SCHEMA,
    )

    groq_result = _groq_second_opinion(token_symbol, findings_json, bull_json, bear_json, mediator_json)
    if groq_result is None:
        result["secondary_judge_verdict"] = None
        result["secondary_judge_opportunity_score"] = None
        result["secondary_judge_risk_score"] = None
        result["secondary_judge_confidence_score"] = None
        result["secondary_judge_earliness_score"] = None
        result["judge_agreement"] = None
    else:
        result["secondary_judge_verdict"] = groq_result.get("verdict")
        result["secondary_judge_opportunity_score"] = groq_result.get("opportunity_score")
        result["secondary_judge_risk_score"] = groq_result.get("risk_score")
        result["secondary_judge_confidence_score"] = groq_result.get("confidence_score")
        result["secondary_judge_earliness_score"] = groq_result.get("earliness_score")
        result["judge_agreement"] = int(groq_result.get("verdict") == result.get("verdict"))
    return result


def diagnose_system(summary_json: str) -> dict:
    """Auto-corrección continua (spec sección 9.3), corre cada vez que hay suficiente evidencia
    evaluada nueva -tanto de tokens analizados como de descartados- en vez de estar atada a un
    piloto único de 7 días."""
    return generate_json(
        model=settings.GEMINI_MODEL_SMART,
        system_instruction=(
            "Eres el módulo de auto-corrección continua del sistema. Se te da un resumen JSON "
            "con: (a) predicciones que pasaron los filtros y fueron analizadas por los agentes, "
            "con sus scores y resultado real; y (b) tokens DESCARTADOS por los hard filters "
            "(sin análisis LLM) junto con la razón exacta de rechazo y qué les pasó realmente "
            "en precio. Diagnostica patrones de error en AMBOS lados: sobre los analizados "
            "(ej. 'sobreestimé señales sociales', 'el rango de confidence <60 nunca cumplió la "
            "tesis'), y sobre los descartados (ej. 'muchos tokens rechazados solo por "
            "MIN_LIQUIDITY_USD sí alcanzaron +20%, el umbral está demasiado alto y estamos "
            "perdiendo oportunidades reales'). Propón ajustes concretos de parámetros/umbrales "
            "(nombra el parámetro exacto: MIN_LIQUIDITY_USD, MIN_VOLUME_24H_USD, "
            "MAX_MARKET_CAP_USD, MIN_HOLDERS, MAX_LISTING_AGE_DAYS, o "
            "MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY -- el corte de confidence_score por debajo "
            f"del cual el Juez no puede emitir 'Strong Opportunity', hoy en "
            f"{settings.MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY}). No inventes causas sin "
            "respaldo en los datos que se te dan; si la evidencia es insuficiente para un "
            "diagnóstico confiable, dilo en vez de forzar una conclusión. " + _JSON_RULE
        ),
        prompt=f"Resumen de evaluaciones recientes (JSON):\n{summary_json}",
        response_schema=DIAGNOSIS_SCHEMA,
    )
