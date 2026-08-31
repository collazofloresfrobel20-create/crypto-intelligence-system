"""
Punto de entrada para GitHub Actions: corre UN ciclo de actualizacion y termina. GitHub
Actions dispara este script por cron (o manualmente via workflow_dispatch, que el boton
"Actualizar sistema" del Worker de Cloudflare activa a traves de la API de GitHub).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db
from app.dynamic_config import load_dynamic_config
from app.pipeline import run_update_cycle

if __name__ == "__main__":
    init_db()
    load_dynamic_config()
    run_id = run_update_cycle()
    print(f"Ciclo completado: {run_id}")
