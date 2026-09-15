"""
Cliente para GoPlus Security API (api.gopluslabs.io). Endpoint de token_security
funciona sin autenticación para uso ligero/gratuito; si se configuran
GOPLUS_APP_KEY/GOPLUS_APP_SECRET en el futuro se pueden usar para subir el rate limit
(no implementado: no es necesario para el MVP).
"""
import time

import requests

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

    for attempt in range(MAX_RETRIES):
        try:
            resp = _session.get(url, params=params, timeout=15)
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
