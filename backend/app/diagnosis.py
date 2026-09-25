"""Auto-corrección continua (spec sección 9.3), desacoplada de un piloto de 7 días único:
corre automáticamente al final de cada ciclo de actualización si hubo evaluaciones nuevas."""
import json
import re
from datetime import datetime, timezone

from .config import settings
from .db import get_conn, row_to_dict
from . import backtesting, dynamic_config
from .agents.debate import diagnose_system

MIN_EVALUATIONS_FOR_DIAGNOSIS = 3

_ADJUSTABLE_PARAMS = {
    "MIN_LIQUIDITY_USD", "MIN_VOLUME_24H_USD", "MAX_MARKET_CAP_USD",
    "MIN_HOLDERS", "MAX_LISTING_AGE_DAYS", "MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY",
}


def _recent_evaluations(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE status = 'evaluated' ORDER BY evaluated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def generate_diagnosis(run_id: str | None = None) -> dict | None:
    evaluations = _recent_evaluations()
    if len(evaluations) < MIN_EVALUATIONS_FOR_DIAGNOSIS:
        return None

    analyzed = [e for e in evaluations if e["category"] == "analyzed"]
    discarded = [e for e in evaluations if e["category"] == "discarded"]
    efficacy = backtesting.get_filter_efficacy_stats()

    summary = {
        "current_filters": {
            "MIN_LIQUIDITY_USD": settings.MIN_LIQUIDITY_USD,
            "MIN_VOLUME_24H_USD": settings.MIN_VOLUME_24H_USD,
            "MAX_MARKET_CAP_USD": settings.MAX_MARKET_CAP_USD,
            "MIN_HOLDERS": settings.MIN_HOLDERS,
            "MAX_LISTING_AGE_DAYS": settings.MAX_LISTING_AGE_DAYS,
            "MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY": settings.MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY,
        },
        "filter_efficacy": efficacy,
        "analyzed_predictions": [
            {
                "symbol": p["symbol"], "opportunity_score": p["opportunity_score"],
                "risk_score": p["risk_score"], "confidence_score": p["confidence_score"],
                "earliness_score": p["earliness_score"], "verdict": p["verdict"],
                "max_return_pct": p["max_return_pct"], "max_drawdown_pct": p["max_drawdown_pct"],
                "thesis_result": p["thesis_result"],
            }
            for p in analyzed
        ],
        "discarded_tokens": [
            {
                "symbol": p["symbol"], "rejection_reasons": p["rejection_reasons"],
                "max_return_pct": p["max_return_pct"], "thesis_result": p["thesis_result"],
            }
            for p in discarded
        ],
    }

    diag = diagnose_system(json.dumps(summary, ensure_ascii=False))

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO diagnoses (run_id, created_at, based_on_evaluated, patterns_found,
                                    proposed_adjustments, summary, status)
            VALUES (?, ?, ?, ?, ?, ?, 'proposed')
            """,
            (
                run_id, datetime.now(timezone.utc).isoformat(), len(evaluations),
                json.dumps(diag.get("patterns_found", []), ensure_ascii=False),
                json.dumps(diag.get("proposed_adjustments", []), ensure_ascii=False),
                diag.get("summary"),
            ),
        )
    return diag


def get_latest_diagnosis() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM diagnoses ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row_to_dict(row) if row else None


def get_diagnosis(diagnosis_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM diagnoses WHERE id = ?", (diagnosis_id,)).fetchone()
    return row_to_dict(row) if row else None


def list_diagnoses(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM diagnoses ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def apply_adjustments(diagnosis_id: int, accepted_params: list[str] | None = None) -> dict:
    """
    Aplica los ajustes propuestos (sólo los aceptados por el usuario) guardándolos en
    `system_config` (tabla compartida en Turso/SQLite) — así los ve tanto el próximo ciclo
    de GitHub Actions como el Worker del dashboard, no solo el proceso que los aplicó.
    """
    diag = get_diagnosis(diagnosis_id)
    if not diag or not diag.get("proposed_adjustments"):
        return {"applied": [], "message": "No hay ajustes propuestos para este diagnóstico."}

    proposed = diag["proposed_adjustments"]
    updates = {}
    for adj in proposed:
        param = adj.get("parameter")
        if param not in _ADJUSTABLE_PARAMS:
            continue
        if accepted_params is not None and param not in accepted_params:
            continue
        updates[param] = adj.get("proposed_value")

    applied_values = dynamic_config.save_dynamic_config(updates)
    applied = [{"parameter": k, "new_value": v} for k, v in applied_values.items()]

    with get_conn() as conn:
        conn.execute(
            "UPDATE diagnoses SET status = 'applied', applied_adjustments = ? WHERE id = ?",
            (json.dumps(applied, ensure_ascii=False), diagnosis_id),
        )

    return {"applied": applied}


def rollback_adjustment(diagnosis_id: int) -> dict:
    """
    Revierte los ajustes que un diagnóstico aplicó, devolviendo cada parámetro al valor que
    el propio diagnóstico tenía registrado como 'current_value' al momento de proponerse (Fase
    0c, 2026-09-24). Advertencia real: ese es el valor que el sistema tenía EN ESE MOMENTO --
    si otro ajuste tocó el mismo parámetro después, el rollback no lo sabe y podría no
    restaurar el valor más reciente. `current_value` es texto libre generado por el LLM (puede
    venir como "$50,000" en vez de "50000"), así que nunca se falla en silencio: cada parámetro
    que no se pudo revertir se reporta explícitamente en `failed`, en vez de reusar el
    skip-silencioso de `save_dynamic_config` -- el usuario pulsó un botón esperando un
    resultado concreto.
    """
    diag = get_diagnosis(diagnosis_id)
    if not diag:
        return {"rolled_back": [], "failed": [], "message": "Diagnóstico no encontrado."}
    if diag.get("status") != "applied":
        return {"rolled_back": [], "failed": [],
                "message": "Este diagnóstico no está en estado 'applied', no hay nada que deshacer."}

    applied = diag.get("applied_adjustments") or []
    proposed_by_param = {
        p.get("parameter"): p.get("current_value")
        for p in (diag.get("proposed_adjustments") or [])
    }

    updates = {}
    failed = []
    for item in applied:
        param = item.get("parameter")
        if param not in _ADJUSTABLE_PARAMS:
            failed.append({"parameter": param, "reason": "parámetro ya no es ajustable"})
            continue
        raw_original = proposed_by_param.get(param)
        if raw_original is None:
            failed.append({"parameter": param, "reason": "no se encontró el valor original en este diagnóstico"})
            continue
        cleaned = re.sub(r"[^\d.\-]", "", str(raw_original))
        if not cleaned or cleaned in ("-", ".", "-."):
            failed.append({"parameter": param, "reason": f"no se pudo interpretar '{raw_original}' como número"})
            continue
        updates[param] = cleaned

    applied_values = dynamic_config.save_dynamic_config(updates) if updates else {}
    rolled_back = [{"parameter": k, "restored_value": v} for k, v in applied_values.items()]
    for param in updates:
        if param not in applied_values:
            failed.append({"parameter": param, "reason": "no se pudo aplicar el valor restaurado"})

    if rolled_back:
        with get_conn() as conn:
            conn.execute("UPDATE diagnoses SET status = 'rolled_back' WHERE id = ?", (diagnosis_id,))

    return {"rolled_back": rolled_back, "failed": failed}
