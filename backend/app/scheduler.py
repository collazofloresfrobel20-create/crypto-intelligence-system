"""
Actualización automática para desarrollo/uso local: sin esto, el sistema solo corre cuando
alguien se acuerda de darle clic al botón. Corre en un hilo de fondo, separado del event loop
de FastAPI (run_update_cycle es una función síncrona/bloqueante).

En producción (con GitHub Actions + Turso + el Worker de Cloudflare desplegados), el cron de
GitHub Actions es quien dispara los ciclos — pon AUTO_UPDATE_ENABLED=false en local para no
correr dos ciclos en paralelo contra la misma base de datos.
"""
import threading
import time
from datetime import datetime, timezone

from . import pipeline, dynamic_config
from .config import settings
from .db import get_conn

_STARTUP_DELAY_SECONDS = 60  # deja que el servidor termine de arrancar antes del primer ciclo


def _has_active_run() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM pipeline_runs WHERE status = 'running'").fetchone()
    return row is not None


def _loop():
    time.sleep(_STARTUP_DELAY_SECONDS)
    while True:
        try:
            dynamic_config.load_dynamic_config()
            if not _has_active_run():
                print(f"[scheduler] {datetime.now(timezone.utc).isoformat()} — iniciando actualización automática")
                pipeline.run_update_cycle()
            else:
                print("[scheduler] ya hay una actualización en curso, se salta este ciclo")
        except Exception as e:
            print(f"[scheduler] error en ciclo automático: {e}")
        time.sleep(max(settings.AUTO_UPDATE_INTERVAL_HOURS, 0.1) * 3600)


def start():
    if not settings.AUTO_UPDATE_ENABLED:
        print("[scheduler] AUTO_UPDATE_ENABLED=false, actualización automática desactivada")
        return
    thread = threading.Thread(target=_loop, daemon=True, name="cis-auto-update")
    thread.start()
    print(f"[scheduler] actualización automática activada cada {settings.AUTO_UPDATE_INTERVAL_HOURS}h")
