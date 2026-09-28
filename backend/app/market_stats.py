import statistics

from . import binance_alpha


def compute_market_stats(alpha_id: str, end_time_ms: int | None = None) -> dict:
    """Deriva estadísticas simples de las klines de 1h de los últimos 7 días (o de los 7 días
    previos a `end_time_ms`, usado por el backfill histórico)."""
    try:
        klines = binance_alpha.get_klines(alpha_id + "USDT", interval="1h", limit=168, end_time_ms=end_time_ms)
    except Exception as e:
        return {"error": str(e)}
    return stats_from_klines(klines)


def stats_from_klines(klines: list[list]) -> dict:
    if not klines or len(klines) < 2:
        return {"error": "klines insuficientes"}

    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    volatility = statistics.pstdev(returns) * 100 if len(returns) > 1 else 0.0

    change_7d_pct = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] > 0 else 0.0

    price = closes[-1]
    high_7d, low_7d = max(highs), min(lows)
    # Plan v2, B1 (2026-09-28): features de precio para el feature store. Mismas definiciones que
    # la prueba de validación (633 filas / 250 tokens) que motivó el plan: ver movement_context.py.
    peak, max_dd = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            max_dd = min(max_dd, (c - peak) / peak * 100)
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    recent_vol = sum(volumes[-24:]) / len(volumes[-24:]) if volumes else 0
    return {
        "change_7d_pct": round(change_7d_pct, 2),
        "high_7d": high_7d,
        "low_7d": low_7d,
        "current_price": price,
        "hourly_volatility_pct": round(volatility, 3),
        "avg_hourly_volume": round(avg_vol, 2),
        "num_candles": len(klines),
        "max_drawdown_7d_pct": round(max_dd, 2),
        "dist_from_high_pct": round((high_7d - price) / price * 100, 2) if price > 0 else None,
        "dist_from_low_pct": round((price - low_7d) / price * 100, 2) if price > 0 else None,
        "range_pos_7d": round((price - low_7d) / (high_7d - low_7d), 4) if high_7d > low_7d else None,
        "vol_trend_24h_vs_7d": round(recent_vol / avg_vol, 3) if avg_vol > 0 else None,
    }


# Nombre de columna en `predictions` <- clave de compute_market_stats (una sola fuente de verdad
# para pipeline.py, el backfill y movement_context.py).
PRICE_FEATURE_COLUMNS = {
    "vol_hourly_pct": "hourly_volatility_pct",
    "momentum_7d_pct": "change_7d_pct",
    "max_drawdown_7d_pct": "max_drawdown_7d_pct",
    "dist_from_high_pct": "dist_from_high_pct",
    "dist_from_low_pct": "dist_from_low_pct",
    "range_pos_7d": "range_pos_7d",
    "vol_trend_24h_vs_7d": "vol_trend_24h_vs_7d",
}


# Con menos velas que esto (token listado hace < ~4 días) volatilidad y rango de "7 días" no
# significan lo mismo que en la muestra validada (>=100 velas); se dejan en None en vez de mezclar.
MIN_CANDLES_FOR_FEATURES = 100


def price_features(stats: dict | None) -> dict:
    """Columnas de features de precio a partir de compute_market_stats(); todo None si el token
    no tiene klines (DexScreener, error de red, historial insuficiente) -- nunca se inventan."""
    ok = stats and "error" not in stats and (stats.get("num_candles") or 0) >= MIN_CANDLES_FOR_FEATURES
    stats = stats if ok else {}
    return {col: stats.get(key) for col, key in PRICE_FEATURE_COLUMNS.items()}
