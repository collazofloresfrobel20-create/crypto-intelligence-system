"""
Reentrenamiento del clasificador ML de pre-filtro (Fase 1, 2026-09-24). Se ejecuta en CADA
corrida del workflow de GitHub Actions (cada 12h), pero no reentrena de verdad salvo que hayan
pasado al menos RETRAIN_INTERVAL_DAYS desde el último entrenamiento -- el guard vive aquí adentro
en vez de en el cron, tal como se pidió: mismo cron de siempre, un paso nuevo antes del ciclo
principal, que la mayoría de las veces simplemente no hace nada.

También reentrena la calibración del confidence_score (Fase 3) en el mismo paso, reusando el
mismo guard de frecuencia y la misma tabla `ml_models` (kind='calibration').
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db
from app.dynamic_config import load_dynamic_config
from app.config import settings
from app import ml_scoring

RETRAIN_INTERVAL_DAYS = 6


def _days_since(iso_ts: str) -> float:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _due(kind: str) -> bool:
    meta = ml_scoring.latest_model_meta(kind)
    if not meta:
        return True
    age_days = _days_since(meta["trained_at"])
    if age_days < RETRAIN_INTERVAL_DAYS:
        print(f"[{kind}] reentrenado hace {age_days:.1f} días (< {RETRAIN_INTERVAL_DAYS}): no toca todavía.")
        return False
    return True


def retrain_selector():
    if not _due("selector"):
        return

    train_rows, val_rows, shadow_rows = ml_scoring.build_training_set()
    n_total = len(train_rows) + len(val_rows) + len(shadow_rows)

    if n_total < settings.MIN_SAMPLES_FOR_ML:
        print(f"[selector] Solo {n_total} casos evaluados con features completas "
              f"(< {settings.MIN_SAMPLES_FOR_ML} mínimo): no se entrena todavía.")
        return

    if not ml_scoring.has_both_classes(train_rows):
        print("[selector] El conjunto de entrenamiento no tiene ambas clases todavía "
              "(todo éxito o todo fracaso): no se entrena.")
        return

    model, metrics = ml_scoring.train_model(train_rows, val_rows)

    if shadow_rows:
        bench = ml_scoring.benchmark_against_heuristic(shadow_rows, model)
        metrics["benchmark_vs_heuristic"] = bench
        print(f"[selector] Benchmark vs. heurística actual: {bench}")

    ml_scoring.save_model(model, "selector", n_total, metrics)
    print(f"[selector] Modelo reentrenado con {n_total} casos evaluados. Métricas: {metrics}")


def retrain_calibration():
    """Fase 3: curva de calibración del confidence_score, misma tabla ml_models, mismo guard
    de frecuencia. Implementación completa en la Fase 3 del plan -- este bloque queda listo
    para conectarse ahí sin tener que volver a tocar el workflow ni el guard de reentrenamiento."""
    if not _due("calibration"):
        return
    print("[calibration] Fase 3 todavía no implementada -- nada que entrenar por ahora.")


if __name__ == "__main__":
    init_db()
    load_dynamic_config()
    retrain_selector()
    retrain_calibration()
