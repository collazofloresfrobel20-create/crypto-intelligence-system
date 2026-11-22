"""Política candidata en sombra, reproducible por corrida y contrato."""
import hashlib
import json
from pathlib import Path


def version(settings):
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    names = ("MIN_LIQUIDITY_USD", "MIN_VOLUME_24H_USD", "MAX_MARKET_CAP_USD", "MIN_HOLDERS",
             "MAX_LISTING_AGE_DAYS", "MAX_CANDIDATES_PER_RUN", "ML_SCORING_MODE", "PRICE_MODEL_MODE",
             "GEMINI_MODEL_FAST", "GEMINI_MODEL_SMART", "GROQ_MODEL", "ENSEMBLE_JUDGE_MODE",
             "GEMINI_ENABLE_SEARCH_GROUNDING", "MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY",
             "CONFIDENCE_CAP_MODE", "PREDICTION_HORIZON_DAYS")
    digest.update(json.dumps({name: getattr(settings, name) for name in names}, sort_keys=True).encode())
    return digest.hexdigest()


def comparison_arms(ranked_keys, limit, seed):
    """Reserva cupos iguales por cadena. Un cupo vacante queda visible."""
    def draw(key):
        return hashlib.sha256(f"{seed}|{key}".encode()).digest()
    chains = sorted(("bsc", "solana"), key=draw)
    result = {}
    for index, chain in enumerate(chains):
        quota = limit // 2 + int(index < limit % 2)
        group = [key for key in ranked_keys if key.startswith(chain + ":")]
        random_slots = quota // 2 + int(quota % 2 and draw(chain)[0] % 2)
        ranked = group[:quota - random_slots]
        remaining = group[len(ranked):]
        result.update(dict.fromkeys(ranked, "ranked"))
        result.update(dict.fromkeys(sorted(remaining, key=draw)[:random_slots], "random"))
    return result


def market_signals(rows):
    """Descriptores de fotos puntuales. Aceleración en unidades por día al cuadrado."""
    from datetime import datetime
    from math import isfinite

    def number(row, field):
        try:
            value = float(row[field])
        except (TypeError, ValueError, KeyError):
            return None
        return value if isfinite(value) and value >= 0 else None

    latest = rows[-1]
    liquidity, volume = number(latest, "liquidity"), number(latest, "volume_24h")
    signals = {"snapshot_count": len(rows), "volume_to_liquidity": volume / liquidity if liquidity and volume is not None else None,
               "price_up_liquidity_down": None, "holders_acceleration": None, "liquidity_acceleration": None}
    if len(rows) >= 2:
        prices = [number(row, "price") for row in rows[-2:]]
        pool = [number(row, "liquidity") for row in rows[-2:]]
        if None not in prices + pool and all(prices) and all(pool):
            signals["price_up_liquidity_down"] = prices[1] > prices[0] and pool[1] < pool[0]
    if len(rows) >= 3:
        times = [datetime.fromisoformat(row["observed_at"]) for row in rows[-3:]]
        days = [(b-a).total_seconds()/86400 for a,b in zip(times,times[1:])]
        if min(days) <= 0:
            return signals
        for field in ("liquidity", "holders"):
            values = [number(row, field) for row in rows[-3:]]
            if None in values:
                continue
            rates = [(b-a)/d for a,b,d in zip(values,values[1:],days)]
            signals[field + "_acceleration"] = (rates[1]-rates[0])/(sum(days)/2)
    return signals
