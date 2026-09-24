"""Backtesting continuo (spec sección 8), extendido para cubrir tanto los tokens analizados
(pasaron los hard filters + research LLM) como los descartados (rechazados por hard filters),
de forma que el sistema pueda auto-evaluar si sus propios filtros están bien calibrados."""
import statistics
from datetime import datetime, timezone

from . import binance_alpha, dexscreener
from .db import get_conn, row_to_dict

# Umbrales de clasificación de tesis. La tesis "se cumple" si el movimiento MÁXIMO alcanzado
# durante el horizonte llegó al rango objetivo (+20-30%); "parcial" si se acercó bastante sin
# llegar a +20%; "no" en cualquier otro caso. Documentado aquí porque el spec no fija el corte
# exacto de "parcial", solo que debe existir la categoría. Aplica igual a analyzed y discarded.
FULL_SUCCESS_THRESHOLD = 20.0
PARTIAL_SUCCESS_THRESHOLD = 10.0


def _hours_since(iso_ts: str) -> float:
    dt = datetime.fromisoformat(iso_ts)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _price_path_since(alpha_id: str, created_at: str) -> list[tuple[float, float]] | None:
    """Devuelve lista de (horas_desde_creacion, close_price) o None si no se pudo obtener."""
    try:
        klines = binance_alpha.get_klines(alpha_id + "USDT", interval="1h", limit=400)
    except Exception:
        return None
    if not klines:
        return None

    created_dt = datetime.fromisoformat(created_at)
    created_ms = created_dt.timestamp() * 1000

    path = []
    for k in klines:
        open_time_ms = float(k[0])
        close_time_ms = float(k[6])
        if close_time_ms < created_ms:
            continue  # vela que ya cerró antes de la predicción: irrelevante
        close_price = float(k[4])
        hours = max((open_time_ms - created_ms) / (1000 * 3600), 0)
        path.append((hours, close_price))
    return path or None


def _price_at_hours(path: list[tuple[float, float]], target_hours: float) -> float | None:
    """Precio del punto del path más cercano a target_hours sin pasarse (None si el path
    todavía no llega tan lejos). Reusa el mismo path ya descargado, sin llamadas extra."""
    candidates = [p for p in path if p[0] <= target_hours]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p[0])[1]


def _realized_volatility_pct(entry_price: float, path: list[tuple[float, float]]) -> float:
    """Desviación estándar de los retornos punto a punto del path realmente observado (mismo
    método que market_stats.compute_market_stats usa para el pre-análisis, aplicado aquí al
    post-análisis)."""
    closes = [entry_price] + [p[1] for p in path]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] > 0
    ]
    return round(statistics.pstdev(returns) * 100, 3) if len(returns) > 1 else 0.0


def _classify(max_return_pct: float) -> str:
    if max_return_pct >= FULL_SUCCESS_THRESHOLD:
        return "yes"
    if max_return_pct >= PARTIAL_SUCCESS_THRESHOLD:
        return "partial"
    return "no"


def evaluate_prediction(pred: dict) -> dict | None:
    """Evalúa una entrada individual (analyzed o discarded) usando su historial de klines. No escribe en DB."""
    alpha_id = pred.get("alpha_id")
    entry_price = pred.get("price_at_prediction")
    if not alpha_id or not entry_price:
        return None

    path = _price_path_since(alpha_id, pred["created_at"])
    if not path:
        return None

    price_after = path[-1][1]
    max_hours, max_price = max(path, key=lambda p: p[1])
    min_price = min(p[1] for p in path)

    return_pct = ((price_after - entry_price) / entry_price) * 100
    max_return_pct = ((max_price - entry_price) / entry_price) * 100
    max_drawdown_pct = ((min_price - entry_price) / entry_price) * 100

    price_day1 = _price_at_hours(path, 24)
    price_day3 = _price_at_hours(path, 72)

    return {
        "price_after": price_after,
        "max_price_reached": max_price,
        "return_pct": round(return_pct, 2),
        "max_return_pct": round(max_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "time_to_max_hours": round(max_hours, 1),
        "thesis_result": _classify(max_return_pct),
        "return_at_day1_pct": round(((price_day1 - entry_price) / entry_price) * 100, 2) if price_day1 else None,
        "return_at_day3_pct": round(((price_day3 - entry_price) / entry_price) * 100, 2) if price_day3 else None,
        "volatility_pct": _realized_volatility_pct(entry_price, path),
    }


UNEVALUABLE_GRACE_DAYS = 3  # margen extra tras vencer el horizonte antes de darla por no-evaluable


def evaluate_due_predictions() -> int:
    """Evalúa y persiste TODAS las entradas 'pending' (analyzed + discarded) cuyo horizonte ya
    venció. Si no se puede obtener su precio (klines vacío -- típicamente porque el token ya no
    está listado en Binance Alpha o cambió de par) se reintenta un margen de
    UNEVALUABLE_GRACE_DAYS; pasado eso se marca 'unevaluable' en vez de quedar 'pending' para
    siempre reintentándose cada ciclo sin nunca resolver (encontrado en producción: 3 símbolos
    llevaban semanas atorados así). Devuelve cuántas se evaluaron (no cuenta las marcadas
    unevaluable)."""
    with get_conn() as conn:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE status = 'pending'"
        ).fetchall()]

    evaluated = 0
    for pred in rows:
        hours_since = _hours_since(pred["created_at"])
        if hours_since < pred["horizon_days"] * 24:
            continue
        is_binance = pred.get("source", "binance_alpha") == "binance_alpha"
        result = evaluate_prediction(pred) if is_binance else evaluate_prediction_polled(pred)
        if not result:
            if hours_since >= (pred["horizon_days"] + UNEVALUABLE_GRACE_DAYS) * 24:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE predictions SET status = 'unevaluable', evaluated_at = ? WHERE id = ?",
                        (datetime.now(timezone.utc).isoformat(), pred["id"]),
                    )
            continue
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE predictions SET
                    price_after = ?, max_price_reached = ?, return_pct = ?, max_return_pct = ?,
                    max_drawdown_pct = ?, time_to_max_hours = ?, thesis_result = ?,
                    return_at_day1_pct = ?, return_at_day3_pct = ?, volatility_pct = ?,
                    status = 'evaluated', evaluated_at = ?
                WHERE id = ?
                """,
                (
                    result["price_after"], result["max_price_reached"], result["return_pct"],
                    result["max_return_pct"], result["max_drawdown_pct"], result["time_to_max_hours"],
                    result["thesis_result"], result["return_at_day1_pct"], result["return_at_day3_pct"],
                    result["volatility_pct"], datetime.now(timezone.utc).isoformat(), pred["id"],
                ),
            )
        evaluated += 1
    return evaluated


def update_current_marks() -> int:
    """'Mark-to-market' de las entradas 'pending' cuyo horizonte AÚN no vence: usa el mismo
    historial de klines que evaluate_prediction() para guardar el precio actual y el % de
    retorno desde el análisis. Le da visibilidad de rendimiento real antes del día 7 (lo que ya
    se muestra post-evaluación, a mitad de camino) sin ser una señal de entrada/salida -- es
    puro dato observado, igual que max_return_pct/return_pct tras la evaluación final."""
    with get_conn() as conn:
        rows = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE status = 'pending'"
        ).fetchall()]

    marked = 0
    for pred in rows:
        if _hours_since(pred["created_at"]) >= pred["horizon_days"] * 24:
            continue  # ya vencido: lo toma evaluate_due_predictions()
        entry_price = pred.get("price_at_prediction")
        if not entry_price:
            continue
        is_binance = pred.get("source", "binance_alpha") == "binance_alpha"

        if is_binance:
            alpha_id = pred.get("alpha_id")
            if not alpha_id:
                continue
            path = _price_path_since(alpha_id, pred["created_at"])
            if not path:
                continue
            current_price = path[-1][1]
        else:
            # Sin historial de velas gratis (ver dexscreener.py): un solo punto de precio por
            # ciclo, y se acumula el máximo/mínimo visto hasta ahora en running_max/min_price
            # para que evaluate_prediction_polled() pueda cerrar el caso al vencer el horizonte.
            chain, address = pred.get("chain_name"), pred.get("contract_address")
            if not chain or not address:
                continue
            current_price = dexscreener.get_current_price(chain, address)
            if current_price is None:
                continue

        current_return_pct = ((current_price - entry_price) / entry_price) * 100
        running_max = max(pred.get("running_max_price") or entry_price, current_price)
        running_min = min(pred.get("running_min_price") or entry_price, current_price)
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE predictions SET current_price = ?, current_return_pct = ?, price_checked_at = ?,
                    running_max_price = ?, running_min_price = ?
                WHERE id = ?
                """,
                (current_price, round(current_return_pct, 2), datetime.now(timezone.utc).isoformat(),
                 running_max, running_min, pred["id"]),
            )
        marked += 1
    return marked


def evaluate_prediction_polled(pred: dict) -> dict | None:
    """Evaluación para predicciones de fuentes sin historial de velas gratis (ej. DexScreener):
    el 'máximo alcanzado' se construye por muestreo -- el valor más alto/bajo que
    update_current_marks() fue viendo cada ciclo de 12h durante la semana (running_max/min_price),
    en vez de un lookback horario completo como con evaluate_prediction()/Binance. Menos preciso
    (resolución de 12h en vez de 1h, y no se puede saber EN QUÉ hora exacta llegó al máximo) pero
    es lo único posible sin pagar por datos históricos. Los campos que de verdad no se pueden
    saber con este método (hora exacta del máximo, retorno a día 1/3 exacto, volatilidad de la
    serie completa) quedan en None a propósito, en vez de inventarse."""
    chain, address = pred.get("chain_name"), pred.get("contract_address")
    entry_price = pred.get("price_at_prediction")
    if not chain or not address or not entry_price:
        return None
    current_price = dexscreener.get_current_price(chain, address)
    if current_price is None:
        return None

    running_max = max(pred.get("running_max_price") or entry_price, current_price)
    running_min = min(pred.get("running_min_price") or entry_price, current_price)

    return_pct = ((current_price - entry_price) / entry_price) * 100
    max_return_pct = ((running_max - entry_price) / entry_price) * 100
    max_drawdown_pct = ((running_min - entry_price) / entry_price) * 100

    return {
        "price_after": current_price,
        "max_price_reached": running_max,
        "return_pct": round(return_pct, 2),
        "max_return_pct": round(max_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "time_to_max_hours": None,
        "thesis_result": _classify(max_return_pct),
        "return_at_day1_pct": None,
        "return_at_day3_pct": None,
        "volatility_pct": None,
    }


def _confidence_bucket(score: int | None) -> str:
    if score is None:
        return "desconocido"
    if score >= 90:
        return "90-100"
    if score >= 75:
        return "75-89"
    if score >= 60:
        return "60-74"
    if score >= 40:
        return "40-59"
    return "<40"


def get_performance_stats() -> dict:
    with get_conn() as conn:
        evaluated = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE status = 'evaluated' AND category = 'analyzed'"
        ).fetchall()]
        total_analyzed = conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE category = 'analyzed'"
        ).fetchone()["c"]

    n = len(evaluated)
    if n == 0:
        return {
            "total_predictions": total_analyzed,
            "evaluated_predictions": 0,
            "message": "Aún no hay predicciones evaluadas (esperando que venza el horizonte de 7 días).",
        }

    reached_20 = sum(1 for p in evaluated if (p["max_return_pct"] or 0) >= 20)
    reached_30 = sum(1 for p in evaluated if (p["max_return_pct"] or 0) >= 30)
    avg_max_return = sum(p["max_return_pct"] or 0 for p in evaluated) / n
    avg_drawdown = sum(p["max_drawdown_pct"] or 0 for p in evaluated) / n

    buckets: dict[str, dict] = {}
    for p in evaluated:
        b = _confidence_bucket(p.get("confidence_score"))
        buckets.setdefault(b, {"n": 0, "yes": 0, "partial": 0, "no": 0})
        buckets[b]["n"] += 1
        buckets[b][p.get("thesis_result") or "no"] += 1

    accuracy_by_confidence = {
        b: {
            "n": v["n"],
            "success_rate_pct": round(100 * (v["yes"] + 0.5 * v["partial"]) / v["n"], 1) if v["n"] else 0,
        }
        for b, v in buckets.items()
    }

    return {
        "total_predictions": total_analyzed,
        "evaluated_predictions": n,
        "pct_reached_plus20": round(100 * reached_20 / n, 1),
        "pct_reached_plus30": round(100 * reached_30 / n, 1),
        "avg_max_return_pct": round(avg_max_return, 2),
        "avg_max_drawdown_pct": round(avg_drawdown, 2),
        "accuracy_by_confidence_bucket": accuracy_by_confidence,
    }


def get_filter_efficacy_stats() -> dict:
    """
    Compara el desempeño real de lo ANALIZADO (pasó hard filters) contra lo DESCARTADO
    (rechazado por hard filters). Si un % alto de descartados también hubiera alcanzado
    +20-30%, es señal de que los filtros están descartando oportunidades válidas (demasiado
    estrictos); si casi ninguno lo alcanza, los filtros están bien calibrados.
    """
    with get_conn() as conn:
        analyzed = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE status='evaluated' AND category='analyzed'"
        ).fetchall()]
        discarded = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE status='evaluated' AND category='discarded'"
        ).fetchall()]

    def summarize(rows):
        n = len(rows)
        if n == 0:
            return {"n": 0, "pct_reached_plus20": None, "avg_max_return_pct": None}
        reached = sum(1 for r in rows if (r["max_return_pct"] or 0) >= 20)
        avg = sum(r["max_return_pct"] or 0 for r in rows) / n
        return {
            "n": n,
            "pct_reached_plus20": round(100 * reached / n, 1),
            "avg_max_return_pct": round(avg, 2),
        }

    analyzed_summary = summarize(analyzed)
    discarded_summary = summarize(discarded)

    # Razones de rechazo más comunes entre los descartados que SÍ hubieran sido una buena
    # oportunidad (max_return_pct >= 20), para saber qué filtro específico está siendo
    # demasiado estricto.
    missed_reasons: dict[str, int] = {}
    for r in discarded:
        if (r.get("max_return_pct") or 0) >= 20:
            for reason in (r.get("rejection_reasons") or []):
                missed_reasons[reason] = missed_reasons.get(reason, 0) + 1

    return {
        "analyzed": analyzed_summary,
        "discarded": discarded_summary,
        "missed_opportunities_reasons": dict(
            sorted(missed_reasons.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ),
    }


HYPOTHETICAL_STAKE_USD = 100.0
MIN_SAMPLE_FOR_CONFIDENCE = 20


def get_history_overview() -> dict:
    """
    Historial completo de desempeño del sistema (todas las actualizaciones corridas hasta
    ahora), para responder directamente '¿qué tan bien le ha ido?': aciertos por tipo de
    veredicto, tiempo promedio a máximo, y una simulación de dinero puramente hipotética
    (NO es una promesa de rendimiento ni una recomendación de inversión).
    """
    with get_conn() as conn:
        total_updates = conn.execute("SELECT COUNT(*) c FROM pipeline_runs").fetchone()["c"]
        date_range = conn.execute(
            "SELECT MIN(created_at) first, MAX(created_at) last FROM predictions WHERE category='analyzed'"
        ).fetchone()
        total_analyzed = conn.execute(
            "SELECT COUNT(*) c FROM predictions WHERE category='analyzed'"
        ).fetchone()["c"]
        evaluated = [row_to_dict(r) for r in conn.execute(
            "SELECT * FROM predictions WHERE category='analyzed' AND status='evaluated'"
        ).fetchall()]

    n = len(evaluated)
    if n == 0:
        return {
            "total_updates_run": total_updates,
            "total_analyzed": total_analyzed,
            "total_evaluated": 0,
            "first_analyzed_at": date_range["first"],
            "last_analyzed_at": date_range["last"],
            "message": "Aún no hay predicciones evaluadas (esperan a que venza su horizonte de 7 días).",
        }

    hits = sum(1 for p in evaluated if p["thesis_result"] == "yes")
    partials = sum(1 for p in evaluated if p["thesis_result"] == "partial")
    overall_hit_rate_pct = round(100 * (hits + 0.5 * partials) / n, 1)
    avg_time_to_max_hours = sum(p["time_to_max_hours"] or 0 for p in evaluated) / n
    # avg_time_to_max_hours mezcla exitos y fracasos (el "maximo" de algo que nunca llego a
    # +20% no es tiempo-a-la-meta, es solo su pico real). Este otro solo cuenta los casos que
    # SI llegaron -- es la unica cifra honesta de "cuanto tarda cuando funciona".
    hit_rows = [p for p in evaluated if p["thesis_result"] == "yes"]
    avg_time_to_target_hours = (
        round(sum(p["time_to_max_hours"] or 0 for p in hit_rows) / len(hit_rows), 1)
        if hit_rows else None
    )

    by_verdict: dict[str, list[dict]] = {}
    for p in evaluated:
        by_verdict.setdefault(p["verdict"] or "N/A", []).append(p)

    def verdict_summary(rows):
        m = len(rows)
        h = sum(1 for r in rows if r["thesis_result"] == "yes")
        pa = sum(1 for r in rows if r["thesis_result"] == "partial")
        return {
            "n": m,
            "success_rate_pct": round(100 * (h + 0.5 * pa) / m, 1),
            "avg_max_return_pct": round(sum(r["max_return_pct"] or 0 for r in rows) / m, 2),
            "avg_return_at_horizon_pct": round(sum(r["return_pct"] or 0 for r in rows) / m, 2),
        }

    accuracy_by_verdict = {v: verdict_summary(rows) for v, rows in by_verdict.items()}

    best = max(evaluated, key=lambda p: p["max_return_pct"] or -999)
    worst = min(evaluated, key=lambda p: p["max_return_pct"] or 999)

    # Simulación puramente educativa: NO es una recomendación ni promesa de rendimiento.
    # "hold_to_horizon" = si se hubiera mantenido hasta el día 7 exacto (return_pct).
    # "best_case" = si se hubiera vendido justo en el máximo alcanzado (max_return_pct, poco realista).
    stake = HYPOTHETICAL_STAKE_USD
    total_staked = stake * n
    hold_value = sum(stake * (1 + (p["return_pct"] or 0) / 100) for p in evaluated)
    best_case_value = sum(stake * (1 + (p["max_return_pct"] or 0) / 100) for p in evaluated)

    return {
        "total_updates_run": total_updates,
        "total_analyzed": total_analyzed,
        "total_evaluated": n,
        "first_analyzed_at": date_range["first"],
        "last_analyzed_at": date_range["last"],
        "overall_hit_rate_pct": overall_hit_rate_pct,
        "avg_time_to_max_hours": round(avg_time_to_max_hours, 1),
        "avg_time_to_target_hours": avg_time_to_target_hours,
        "hit_count_for_timing": len(hit_rows),
        "low_sample_warning": n < MIN_SAMPLE_FOR_CONFIDENCE,
        "min_sample_for_confidence": MIN_SAMPLE_FOR_CONFIDENCE,
        "accuracy_by_verdict": accuracy_by_verdict,
        "best_call": {"symbol": best["symbol"], "max_return_pct": best["max_return_pct"]},
        "worst_call": {"symbol": worst["symbol"], "max_return_pct": worst["max_return_pct"]},
        "hypothetical_simulation": {
            "stake_per_pick_usd": stake,
            "total_staked_usd": round(total_staked, 2),
            "hold_to_horizon_value_usd": round(hold_value, 2),
            "hold_to_horizon_return_pct": round(100 * (hold_value - total_staked) / total_staked, 2),
            "best_case_value_usd": round(best_case_value, 2),
            "best_case_return_pct": round(100 * (best_case_value - total_staked) / total_staked, 2),
            "disclaimer": "Simulación educativa con capital hipotético igual en cada predicción "
                          "analizada. No es una recomendación de inversión ni garantía de resultados futuros.",
        },
    }
