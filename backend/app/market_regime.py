"""
Etiqueta de régimen de mercado (Plan de correcciones, Fase A, 2026-09-28).

Por qué existe: en el historial real el acierto del grupo de control (descartados) cambió de
21% a 43% de una semana a otra -- el mercado en general domina el resultado, y comparar un
solo número global contra el sistema engaña. Guardar en qué régimen se hizo cada predicción
permite condicionar el benchmark después.

Esto NO pausa nada ni cambia ningún veredicto: es una etiqueta descriptiva. Las bandas
(+/-3% de tendencia semanal, 3% de desviación diaria) son convenciones para agrupar, no umbrales
calibrados; se guardan también los números crudos para poder reagrupar sin recalcular.
Mejor esfuerzo: si el endpoint falla, devuelve None y el ciclo sigue igual.
"""
import json
import statistics
from datetime import datetime, timezone

import requests

# data-api.binance.vision es el espejo público de datos de mercado (no está bloqueado por
# región como api.binance.com puede estarlo desde algunos runners de GitHub Actions).
_ENDPOINTS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
TREND_BAND_PCT = 3.0
HIGH_VOL_DAILY_PCT = 3.0


def compute_regime() -> dict | None:
    closes = None
    for url in _ENDPOINTS:
        try:
            resp = requests.get(url, params={"symbol": "BTCUSDT", "interval": "1d", "limit": 9}, timeout=15)
            resp.raise_for_status()
            closes = [float(k[4]) for k in resp.json()]
            if len(closes) >= 8:
                break
        except Exception:
            closes = None
    if not closes or len(closes) < 8:
        return None
    closes = closes[-8:]
    change_7d = (closes[-1] - closes[0]) / closes[0] * 100
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, len(closes))]
    vol = statistics.pstdev(rets)
    trend = "alza" if change_7d > TREND_BAND_PCT else "baja" if change_7d < -TREND_BAND_PCT else "lateral"
    return {
        "btc_7d_change_pct": round(change_7d, 2),
        "btc_7d_daily_vol_pct": round(vol, 2),
        "trend": trend,
        "volatility": "alta" if vol > HIGH_VOL_DAILY_PCT else "normal",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def regime_json() -> str | None:
    r = compute_regime()
    return json.dumps(r, ensure_ascii=False) if r else None
