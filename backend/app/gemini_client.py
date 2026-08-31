"""
Wrapper sobre google-genai con:
  - Una sola API key compartida por todo el proyecto (ver config.py para el porqué).
  - Throttling de RPM/RPD para respetar el tier gratuito (los límites son por key,
    no por "agente", así que serializar es obligatorio si se usa una sola key).
  - Reintentos con backoff ante 429 (rate limit) / errores transitorios.
  - Modo JSON estructurado (response_schema) para que los agentes devuelvan datos
    parseables en vez de texto libre.
  - Google Search grounding opcional, para que los analistas puedan citar
    evidencia externa real en vez de solo "conocimiento" del modelo.
"""
import json
import time
import threading
from datetime import date

from google import genai
from google.genai import types

from .config import settings
from .db import get_conn

_client: genai.Client | None = None
_lock = threading.Lock()
_last_call_ts: list[float] = []


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "Falta GEMINI_API_KEY en backend/.env. Consigue una key gratuita en "
                "https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _check_daily_quota():
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT count FROM gemini_usage WHERE day = ?", (today,)).fetchone()
        count = row["count"] if row else 0
        if count >= settings.GEMINI_MAX_RPD:
            raise RuntimeError(
                f"Límite diario del tier gratuito de Gemini alcanzado ({settings.GEMINI_MAX_RPD} "
                "llamadas). Intenta de nuevo mañana o sube GEMINI_MAX_RPD si tienes un tier de pago."
            )
        if row:
            conn.execute("UPDATE gemini_usage SET count = count + 1 WHERE day = ?", (today,))
        else:
            conn.execute("INSERT INTO gemini_usage (day, count) VALUES (?, 1)", (today,))


def _throttle():
    """Serializa llamadas para no exceder GEMINI_MAX_RPM (todas comparten la misma key)."""
    with _lock:
        now = time.time()
        window_start = now - 60
        while _last_call_ts and _last_call_ts[0] < window_start:
            _last_call_ts.pop(0)
        if len(_last_call_ts) >= settings.GEMINI_MAX_RPM:
            sleep_for = 60 - (now - _last_call_ts[0]) + 0.5
            time.sleep(max(sleep_for, 0))
        _last_call_ts.append(time.time())


def generate_json(
    *,
    model: str,
    system_instruction: str,
    prompt: str,
    response_schema: dict,
    use_search: bool = False,
    max_retries: int = 4,
) -> dict:
    """Llama a Gemini pidiendo salida JSON validada contra response_schema."""
    client = _get_client()

    config_kwargs = dict(
        system_instruction=system_instruction,
        temperature=0.4,
    )
    if use_search:
        # Con grounding no se puede forzar response_mime_type=json de forma nativa;
        # pedimos JSON por instrucción y parseamos de forma tolerante.
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        prompt = (
            prompt
            + "\n\nResponde EXCLUSIVAMENTE con un objeto JSON válido (sin markdown, "
              "sin ```), que cumpla exactamente este JSON schema:\n"
            + json.dumps(response_schema, ensure_ascii=False)
        )
    else:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema

    last_error = None
    for attempt in range(max_retries):
        try:
            _check_daily_quota()
            _throttle()
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            text = (resp.text or "").strip()
            text = _strip_code_fences(text)
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_error = e
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            if "perday" in msg or "requestsperday" in msg or "generaterequestsperday" in msg:
                # Cuota DIARIA agotada para este modelo: reintentar no sirve de nada (no se
                # resetea en segundos), así que fallamos rápido con un mensaje claro en vez de
                # quemar 4 intentos con backoff.
                raise RuntimeError(
                    f"Cuota diaria gratuita agotada para el modelo '{model}'. Prueba de nuevo "
                    f"mañana, baja MAX_CANDIDATES_PER_RUN, o revisa tus límites reales en "
                    f"https://aistudio.google.com (Rate limits). Detalle: {e}"
                ) from e
            if "429" in msg or "rate" in msg or "resource_exhausted" in msg:
                time.sleep(8 * (attempt + 1))
            elif attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"Gemini generate_json falló tras {max_retries} intentos: {last_error}")


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
