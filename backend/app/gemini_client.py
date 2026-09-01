"""
Wrapper sobre google-genai con:
  - Rotación automática de API keys para el modelo "smart" (Bull/Bear/Mediador/Juez): la
    cuota gratuita diaria (confirmada en producción: 20 llamadas/día) es POR PROYECTO de
    Google Cloud, no por key. Varias keys del MISMO proyecto no ayudan en nada; keys de
    proyectos DISTINTOS (configuradas en GEMINI_SMART_API_KEYS) sí traen cuota independiente
    cada una. Cuando la key actual se queda sin cuota del día, se pasa automáticamente a la
    siguiente sin intervención manual.
  - Throttling de RPM para no pasarse del límite del tier gratuito.
  - Reintentos con backoff ante errores transitorios (no ante cuota diaria agotada: ahí no
    sirve reintentar, se rota de key o se falla rápido si ya no quedan).
  - Modo JSON estructurado (response_schema) para que los agentes devuelvan datos parseables.
  - Google Search grounding opcional (requiere facturación habilitada, ver config.py).
"""
import json
import time
import threading
from datetime import date

from google import genai
from google.genai import types

from .config import settings
from .db import get_conn

_clients: dict[str, genai.Client] = {}
_lock = threading.Lock()
_last_call_ts: list[float] = []
_exhausted_keys: set[str] = set()  # keys que ya agotaron su cuota diaria en esta corrida


def _get_client(api_key: str) -> genai.Client:
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


def _key_pool(model: str) -> list[str]:
    """Keys disponibles para este modelo, en orden de preferencia, sin duplicados."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "Falta GEMINI_API_KEY en backend/.env. Consigue una key gratuita en "
            "https://aistudio.google.com/apikey"
        )
    pool = [settings.GEMINI_API_KEY]
    if model == settings.GEMINI_MODEL_SMART:
        pool += [k for k in settings.GEMINI_SMART_API_KEYS if k not in pool]
    return pool


def _check_daily_quota():
    """Tope de seguridad global (todas las keys combinadas) contra un bug que dispare
    llamadas sin control — no modela la cuota real de Gemini (esa es por key/proyecto y se
    maneja con la rotación de keys y el error PerDay más abajo)."""
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT count FROM gemini_usage WHERE day = ?", (today,)).fetchone()
        count = row["count"] if row else 0
        if count >= settings.GEMINI_MAX_RPD:
            raise RuntimeError(
                f"Tope de seguridad diario alcanzado ({settings.GEMINI_MAX_RPD} llamadas "
                "combinadas). Sube GEMINI_MAX_RPD si esto es un falso positivo."
            )
        if row:
            conn.execute("UPDATE gemini_usage SET count = count + 1 WHERE day = ?", (today,))
        else:
            conn.execute("INSERT INTO gemini_usage (day, count) VALUES (?, 1)", (today,))


def _throttle():
    """Serializa llamadas para no exceder GEMINI_MAX_RPM (conservador: se comparte entre todas las keys)."""
    with _lock:
        now = time.time()
        window_start = now - 60
        while _last_call_ts and _last_call_ts[0] < window_start:
            _last_call_ts.pop(0)
        if len(_last_call_ts) >= settings.GEMINI_MAX_RPM:
            sleep_for = 60 - (now - _last_call_ts[0]) + 0.5
            time.sleep(max(sleep_for, 0))
        _last_call_ts.append(time.time())


def _is_daily_quota_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "perday" in msg or "requestsperday" in msg or "generaterequestsperday" in msg


def generate_json(
    *,
    model: str,
    system_instruction: str,
    prompt: str,
    response_schema: dict,
    use_search: bool = False,
    max_retries: int = 4,
) -> dict:
    """Llama a Gemini pidiendo salida JSON validada contra response_schema. Rota de API key
    automáticamente si la cuota diaria de la key actual se agota (ver GEMINI_SMART_API_KEYS)."""
    pool = [k for k in _key_pool(model) if k not in _exhausted_keys]
    if not pool:
        raise RuntimeError(
            f"Todas las API keys disponibles agotaron su cuota diaria para '{model}'. "
            "Agrega más en GEMINI_SMART_API_KEYS (una por proyecto de Google Cloud distinto), "
            "o activa facturación en https://aistudio.google.com."
        )

    config_kwargs = dict(
        system_instruction=system_instruction,
        temperature=0.4,
    )
    if use_search:
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        search_prompt = (
            prompt
            + "\n\nResponde EXCLUSIVAMENTE con un objeto JSON válido (sin markdown, "
              "sin ```), que cumpla exactamente este JSON schema:\n"
            + json.dumps(response_schema, ensure_ascii=False)
        )
    else:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema
        search_prompt = prompt

    last_error = None
    for key in pool:
        client = _get_client(key)
        for attempt in range(max_retries):
            try:
                _check_daily_quota()
                _throttle()
                resp = client.models.generate_content(
                    model=model,
                    contents=search_prompt,
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
                if _is_daily_quota_error(e):
                    # Esta key ya no sirve por hoy: márcala y pasa a la siguiente del pool
                    # (si hay). No tiene caso reintentar la misma key.
                    _exhausted_keys.add(key)
                    break
                msg = str(e).lower()
                if "429" in msg or "rate" in msg or "resource_exhausted" in msg:
                    time.sleep(8 * (attempt + 1))
                elif attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                else:
                    raise
        else:
            # Se agotaron los reintentos con esta key por un error que no era de cuota diaria.
            raise RuntimeError(f"Gemini generate_json falló tras {max_retries} intentos: {last_error}")

    raise RuntimeError(
        f"Todas las API keys disponibles agotaron su cuota diaria para '{model}'. "
        f"Detalle de la última: {last_error}"
    )


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
