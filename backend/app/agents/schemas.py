"""JSON schemas (formato aceptado por google-genai response_schema) para cada agente."""

# Única fuente de verdad de los 5 veredictos posibles -- reusado por JUDGE_SCHEMA más abajo y,
# fuera de este archivo, por dynamic_config.py (Fase 5, whitelist de TELEGRAM_NOTIFY_VERDICTS)
# y main.py (expuesto en /api/config para que el dashboard no lo hardcodee una segunda vez).
VERDICT_VALUES = [
    "Strong Opportunity",
    "Watchlist",
    "High Risk / Speculative",
    "Reject",
    "Insufficient Evidence",
]

ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "tier": {"type": "integer"},
                    "source": {"type": "string"},
                },
                "required": ["claim", "tier", "source"],
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "findings", "evidence", "uncertainties"],
}

DEBATE_SCHEMA = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string"},
        "arguments": {"type": "array", "items": {"type": "string"}},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["thesis", "arguments", "supporting_evidence"],
}

MEDIATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "supported_arguments": {"type": "array", "items": {"type": "string"}},
        "speculative_arguments": {"type": "array", "items": {"type": "string"}},
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "surviving_conclusion": {"type": "string"},
    },
    "required": [
        "supported_arguments",
        "speculative_arguments",
        "contradictions",
        "missing_information",
        "surviving_conclusion",
    ],
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunity_score": {"type": "integer"},
        "risk_score": {"type": "integer"},
        "confidence_score": {"type": "integer"},
        "earliness_score": {"type": "integer"},
        "evidence_tier": {"type": "string"},
        "verdict": {
            "type": "string",
            "enum": VERDICT_VALUES,
        },
        "bull_case_summary": {"type": "string"},
        "bear_case_summary": {"type": "string"},
        "key_evidence": {"type": "array", "items": {"type": "string"}},
        "main_risks": {"type": "array", "items": {"type": "string"}},
        "system_note": {"type": "string"},
        "project_explainer": {"type": "string"},
    },
    "required": [
        "opportunity_score",
        "risk_score",
        "confidence_score",
        "earliness_score",
        "evidence_tier",
        "verdict",
        "bull_case_summary",
        "bear_case_summary",
        "key_evidence",
        "main_risks",
        "system_note",
        "project_explainer",
    ],
}

DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "patterns_found": {"type": "array", "items": {"type": "string"}},
        "proposed_adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "current_value": {"type": "string"},
                    "proposed_value": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["parameter", "current_value", "proposed_value", "reason"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["patterns_found", "proposed_adjustments", "summary"],
}
