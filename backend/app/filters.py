"""Hard filters: de "cientos/miles de activos" a "candidatos tempranos" (spec sección 4)."""
import time

from .config import settings


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def token_age_days(token: dict) -> float | None:
    """Antigüedad del listado en días, o None si el token no trae 'listingTime'."""
    listing_time = token.get("listingTime")
    if not listing_time:
        return None
    try:
        listing_ms = float(listing_time)
    except (TypeError, ValueError):
        return None
    return (time.time() * 1000 - listing_ms) / (1000 * 60 * 60 * 24)


def _evaluate_filters(token: dict) -> tuple[list[str], list[dict]]:
    """Corre los 5 chequeos de hard filters. Devuelve (razones_texto, margenes_numericos) --
    margenes solo incluye los filtros numéricos que fallaron, con el valor real, el umbral, y
    qué tan cerca estuvo de pasar (ratio_of_threshold), para poder distinguir en el dashboard
    "falló por mucho" de "casi pasó"."""
    reasons = []
    margins = []

    liquidity = _num(token.get("liquidity"))
    volume_24h = _num(token.get("volume24h"))
    market_cap = _num(token.get("marketCap"))
    holders = int(_num(token.get("holders")))

    if token.get("offline"):
        reasons.append("token marcado offline en Binance Alpha")

    if liquidity < settings.MIN_LIQUIDITY_USD:
        reasons.append(f"liquidez {liquidity:,.0f} < mínimo {settings.MIN_LIQUIDITY_USD:,.0f}")
        margins.append({
            "filter": "MIN_LIQUIDITY_USD", "actual": liquidity, "threshold": settings.MIN_LIQUIDITY_USD,
            "ratio_of_threshold": round(liquidity / settings.MIN_LIQUIDITY_USD, 3) if settings.MIN_LIQUIDITY_USD else None,
        })

    if volume_24h < settings.MIN_VOLUME_24H_USD:
        reasons.append(f"volumen 24h {volume_24h:,.0f} < mínimo {settings.MIN_VOLUME_24H_USD:,.0f}")
        margins.append({
            "filter": "MIN_VOLUME_24H_USD", "actual": volume_24h, "threshold": settings.MIN_VOLUME_24H_USD,
            "ratio_of_threshold": round(volume_24h / settings.MIN_VOLUME_24H_USD, 3) if settings.MIN_VOLUME_24H_USD else None,
        })

    if market_cap <= 0:
        reasons.append("market cap no disponible")
    elif market_cap > settings.MAX_MARKET_CAP_USD:
        reasons.append(f"market cap {market_cap:,.0f} > máximo {settings.MAX_MARKET_CAP_USD:,.0f} (deja de ser small-cap)")
        margins.append({
            "filter": "MAX_MARKET_CAP_USD", "actual": market_cap, "threshold": settings.MAX_MARKET_CAP_USD,
            "ratio_of_threshold": round(settings.MAX_MARKET_CAP_USD / market_cap, 3) if market_cap else None,
        })

    if holders and holders < settings.MIN_HOLDERS:
        reasons.append(f"holders {holders} < mínimo {settings.MIN_HOLDERS}")
        margins.append({
            "filter": "MIN_HOLDERS", "actual": holders, "threshold": settings.MIN_HOLDERS,
            "ratio_of_threshold": round(holders / settings.MIN_HOLDERS, 3) if settings.MIN_HOLDERS else None,
        })

    age_days = token_age_days(token)
    if age_days is not None and age_days > settings.MAX_LISTING_AGE_DAYS:
        reasons.append(f"listado hace {age_days:.0f} días > máximo {settings.MAX_LISTING_AGE_DAYS} (ya no es 'reciente')")
        margins.append({
            "filter": "MAX_LISTING_AGE_DAYS", "actual": round(age_days, 1), "threshold": settings.MAX_LISTING_AGE_DAYS,
            "ratio_of_threshold": round(settings.MAX_LISTING_AGE_DAYS / age_days, 3) if age_days else None,
        })

    return reasons, margins


def passes_hard_filters(token: dict) -> tuple[bool, list[str]]:
    """Devuelve (pasa, razones_de_rechazo). No evalúa Earliness (eso es aparte)."""
    reasons, _margins = _evaluate_filters(token)
    return (len(reasons) == 0, reasons)


def passes_hard_filters_with_margins(token: dict) -> tuple[bool, list[str], list[dict]]:
    """Igual que passes_hard_filters, pero además devuelve el margen numérico de cada filtro
    que falló -- para poder mostrar "casi pasó los filtros" en vez de solo pasa/no pasa."""
    reasons, margins = _evaluate_filters(token)
    return (len(reasons) == 0, reasons, margins)


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
