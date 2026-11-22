"""Revisa un expediente de una corrida sin publicar su contenido en el log."""
import os
from replay_dossier import replay
from app.db import get_conn

if __name__ == "__main__":
    run = os.environ["CIS_REVIEW_RUN"]
    with get_conn() as db:
        row = db.execute("SELECT token_key FROM research_dossiers WHERE run_id=? ORDER BY token_key LIMIT 1",(run,)).fetchone()
    if not row:
        raise SystemExit("No se encontró un expediente")
    result = replay(run,row["token_key"],use_models=True)["blind_review"]
    print(result["estado"], {k: v.get("error") or v.get("decision") for k,v in result["judges"].items()})
    if result["estado"] == "evaluacion_incompleta":
        raise SystemExit(1)
