"""
Cliente para GoPlus Security API (api.gopluslabs.io). Endpoint de token_security
funciona sin autenticación para uso ligero/gratuito; si se configuran
GOPLUS_APP_KEY/GOPLUS_APP_SECRET se usan para subir el rate limit (Fase 4, 2026-09-24 --
necesario porque ahora se consulta también en lote para los sobrevivientes post-filtros, no
solo para los ~15 candidatos finalmente seleccionados).
"""
import hashlib
import threading
import time

import requests

from .config import settings

BASE_URL = "https://api.gopluslabs.io/api/v1"
MAX_RETRIES = 3  # encontrado en producción: sin reintento, un solo timeout/blip transitorio
                  # pierde para siempre la evidencia Tier 1 del Security Analyst para ese token
                  # -- confirmado manualmente que la API sí tenía el dato, solo la llamada falló.

# Mapeo de chainName (tal como viene de Binance Alpha) a chain_id de GoPlus.
CHAIN_ID_MAP = {
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "bnb": "56",
    "arbitrum": "42161",
    "base": "8453",
    "optimism": "10",
    "polygon": "137",
    "avalanche": "43114",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (CIS Research Bot)"})

_access_token_cache = {"token": None, "expires_at": 0.0}
_token_lock = threading.Lock()


def _get_access_token() -> str | None:
    """
    Flujo autenticado de GoPlus (Fase 4, 2026-09-24), según su documentación pública:
    POST /api/v1/token con sign = sha1(app_key + time + app_secret). Sube el rate limit
    respecto al modo sin autenticar -- necesario ahora que se consulta en lote.

    OJO: este flujo no se pudo probar contra credenciales reales en este entorno
    (GOPLUS_APP_KEY/GOPLUS_APP_SECRET vienen vacíos por defecto, y no hay ninguno configurado
    en producción al momento de escribir esto) -- verificar contra una llamada real en cuanto
    existan credenciales. Cualquier fallo aquí (firma incorrecta, forma de respuesta distinta a
    la documentada, etc.) se traga y devuelve None -- el caller sigue sin autenticar, nunca se
    rompe el pipeline por esto.
    """
    if not settings.GOPLUS_APP_KEY or not settings.GOPLUS_APP_SECRET:
        return None
    with _token_lock:
        if _access_token_cache["token"] and time.time() < _access_token_cache["expires_at"]:
            return _access_token_cache["token"]
        try:
            ts = int(time.time())
            sign = hashlib.sha1(
                f"{settings.GOPLUS_APP_KEY}{ts}{settings.GOPLUS_APP_SECRET}".encode()
            ).hexdigest()
            resp = _session.post(
                f"{BASE_URL}/token",
                json={"app_key": settings.GOPLUS_APP_KEY, "time": ts, "sign": sign},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json().get("result") or {}
            token = result.get("access_token")
            if token:
                _access_token_cache["token"] = token
                _access_token_cache["expires_at"] = time.time() + result.get("expires_in", 3600) - 60
                return token
        except Exception:
            pass
    return None


def get_token_security(chain_name: str, contract_address: str) -> dict | None:
    """
    Devuelve el reporte de seguridad de un contrato. Retorna None si la cadena
    no está soportada, no hay dirección de contrato, o la llamada falla
    (no debe tumbar el pipeline: es una capa de evidencia, no la única fuente).
    """
    if not contract_address:
        return None
    chain_key = (chain_name or "").lower()

    if chain_key == "solana":
        url = f"{BASE_URL}/solana/token_security"
    else:
        chain_id = CHAIN_ID_MAP.get(chain_key)
        if not chain_id:
            return None
        url = f"{BASE_URL}/token_security/{chain_id}"
    params = {"contract_addresses": contract_address}
    headers = {}
    access_token = _get_access_token()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    for attempt in range(MAX_RETRIES):
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            result = payload.get("result") or {}
            # La respuesta indexa por dirección en minúsculas.
            data = result.get(contract_address.lower()) or (list(result.values())[0] if result else None)
            return data
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
    return None


def extract_security_features(security_report: dict | None) -> dict:
    """
    Fase 4 (2026-09-24): extrae señales estructuradas del reporte crudo de GoPlus para usarlas
    como features del clasificador (ml_scoring.py), no como texto libre para los analistas LLM.

    Verificado contra la API real antes de escribir esto (mismo rigor que DexScreener en el
    Batch 1) con un token de BSC (CAKE) y uno de Solana (USDC) -- la forma de la respuesta es
    DISTINTA entre EVM y Solana, así que esta función detecta cuál llegó y extrae de forma
    defensiva. Cada campo es None-seguro: si no viene, o no se puede interpretar, queda en None.

      EVM (chain_id numérico, ej. BSC): creator_percent/owner_percent son strings numéricos
      (fracción 0-1, se multiplican por 100); is_honeypot/is_mintable son strings "0"/"1". NO
      hay un array 'lp_holders' separado -- confirmado con CAKE en BSC: el locking de supply,
      cuando existe, aparece como is_locked=1 dentro del array 'holders' normal (ej. la
      dirección quemada 0x...dead con is_locked=1). lp_holders_locked_pct se calcula sumando
      el 'percent' de los holders con is_locked=1.

      Solana: NO hay creator_percent/owner_percent/is_honeypot -- en su lugar hay 'creators'
      (lista, puede venir vacía) y 'mintable'/'freezable' como objetos {authority, status}
      ("1"=sí puede, "0"=no puede). SÍ hay un array 'lp_holders' separado, pero su campo
      'percent' no se comportó como un 0-100 confiable en la prueba (valores como 199603252
      para USDC, claramente no un porcentaje) -- se ignora ese campo en vez de confiar en un
      dato que no se pudo verificar como correcto; lp_holders_locked_pct e is_honeypot quedan
      en None para Solana.
    """
    empty = {
        "creator_percent": None, "owner_percent": None, "lp_holders_locked_pct": None,
        "is_honeypot": None, "is_mintable": None,
    }
    if not security_report:
        return empty

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    is_solana = "mintable" in security_report or "freezable" in security_report

    if is_solana:
        creators = security_report.get("creators")
        creator_pct = None
        if isinstance(creators, list) and creators:
            total = sum((_num(c.get("percent")) or 0) for c in creators if isinstance(c, dict))
            creator_pct = round(total * 100, 4)
        mintable = security_report.get("mintable")
        is_mintable = int(mintable.get("status") == "1") if isinstance(mintable, dict) else None
        return {
            "creator_percent": creator_pct, "owner_percent": None,
            "lp_holders_locked_pct": None, "is_honeypot": None, "is_mintable": is_mintable,
        }

    creator_pct = _num(security_report.get("creator_percent"))
    owner_pct = _num(security_report.get("owner_percent"))
    if creator_pct is not None:
        creator_pct = round(creator_pct * 100, 4)
    if owner_pct is not None:
        owner_pct = round(owner_pct * 100, 4)

    raw_honeypot = security_report.get("is_honeypot")
    is_honeypot = int(raw_honeypot == "1") if raw_honeypot is not None else None
    raw_mintable = security_report.get("is_mintable")
    is_mintable = int(raw_mintable == "1") if raw_mintable is not None else None

    holders = security_report.get("holders")
    lp_locked_pct = None
    if isinstance(holders, list) and holders:
        try:
            locked = sum(
                (_num(h.get("percent")) or 0) for h in holders
                if isinstance(h, dict) and h.get("is_locked") == 1
            )
            lp_locked_pct = round(locked * 100, 2)
        except (TypeError, AttributeError):
            lp_locked_pct = None

    return {
        "creator_percent": creator_pct, "owner_percent": owner_pct,
        "lp_holders_locked_pct": lp_locked_pct, "is_honeypot": is_honeypot, "is_mintable": is_mintable,
    }
