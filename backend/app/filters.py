"""Hard filters: de "cientos/miles de activos" a "candidatos tempranos" (spec sección 4)."""
import time

from .config import settings


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def passes_hard_filters(token: dict) -> tuple[bool, list[str]]:
    """Devuelve (pasa, razones_de_rechazo). No evalúa Earliness (eso es aparte)."""
    reasons = []

    liquidity = _num(token.get("liquidity"))
    volume_24h = _num(token.get("volume24h"))
    market_cap = _num(token.get("marketCap"))
    holders = int(_num(token.get("holders")))

    if token.get("offline"):
        reasons.append("token marcado offline en Binance Alpha")

    if liquidity < settings.MIN_LIQUIDITY_USD:
        reasons.append(f"liquidez {liquidity:,.0f} < mínimo {settings.MIN_LIQUIDITY_USD:,.0f}")

    if volume_24h < settings.MIN_VOLUME_24H_USD:
        reasons.append(f"volumen 24h {volume_24h:,.0f} < mínimo {settings.MIN_VOLUME_24H_USD:,.0f}")

    if market_cap <= 0:
        reasons.append("market cap no disponible")
    elif market_cap > settings.MAX_MARKET_CAP_USD:
        reasons.append(f"market cap {market_cap:,.0f} > máximo {settings.MAX_MARKET_CAP_USD:,.0f} (deja de ser small-cap)")

    if holders and holders < settings.MIN_HOLDERS:
        reasons.append(f"holders {holders} < mínimo {settings.MIN_HOLDERS}")

    listing_time = token.get("listingTime")
    if listing_time:
        try:
            listing_ms = float(listing_time)
            age_days = (time.time() * 1000 - listing_ms) / (1000 * 60 * 60 * 24)
            if age_days > settings.MAX_LISTING_AGE_DAYS:
                reasons.append(f"listado hace {age_days:.0f} días > máximo {settings.MAX_LISTING_AGE_DAYS} (ya no es 'reciente')")
        except (TypeError, ValueError):
            pass

    return (len(reasons) == 0, reasons)


def rank_candidates(tokens: list[dict], limit: int) -> list[dict]:
    """
    Ranking simple pre-Earliness para no gastar llamadas de Gemini en todo el universo:
    prioriza volumen relativo a market cap (proxy de momentum temprano) y recencia de listado.
    El Earliness Score real (LLM) se calcula después, ya con el research profundo.
    """
    def score(t: dict) -> float:
        mc = _num(t.get("marketCap"), 1)
        vol = _num(t.get("volume24h"))
        change = _num(t.get("percentChange24h"))
        vol_ratio = vol / mc if mc > 0 else 0
        return vol_ratio * 100 + max(change, 0)

    ranked = sorted(tokens, key=score, reverse=True)
    return ranked[:limit]
