"""
Cliente para la API pública de DexScreener (api.dexscreener.com). Gratis, sin key.

Segunda fuente de descubrimiento (además de Binance Alpha), pedida por el usuario para "no
limitarse a Binance Alpha" -- cubre PancakeSwap y cientos de otros DEXs/chains.

Diferencia importante frente a Binance Alpha, encontrada al implementar esto (2026-09-24):
DexScreener NO ofrece historial de velas (OHLCV) gratis, solo el precio/estado actual de cada
par. Por eso la verificación de 7 días para tokens de esta fuente no puede usar un lookback
histórico como con Binance (ver backtesting.evaluate_prediction_polled) -- se construye por
muestreo cada 12h (la misma cadencia del ciclo automático), guardando el máximo/mínimo visto
hasta el momento en vez de descargar el camino completo de una sola vez. Menos preciso
(resolución de 12h en vez de 1h) pero es lo único posible sin pagar por datos históricos.
"""
import time

import requests

BASE_URL = "https://api.dexscreener.com"
MAX_RETRIES = 3
BATCH_SIZE = 30  # límite documentado de /tokens/v1/{chain}/{addresses}

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (CIS Research Bot)"})


def _get_json(url: str):
    for attempt in range(MAX_RETRIES):
        try:
            resp = _session.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
    return None


def _get_pairs_batch(chain: str, addresses: list[str]) -> list[dict]:
    """Hasta 30 direcciones por llamada (límite de la API). Devuelve la lista cruda de pares."""
    if not addresses:
        return []
    addr_str = ",".join(addresses[:BATCH_SIZE])
    data = _get_json(f"{BASE_URL}/tokens/v1/{chain}/{addr_str}")
    return data if isinstance(data, list) else []


def get_candidate_addresses(chain: str = "bsc") -> list[str]:
    """Direcciones candidatas desde los feeds gratuitos de 'boosteados' + 'perfiles nuevos' de
    DexScreener, filtradas a `chain`. No es un universo completo como el listado curado de
    Binance Alpha (DexScreener no ofrece eso gratis sin una query) -- es lo que DexScreener
    mismo marca como boosteado o recién descrito, un proxy razonable de "atención temprana"."""
    addresses: set[str] = set()
    for path in ("/token-boosts/latest/v1", "/token-profiles/latest/v1"):
        data = _get_json(BASE_URL + path)
        if not isinstance(data, list):
            continue
        for item in data:
            if item.get("chainId") == chain and item.get("tokenAddress"):
                addresses.add(item["tokenAddress"])
    return list(addresses)


def _to_token_dict(pair: dict) -> dict:
    """Normaliza un par de DexScreener al mismo shape que usan filters.py/pipeline.py para
    tokens de Binance Alpha. holders/totalSupply/circulatingSupply quedan en None -- DexScreener
    no los da; MIN_HOLDERS se confirma más tarde vía GoPlus dentro de research_token()."""
    base = pair.get("baseToken") or {}
    liquidity = (pair.get("liquidity") or {}).get("usd")
    volume24h = (pair.get("volume") or {}).get("h24")
    price_change_24h = (pair.get("priceChange") or {}).get("h24")
    try:
        price = float(pair["priceUsd"]) if pair.get("priceUsd") is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "alphaId": None,
        "chainName": pair.get("chainId"),
        "contractAddress": base.get("address"),
        "price": price,
        "marketCap": pair.get("marketCap") or pair.get("fdv"),
        "fdv": pair.get("fdv"),
        "liquidity": liquidity,
        "volume24h": volume24h,
        "percentChange24h": price_change_24h,
        "holders": None,
        "totalSupply": None,
        "circulatingSupply": None,
        "listingTime": pair.get("pairCreatedAt"),
        "hotTag": None,
        "offline": False,
        "source": "dexscreener",
        "dexId": pair.get("dexId"),
    }


def get_universe(chain: str = "bsc") -> list[dict]:
    """Universo de candidatos de DexScreener para esta cadena: direcciones descubiertas +
    su market data real (batch de hasta 30 por llamada), normalizado. Si un token tiene
    varios pares/pools, se queda con el de mayor liquidez."""
    addresses = get_candidate_addresses(chain)
    if not addresses:
        return []
    by_address: dict[str, dict] = {}
    for i in range(0, len(addresses), BATCH_SIZE):
        batch = addresses[i:i + BATCH_SIZE]
        for pair in _get_pairs_batch(chain, batch):
            base = pair.get("baseToken") or {}
            addr = base.get("address")
            if not addr:
                continue
            liq = (pair.get("liquidity") or {}).get("usd") or 0
            existing = by_address.get(addr)
            existing_liq = (existing.get("liquidity") or {}).get("usd") or 0 if existing else -1
            if liq > existing_liq:
                by_address[addr] = pair
    return [_to_token_dict(p) for p in by_address.values()]


def get_current_price(chain: str, address: str) -> float | None:
    """Precio actual (para mark-to-market/evaluación por muestreo). Si el token tiene varios
    pares, usa el de mayor liquidez -- mismo criterio que get_universe()."""
    pairs = _get_pairs_batch(chain, [address])
    if not pairs:
        return None
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    try:
        return float(best["priceUsd"]) if best.get("priceUsd") is not None else None
    except (TypeError, ValueError):
        return None


def to_market_stats(token: dict) -> dict:
    """Estadísticas de mercado equivalentes a market_stats.compute_market_stats(), pero
    derivadas del snapshot de DexScreener en vez de klines de Binance (que no existen para
    estos tokens). Sin historial horario propio, change_7d/volatilidad de la ventana previa
    quedan como None en vez de inventarse -- el Market Analyst lo debe marcar como
    incertidumbre explícita, igual que con cualquier otro dato faltante."""
    return {
        "change_7d_pct": None,
        "high_7d": None,
        "low_7d": None,
        "current_price": token.get("price"),
        "hourly_volatility_pct": None,
        "avg_hourly_volume": None,
        "num_candles": 0,
        "nota": "fuente DexScreener: sin historial horario propio disponible gratis, a diferencia de Binance Alpha",
    }
