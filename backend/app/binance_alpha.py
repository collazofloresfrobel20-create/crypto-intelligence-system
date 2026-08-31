"""
Cliente para las APIs públicas de Binance Alpha (developers.binance.com/docs/alpha).
No requieren API key. Documentadas oficialmente:
  - Token List: GET /bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list
  - Klines:     GET /bapi/defi/v1/public/alpha-trade/klines
"""
import requests

BASE_URL = "https://www.binance.com"
TOKEN_LIST_PATH = "/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list"
KLINES_PATH = "/bapi/defi/v1/public/alpha-trade/klines"

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (CIS Research Bot)"})


class BinanceAlphaError(Exception):
    pass


def get_alpha_token_list() -> list[dict]:
    """Devuelve la lista completa de tokens listados en Binance Alpha."""
    resp = _session.get(BASE_URL + TOKEN_LIST_PATH, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise BinanceAlphaError(payload.get("message", "respuesta no exitosa"))
    return payload.get("data") or []


def get_klines(alpha_id: str, interval: str = "1h", limit: int = 168) -> list[list]:
    """
    Klines para un token Alpha. `alpha_id` debe incluir el sufijo de quote asset, ej.
    "ALPHA_175USDT" (el campo `alphaId` de get_alpha_token_list() sólo trae "ALPHA_175";
    los llamadores deben concatenar "USDT" — verificado empíricamente contra la API real).
    """
    params = {"symbol": alpha_id, "interval": interval, "limit": limit}
    resp = _session.get(BASE_URL + KLINES_PATH, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise BinanceAlphaError(payload.get("message", "respuesta no exitosa"))
    return payload.get("data") or []


def find_token_by_symbol(symbol: str) -> dict | None:
    for t in get_alpha_token_list():
        if t.get("symbol", "").upper() == symbol.upper():
            return t
    return None
