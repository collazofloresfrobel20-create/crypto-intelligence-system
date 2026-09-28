"""Registro del consumo real de tokens por llamada a un LLM (Plan de correcciones, Fase A).
Hoy el free tier cuesta $0, pero solo se contaban llamadas (gemini_usage): sin tokens no se
puede proyectar cuánto costaría salir del free tier. Mejor esfuerzo -- un fallo aquí NUNCA debe
romper ni retrasar el análisis de un token."""
from datetime import datetime, timezone

from .db import get_conn


def record(provider: str, model: str | None, input_tokens, output_tokens) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO llm_usage (created_at, provider, model, input_tokens, output_tokens) VALUES (?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), provider, model,
                 int(input_tokens) if input_tokens is not None else None,
                 int(output_tokens) if output_tokens is not None else None),
            )
    except Exception:
        pass
