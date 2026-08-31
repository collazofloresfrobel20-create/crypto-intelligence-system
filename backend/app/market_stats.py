import statistics

from . import binance_alpha


def compute_market_stats(alpha_id: str) -> dict:
    """Deriva estadísticas simples de las klines de 1h de los últimos 7 días."""
    try:
        klines = binance_alpha.get_klines(alpha_id + "USDT", interval="1h", limit=168)
    except Exception as e:
        return {"error": str(e)}

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

    return {
        "change_7d_pct": round(change_7d_pct, 2),
        "high_7d": max(highs),
        "low_7d": min(lows),
        "current_price": closes[-1],
        "hourly_volatility_pct": round(volatility, 3),
        "avg_hourly_volume": round(sum(volumes) / len(volumes), 2) if volumes else 0,
        "num_candles": len(klines),
    }
