import json
import uuid
from datetime import datetime, timedelta, timezone

from . import binance_alpha, dexscreener, goplus, filters, notifications
from .config import settings
from .db import get_conn
from .market_stats import compute_market_stats
from .agents.research import ALL_ANALYSTS
from .agents.debate import bull_agent, bear_agent, mediator_agent, judge_agent

# Encontrado en producción (2026-09-23): un brote de 503 UNAVAILABLE ("alta demanda") de
# Gemini hizo fallar los 15/15 candidatos de una corrida, cada uno tras ~10-14 min de
# reintentos (11 llamadas por token) -- casi una hora entera sin ningun resultado, y
# bloqueando el siguiente ciclo (GitHub Actions no corre dos en paralelo). Si varios
# candidatos seguidos fallan por algo que NO es cuota diaria, es casi seguro una caida
# temporal de Gemini, no candidatos malos uno tras otro -- mejor parar pronto y dejar que
# el proximo ciclo automatico lo intente de nuevo.
CONSECUTIVE_FAILURES_TO_ABORT = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(run_id: str, message: str):
    with get_conn() as conn:
        row = conn.execute("SELECT log FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        log = json.loads(row["log"]) if row and row["log"] else []
        log.append(f"[{_now()}] {message}")
        conn.execute("UPDATE pipeline_runs SET log = ? WHERE id = ?", (json.dumps(log), run_id))


def create_run(kind: str = "update") -> str:
    run_id = uuid.uuid4().hex[:12]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO pipeline_runs (id, kind, started_at, status, log) VALUES (?, ?, ?, 'running', '[]')",
            (run_id, kind, _now()),
        )
    return run_id


def _finish_run(run_id: str, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pipeline_runs SET status = ?, finished_at = ? WHERE id = ?",
            (status, _now(), run_id),
        )


def get_holder_history(symbol: str, limit: int = 8) -> list[dict]:
    """
    Historial propio de snapshots (analyzed + discarded) de ciclos anteriores para este
    símbolo. Reemplaza la necesidad de un explorer on-chain de pago (Etherscan excluye BSC de
    su tier gratuito, que es donde vive el ~74% de los tokens de Binance Alpha; Solscan no
    tiene un tier gratuito confiable para producción): en vez de pedirle a un tercero la
    evolución de holders/liquidez, usamos lo que el propio sistema ya fotografía en cada
    actualización. Con el auto-update corriendo, esto se vuelve cada vez más útil con el tiempo.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT created_at, holders, price_at_prediction, market_cap, liquidity, volume_24h
            FROM predictions WHERE symbol = ? ORDER BY created_at ASC LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def holder_concentration_pct(security_report: dict | None) -> float | None:
    """Suma el % de supply que controlan los top holders que GoPlus reporta (normalmente hasta
    10-20, según el token -- no es un 'top 10' exacto garantizado, es lo que la API devuelve).
    Encontrado en 'Mejoras cis.pdf' sección B.6/7: el reporte crudo de GoPlus ya trae esto (campo
    'holders' con 'percent' por wallet) pero antes solo lo leía el analista como texto, sin un
    número calculado y expuesto directo en el dashboard."""
    if not security_report:
        return None
    holders = security_report.get("holders") or []
    if not holders:
        return None
    try:
        total = sum(float(h.get("percent", 0)) for h in holders)
    except (TypeError, ValueError):
        return None
    return round(total * 100, 2)


def build_context(token: dict, market_stats: dict, goplus_report: dict | None, holder_history: list[dict]) -> str:
    """Auditoría de data leakage (2026-09-24, pedida en 'Mejoras cis.pdf' sección J): confirmado
    que este contexto no puede filtrar información del futuro. Las 3 fuentes son siempre 'en
    vivo al momento de esta llamada', nunca un dato re-leído con conocimiento posterior:
      - token (Binance Alpha) y market_stats (klines, market_stats.py) se piden EN este
        momento, sin rango de tiempo hacia adelante -- 'ahora' del research ES 'ahora' del dato.
      - goplus_report igual, es una consulta en vivo al momento de la llamada.
      - holder_history (get_holder_history) solo trae snapshots de predictions ANTERIORES
        (created_at ya en el pasado respecto a esta corrida), nunca del futuro.
    Y evaluate_prediction()/_price_path_since() en backtesting.py, simétricamente, filtra los
    klines de evaluación a close_time_ms >= created_ms -- nunca usa una vela anterior al análisis
    para "adivinar" el resultado. No hace falta ningún cambio de código para esto, ya estaba bien
    por construcción; queda documentado explícitamente para que no se rompa sin querer después."""
    context = {
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "chain": token.get("chainName"),
        "contract_address": token.get("contractAddress"),
        "price_usd": token.get("price"),
        "market_cap_usd": token.get("marketCap"),
        "fdv_usd": token.get("fdv"),
        "liquidity_usd": token.get("liquidity"),
        "volume_24h_usd": token.get("volume24h"),
        "percent_change_24h": token.get("percentChange24h"),
        "holders": token.get("holders"),
        "total_supply": token.get("totalSupply"),
        "circulating_supply": token.get("circulatingSupply"),
        "listing_time": token.get("listingTime"),
        "hot_tag": token.get("hotTag"),
        "market_stats_7d": market_stats,
        "goplus_security_report": goplus_report or "no disponible para esta cadena/contrato",
        "top_holders_concentration_pct": holder_concentration_pct(goplus_report),
        "historial_propio_de_ciclos_anteriores": holder_history or "sin ciclos anteriores registrados todavía para este símbolo",
    }
    return json.dumps(context, ensure_ascii=False, default=str)


def research_token(token: dict, run_id: str | None = None) -> dict:
    """Ejecuta research+debate+juez para un token. Devuelve el dict listo para guardar
    (category='analyzed', o 'discarded' si se confirma tarde que no cumple MIN_HOLDERS -- ver
    abajo). El caller (_save_entry) guarda cualquiera de los dos uniformemente."""
    symbol = token.get("symbol")
    alpha_id = token.get("alphaId")
    is_binance = token.get("source", "binance_alpha") == "binance_alpha"

    market_stats = compute_market_stats(alpha_id) if alpha_id else dexscreener.to_market_stats(token)
    security_report = goplus.get_token_security(token.get("chainName"), token.get("contractAddress"))
    if security_report is None and run_id:
        _append_log(run_id, f"  aviso: sin reporte de GoPlus para {symbol} tras reintentos "
                              "(el Security Analyst queda sin su evidencia Tier 1 principal).")

    # Fuentes no-Binance (ej. DexScreener) no traen 'holders' en el universo, así que el hard
    # filter MIN_HOLDERS no lo pudo evaluar antes de llegar aquí. GoPlus sí lo trae -- se
    # confirma ahora, antes de gastar las 11 llamadas de Gemini, no después.
    if not is_binance and security_report:
        holder_count = security_report.get("holder_count")
        if holder_count is not None and int(holder_count) < settings.MIN_HOLDERS:
            if run_id:
                _append_log(run_id, f"  {symbol}: descartado tras confirmar holders ({holder_count} < "
                                     f"{settings.MIN_HOLDERS}) vía GoPlus -- se salta el research de Gemini "
                                     "(Binance Alpha ya trae este dato antes; esta fuente no).")
            return discarded_entry(
                {**token, "holders": holder_count},
                [f"holders {holder_count} < mínimo {settings.MIN_HOLDERS} (confirmado vía GoPlus, "
                 "esta fuente no trae holders en el universo inicial)"],
            )

    holder_history = get_holder_history(symbol)

    context = build_context(token, market_stats, security_report, holder_history)

    findings = {}
    for role, fn in ALL_ANALYSTS.items():
        try:
            findings[role] = fn(context)
        except Exception as e:
            findings[role] = {"error": str(e)}

    findings_json = json.dumps(findings, ensure_ascii=False)

    bull = bull_agent(symbol, findings_json)
    bear = bear_agent(symbol, findings_json, json.dumps(bull, ensure_ascii=False))
    mediator = mediator_agent(symbol, findings_json, json.dumps(bull, ensure_ascii=False), json.dumps(bear, ensure_ascii=False))
    verdict = judge_agent(
        symbol,
        findings_json,
        json.dumps(bull, ensure_ascii=False),
        json.dumps(bear, ensure_ascii=False),
        json.dumps(mediator, ensure_ascii=False),
    )

    return {
        "category": "analyzed",
        "symbol": symbol,
        "name": token.get("name"),
        "alpha_id": alpha_id,
        "chain_name": token.get("chainName"),
        "contract_address": token.get("contractAddress"),
        "price_at_prediction": market_stats.get("current_price") or token.get("price"),
        "market_cap": token.get("marketCap"),
        "liquidity": token.get("liquidity"),
        "volume_24h": token.get("volume24h"),
        "holders": token.get("holders"),
        "source": token.get("source", "binance_alpha"),
        "rejection_reasons": None,
        "top10_holder_concentration_pct": holder_concentration_pct(security_report),
        "opportunity_score": verdict.get("opportunity_score"),
        "risk_score": verdict.get("risk_score"),
        "confidence_score": verdict.get("confidence_score"),
        "earliness_score": verdict.get("earliness_score"),
        "evidence_tier": verdict.get("evidence_tier"),
        "verdict": verdict.get("verdict"),
        "bull_case": bull.get("thesis"),
        "bear_case": bear.get("thesis"),
        "mediator_notes": mediator.get("surviving_conclusion"),
        "key_evidence": json.dumps(verdict.get("key_evidence", []), ensure_ascii=False),
        "main_risks": json.dumps(verdict.get("main_risks", []), ensure_ascii=False),
        "system_note": verdict.get("system_note"),
        "project_explainer": verdict.get("project_explainer"),
        "agent_findings": json.dumps(
            {"analysts": findings, "bull": bull, "bear": bear, "mediator": mediator},
            ensure_ascii=False,
        ),
        "horizon_days": settings.PREDICTION_HORIZON_DAYS,
        "rejection_margins": None,  # solo aplica a descartados
        "mediator_contradictions_count": len(mediator.get("contradictions", []) or []),
    }


def discarded_entry(token: dict, reasons: list[str], margins: list[dict] | None = None) -> dict:
    """Registro ligero (sin research LLM) para un token rechazado por hard filters. Se
    trackea para poder comparar después si el rechazo estuvo justificado. `margins` (Fase 0,
    2026-09-24) guarda, por cada filtro que falló, qué tan cerca estuvo de pasar -- permite
    distinguir en el dashboard "falló por mucho" de "casi pasó los filtros"."""
    return {
        "category": "discarded",
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "alpha_id": token.get("alphaId"),
        "chain_name": token.get("chainName"),
        "contract_address": token.get("contractAddress"),
        "price_at_prediction": token.get("price"),
        "market_cap": token.get("marketCap"),
        "liquidity": token.get("liquidity"),
        "volume_24h": token.get("volume24h"),
        "holders": token.get("holders"),
        "source": token.get("source", "binance_alpha"),
        "rejection_reasons": json.dumps(reasons, ensure_ascii=False),
        "rejection_margins": json.dumps(margins, ensure_ascii=False) if margins else None,
        "top10_holder_concentration_pct": None,  # descartados no pasan por GoPlus
        "opportunity_score": None, "risk_score": None, "confidence_score": None,
        "earliness_score": None, "evidence_tier": None, "verdict": "Discarded (hard filter)",
        "bull_case": None, "bear_case": None, "mediator_notes": None,
        "key_evidence": None, "main_risks": None, "system_note": None, "agent_findings": None,
        "project_explainer": None,
        "mediator_contradictions_count": None,
        "horizon_days": settings.PREDICTION_HORIZON_DAYS,
    }


def _save_entry(run_id: str, data: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO predictions (
                run_id, category, symbol, name, alpha_id, chain_name, contract_address,
                price_at_prediction, market_cap, liquidity, volume_24h, holders, source,
                rejection_reasons, rejection_margins, top10_holder_concentration_pct,
                opportunity_score, risk_score, confidence_score, earliness_score,
                evidence_tier, verdict, bull_case, bear_case, mediator_notes,
                mediator_contradictions_count,
                key_evidence, main_risks, system_note, project_explainer, agent_findings, horizon_days,
                created_at, status
            ) VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?,?,?, ?, ?,?,?,?,?,?, ?, 'pending')
            """,
            (
                run_id, data["category"], data["symbol"], data["name"], data["alpha_id"],
                data["chain_name"], data["contract_address"], data["price_at_prediction"],
                data["market_cap"], data["liquidity"], data["volume_24h"], data["holders"],
                data["source"],
                data["rejection_reasons"], data["rejection_margins"], data["top10_holder_concentration_pct"],
                data["opportunity_score"], data["risk_score"], data["confidence_score"],
                data["earliness_score"], data["evidence_tier"], data["verdict"],
                data["bull_case"], data["bear_case"], data["mediator_notes"],
                data["mediator_contradictions_count"],
                data["key_evidence"], data["main_risks"], data["system_note"],
                data["project_explainer"], data["agent_findings"], data["horizon_days"], _now(),
            ),
        )


def _recently_tracked_symbols(hours: int = 20) -> set[str]:
    """Símbolos ya trackeados (analyzed o discarded) en las últimas `hours` horas, para no
    insertar filas duplicadas cada vez que se le da al botón de actualizar varias veces al día."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM predictions WHERE created_at >= ?", (cutoff,)
        ).fetchall()
    return {r["symbol"] for r in rows}


def _symbols_with_open_analysis() -> set[str]:
    """Símbolos que YA tienen un análisis profundo 'pending' sin resolver (categoría
    'analyzed'). Encontrado en producción: con la sola ventana de `_recently_tracked_symbols`
    (20h) el ranking pre-Earliness re-selecciona casi cada ciclo a los mismos ~10 símbolos con
    mejor volumen/market cap (en 13 dias, 168 analisis fueron solo 36 simbolos distintos --
    algunos re-investigados 12-14 veces). No tiene sentido gastar otras 11 llamadas de Gemini
    en un símbolo cuyo veredicto anterior todavía ni siquiera se terminó de verificar contra
    precio real; mejor esperar a que resuelva (evaluated/unevaluable) y así el research se
    reparte sobre más candidatos distintos en vez de repetir siempre los mismos."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM predictions WHERE category = 'analyzed' AND status = 'pending'"
        ).fetchall()
    return {r["symbol"] for r in rows}


def _permanently_unevaluable_symbols() -> set[str]:
    """Símbolos que ya se confirmó (backtesting.evaluate_due_predictions) que no se les puede
    conseguir precio en Binance Alpha (típicamente porque ya no están listados o cambiaron de
    par). Encontrado en producción: sin este filtro, el ranking los sigue seleccionando como
    'candidatos' día tras día -- gastando 11 llamadas de Gemini por token en algo que nunca se
    podrá verificar contra precio real."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM predictions WHERE status = 'unevaluable'"
        ).fetchall()
    return {r["symbol"] for r in rows}


def run_update_cycle(existing_run_id: str | None = None) -> str:
    """
    Ciclo único de actualización del sistema:
      1. Descubre el universo completo en Binance Alpha.
      2. Aplica hard filters -> separa sobrevivientes de descartados.
      3. Trackea TODOS los tokens vistos (analyzed y discarded) que no se hayan registrado
         en las últimas ~20h, para poder revisar después qué pasó con su precio real.
      4. Corre research profundo (LLM) solo sobre los mejores N sobrevivientes.
      5. Reevalúa (backtesting) cualquier entrada -analizada o descartada- cuyo horizonte
         ya venció, usando precio real de mercado.
      6. Si hay suficiente evidencia evaluada nueva, genera un diagnóstico de auto-corrección.
    Corre sincrónico (pensado para lanzarse desde un background task).
    """
    run_id = existing_run_id or create_run("update")
    try:
        _append_log(run_id, "Descargando universo de tokens de Binance Alpha...")
        universe = binance_alpha.get_alpha_token_list()
        _append_log(run_id, f"Binance Alpha: {len(universe)} tokens.")

        try:
            dex_universe = dexscreener.get_universe("bsc")
        except Exception as e:
            dex_universe = []
            _append_log(run_id, f"DexScreener (BSC) falló, se sigue solo con Binance Alpha: {e}")
        if dex_universe:
            _append_log(run_id, f"DexScreener (BSC, boosteados/nuevos): {len(dex_universe)} tokens adicionales.")
        universe = universe + dex_universe
        _append_log(run_id, f"Universo total combinado: {len(universe)} tokens.")

        already_tracked = _recently_tracked_symbols()
        unevaluable_symbols = _permanently_unevaluable_symbols()
        open_analysis_symbols = _symbols_with_open_analysis()

        survivors, discarded = [], []
        for token in universe:
            ok, reasons, margins = filters.passes_hard_filters_with_margins(token)
            (survivors if ok else discarded).append((token, reasons, margins))

        _append_log(run_id, f"Hard filters: {len(survivors)} sobreviven, {len(discarded)} descartados.")

        new_discarded = 0
        for token, reasons, margins in discarded:
            if token.get("symbol") in already_tracked:
                continue
            try:
                _save_entry(run_id, discarded_entry(token, reasons, margins))
                new_discarded += 1
            except Exception:
                pass
        _append_log(run_id, f"Registrados {new_discarded} descartados nuevos para seguimiento (de {len(discarded)}).")

        excluded_symbols = unevaluable_symbols | open_analysis_symbols
        eligible_survivors = [t for t, _, _ in survivors if t.get("symbol") not in excluded_symbols]
        candidates = filters.rank_candidates(eligible_survivors, settings.MAX_CANDIDATES_PER_RUN)
        candidates = [t for t in candidates if t.get("symbol") not in already_tracked]
        skipped_unevaluable = len([t for t, _, _ in survivors if t.get("symbol") in unevaluable_symbols])
        skipped_open = len([t for t, _, _ in survivors if t.get("symbol") in open_analysis_symbols])
        _append_log(run_id, f"Candidatos nuevos para research profundo: {len(candidates)}."
                    + (f" ({skipped_unevaluable} excluidos por no tener precio disponible en ciclos anteriores.)" if skipped_unevaluable else "")
                    + (f" ({skipped_open} excluidos por ya tener un análisis pendiente de resolver, para diversificar el research en vez de repetir siempre los mismos símbolos.)" if skipped_open else ""))

        consecutive_failures = 0
        for i, token in enumerate(candidates, start=1):
            symbol = token.get("symbol")
            _append_log(run_id, f"[{i}/{len(candidates)}] Investigando {symbol}...")
            try:
                data = research_token(token, run_id=run_id)
                _save_entry(run_id, data)
                _append_log(run_id, f"[{i}/{len(candidates)}] {symbol}: veredicto = {data.get('verdict')}")
                consecutive_failures = 0
                try:
                    notifications.notify_verdict(data)
                except Exception:
                    pass  # una alerta fallida no debe tumbar el ciclo
            except Exception as e:
                _append_log(run_id, f"[{i}/{len(candidates)}] {symbol}: ERROR - {e}")
                if "cuota diaria" in str(e).lower():
                    _append_log(run_id, "Cuota diaria de Gemini agotada: se detiene el research de esta actualización (los descartados ya quedaron registrados).")
                    break
                consecutive_failures += 1
                if consecutive_failures >= CONSECUTIVE_FAILURES_TO_ABORT:
                    _append_log(
                        run_id,
                        f"{consecutive_failures} candidatos seguidos fallaron (no es cuota -- probablemente Gemini caído/con "
                        "alta demanda ahora mismo). Se detiene el research de esta actualización en vez de reintentar los "
                        "restantes durante otra hora sin resultado; los descartados ya quedaron registrados.",
                    )
                    break

        _append_log(run_id, "Revisando resultado real de predicciones y descartes anteriores...")
        from . import backtesting  # import diferido: evita ciclo de importación con pipeline
        evaluated = backtesting.evaluate_due_predictions()
        _append_log(run_id, f"{evaluated} entradas (analizadas + descartadas) evaluadas contra precio real.")

        marked = backtesting.update_current_marks()
        _append_log(run_id, f"{marked} entradas pendientes actualizadas con su precio/retorno actual (mark-to-market).")

        if evaluated > 0:
            from . import diagnosis
            try:
                diag = diagnosis.generate_diagnosis(run_id)
                if diag:
                    _append_log(run_id, "Diagnóstico de auto-corrección generado.")
            except Exception as e:
                _append_log(run_id, f"No se pudo generar diagnóstico: {e}")

        _append_log(run_id, "Ciclo completado.")
        _finish_run(run_id, "done")
    except Exception as e:
        _append_log(run_id, f"ERROR FATAL: {e}")
        _finish_run(run_id, "error")
    return run_id
