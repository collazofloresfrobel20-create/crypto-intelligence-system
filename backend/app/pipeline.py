import json
import uuid
from datetime import datetime, timedelta, timezone

from . import binance_alpha, goplus, filters, notifications
from .config import settings
from .db import get_conn
from .market_stats import compute_market_stats
from .agents.research import ALL_ANALYSTS
from .agents.debate import bull_agent, bear_agent, mediator_agent, judge_agent


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


def build_context(token: dict, market_stats: dict, goplus_report: dict | None, holder_history: list[dict]) -> str:
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
        "historial_propio_de_ciclos_anteriores": holder_history or "sin ciclos anteriores registrados todavía para este símbolo",
    }
    return json.dumps(context, ensure_ascii=False, default=str)


def research_token(token: dict, run_id: str | None = None) -> dict:
    """Ejecuta research+debate+juez para un token. Devuelve el dict listo para guardar (category='analyzed')."""
    symbol = token.get("symbol")
    alpha_id = token.get("alphaId")

    market_stats = compute_market_stats(alpha_id) if alpha_id else {"error": "sin alphaId"}
    security_report = goplus.get_token_security(token.get("chainName"), token.get("contractAddress"))
    if security_report is None and run_id:
        _append_log(run_id, f"  aviso: sin reporte de GoPlus para {symbol} tras reintentos "
                              "(el Security Analyst queda sin su evidencia Tier 1 principal).")
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
        "rejection_reasons": None,
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
    }


def discarded_entry(token: dict, reasons: list[str]) -> dict:
    """Registro ligero (sin research LLM) para un token rechazado por hard filters. Se
    trackea para poder comparar después si el rechazo estuvo justificado."""
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
        "rejection_reasons": json.dumps(reasons, ensure_ascii=False),
        "opportunity_score": None, "risk_score": None, "confidence_score": None,
        "earliness_score": None, "evidence_tier": None, "verdict": "Discarded (hard filter)",
        "bull_case": None, "bear_case": None, "mediator_notes": None,
        "key_evidence": None, "main_risks": None, "system_note": None, "agent_findings": None,
        "project_explainer": None,
        "horizon_days": settings.PREDICTION_HORIZON_DAYS,
    }


def _save_entry(run_id: str, data: dict):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO predictions (
                run_id, category, symbol, name, alpha_id, chain_name, contract_address,
                price_at_prediction, market_cap, liquidity, volume_24h, holders, rejection_reasons,
                opportunity_score, risk_score, confidence_score, earliness_score,
                evidence_tier, verdict, bull_case, bear_case, mediator_notes,
                key_evidence, main_risks, system_note, project_explainer, agent_findings, horizon_days,
                created_at, status
            ) VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?, ?, 'pending')
            """,
            (
                run_id, data["category"], data["symbol"], data["name"], data["alpha_id"],
                data["chain_name"], data["contract_address"], data["price_at_prediction"],
                data["market_cap"], data["liquidity"], data["volume_24h"], data["holders"],
                data["rejection_reasons"],
                data["opportunity_score"], data["risk_score"], data["confidence_score"],
                data["earliness_score"], data["evidence_tier"], data["verdict"],
                data["bull_case"], data["bear_case"], data["mediator_notes"],
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
        _append_log(run_id, f"Universo total: {len(universe)} tokens.")

        already_tracked = _recently_tracked_symbols()
        unevaluable_symbols = _permanently_unevaluable_symbols()
        open_analysis_symbols = _symbols_with_open_analysis()

        survivors, discarded = [], []
        for token in universe:
            ok, reasons = filters.passes_hard_filters(token)
            (survivors if ok else discarded).append((token, reasons))

        _append_log(run_id, f"Hard filters: {len(survivors)} sobreviven, {len(discarded)} descartados.")

        new_discarded = 0
        for token, reasons in discarded:
            if token.get("symbol") in already_tracked:
                continue
            try:
                _save_entry(run_id, discarded_entry(token, reasons))
                new_discarded += 1
            except Exception:
                pass
        _append_log(run_id, f"Registrados {new_discarded} descartados nuevos para seguimiento (de {len(discarded)}).")

        excluded_symbols = unevaluable_symbols | open_analysis_symbols
        eligible_survivors = [t for t, _ in survivors if t.get("symbol") not in excluded_symbols]
        candidates = filters.rank_candidates(eligible_survivors, settings.MAX_CANDIDATES_PER_RUN)
        candidates = [t for t in candidates if t.get("symbol") not in already_tracked]
        skipped_unevaluable = len([t for t, _ in survivors if t.get("symbol") in unevaluable_symbols])
        skipped_open = len([t for t, _ in survivors if t.get("symbol") in open_analysis_symbols])
        _append_log(run_id, f"Candidatos nuevos para research profundo: {len(candidates)}."
                    + (f" ({skipped_unevaluable} excluidos por no tener precio disponible en ciclos anteriores.)" if skipped_unevaluable else "")
                    + (f" ({skipped_open} excluidos por ya tener un análisis pendiente de resolver, para diversificar el research en vez de repetir siempre los mismos símbolos.)" if skipped_open else ""))

        for i, token in enumerate(candidates, start=1):
            symbol = token.get("symbol")
            _append_log(run_id, f"[{i}/{len(candidates)}] Investigando {symbol}...")
            try:
                data = research_token(token, run_id=run_id)
                _save_entry(run_id, data)
                _append_log(run_id, f"[{i}/{len(candidates)}] {symbol}: veredicto = {data.get('verdict')}")
                try:
                    notifications.notify_verdict(data)
                except Exception:
                    pass  # una alerta fallida no debe tumbar el ciclo
            except Exception as e:
                _append_log(run_id, f"[{i}/{len(candidates)}] {symbol}: ERROR - {e}")
                if "cuota diaria" in str(e).lower():
                    _append_log(run_id, "Cuota diaria de Gemini agotada: se detiene el research de esta actualización (los descartados ya quedaron registrados).")
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
