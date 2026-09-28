"""
Cliente de mejor esfuerzo para Jev (TypeSafe), vía HTTP directo (POST /v1/systemone, ver
docs.typesafe.ai/api) -- sin el SDK, para no sumar una dependencia cuya forma no se pudo probar
con una key real todavía. Mismo contrato que groq_client.py: cualquier fallo -> None, nunca rompe
el ciclo. Una sola solicitud con TODAS las preguntas del objeto (fan-out: la doc mide mucho menos
costo y latencia que preguntar por separado).
"""
import time

import requests

from .config import settings

API_URL = "https://api.typesafe.ai/v1/systemone"


def system_one(state, questions: dict, max_retries: int = 4) -> dict | None:
    """Devuelve {'model': ..., 'answers': {...}, 'usage': {...}} o None si no hay key o falla.
    Reintenta 429/529 con backoff (lo que la doc pide para esos códigos); 401/422 no se reintentan."""
    if not settings.TYPESAFE_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {settings.TYPESAFE_API_KEY}", "Content-Type": "application/json"}
    body = {"state": state, "model": settings.JEV_MODEL, "questions": questions}
    for attempt in range(max_retries):
        try:
            resp = requests.post(API_URL, headers=headers, json=body, timeout=30)
            if resp.status_code in (429, 529):
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if "answers" in data else None
        except (requests.RequestException, ValueError):
            time.sleep(1.5 * (attempt + 1))
    return None
