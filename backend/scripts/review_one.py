"""Evalúa exactamente el expediente solicitado y registra la finalización de la tarea."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.replay_dossier import replay
from app.db import get_conn


def run_review(request_id=None, run=None):
    with get_conn() as db:
        if request_id:
            row=db.execute("SELECT run_id,token_key,status FROM judge_requests WHERE id=?",(request_id,)).fetchone()
            if not row: raise ValueError("Solicitud inexistente")
            if row["status"]=="done": return
            run=row["run_id"]
            db.execute("UPDATE judge_requests SET status='running' WHERE id=?",(request_id,))
        else:
            row=db.execute("SELECT token_key FROM research_dossiers WHERE run_id=? ORDER BY token_key LIMIT 2",(run,)).fetchall()
            if len(row)!=1: raise ValueError("Selecciona un expediente específico desde el portal")
            row=row[0]
    status="error"
    try:
        result=replay(run,row["token_key"],use_models=True)["blind_review"]
        print(result["estado"],{k:v.get("error") or v.get("decision") for k,v in result["judges"].items()})
        if result["estado"]=="evaluacion_incompleta": raise RuntimeError("Evaluación incompleta")
        status="done"
    finally:
        if request_id:
            with get_conn() as db:
                db.execute("UPDATE judge_requests SET status=? WHERE id=?",(status,request_id))


if __name__=="__main__":
    run_review(os.getenv("CIS_REVIEW_ID"),os.getenv("CIS_REVIEW_RUN"))
