"""
Cliente para Groq (API compatible con OpenAI, chat completions), usado en la Fase 2 como
segunda opinión independiente del Juez (agents/debate.py::judge_agent). Se eligió Groq porque
tiene un free tier permanente (sin necesidad de facturación), a diferencia del grounding de
Gemini que dejó de ser gratis.

A diferencia de gemini_client.py, Groq no tiene un response_schema nativo (decodificación
restringida por gramática) -- solo un modo JSON genérico (response_format={"type":
"json_object"}) que garantiza JSON válido pero no una forma específica. Por eso el shape
deseado se describe en el propio prompt, y aquí se valida que las claves requeridas estén
presentes antes de devolver el resultado, reintentando si no lo están.
"""
import json
import time

import requests

from .config import settings

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def generate_json(
    *,
    model: str,
    system_instruction: str,
    prompt: str,
    required_keys: list[str],
    max_retries: int = 3,
) -> dict:
    """Llama a Groq pidiendo un JSON con al menos las claves de required_keys. Reintenta ante
    JSON inválido o claves faltantes -- el modo JSON de Groq garantiza JSON válido, no una
    forma específica. Lanza la última excepción si se agotan los intentos; el caller
    (judge_agent) debe tratar esto como best-effort y no bloquear el ciclo si falla."""
    if not settings.GROQ_API_KEY:
        raise RuntimeError("Falta GROQ_API_KEY en backend/.env.")

    headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}", "Content-Type": "application/json"}
    schema_hint = (
        "\n\nResponde EXCLUSIVAMENTE con un objeto JSON válido (sin markdown, sin ```) que "
        f"incluya al menos estas claves: {', '.join(required_keys)}."
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt + schema_hint},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }

    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(GROQ_API_URL, headers=headers, json=body, timeout=30)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(text)
            missing = [k for k in required_keys if k not in data]
            if missing:
                raise ValueError(f"Groq devolvió JSON sin las claves requeridas: {missing}")
            return data
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            time.sleep(1.5 * (attempt + 1))
        except requests.RequestException as e:
            last_error = e
            msg = str(e).lower()
            if "429" in msg or "rate" in msg:
                time.sleep(8 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Groq generate_json falló tras {max_retries} intentos: {last_error}")
