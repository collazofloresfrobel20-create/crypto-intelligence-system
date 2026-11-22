"""Revisión fechada de un expediente congelado. Conserva el original para comparaciones."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import blind_review, project_rubric
from app.db import get_conn


def replay(run_id, token_key, assessments=None, use_models=False):
    with get_conn() as conn:
        row = conn.execute("SELECT payload FROM research_dossiers WHERE run_id=? AND token_key=?",
                           (run_id,token_key)).fetchone()
    if row is None:
        raise ValueError("Expediente inexistente")
    raw = row["payload"]
    dossier = json.loads(raw)
    if assessments is not None:
        if not isinstance(assessments,dict):
            raise ValueError("Se esperaba un objeto por criterio")
        for key, item in assessments.items():
            if key not in ("producto","competencia","distribucion","detonador") or not project_rubric.corroborate(dossier,key,item):
                raise ValueError(f"Citas o revisión inválidas en {key}")
        dossier["verified_assessments"] = assessments
    if use_models:
        dossier["blind_review"] = blind_review.evaluate(dossier)
    result = {"project_card":project_rubric.evaluate(dossier),"blind_review":dossier.get("blind_review"),
              "verified_assessments":dossier.get("verified_assessments",{})}
    with get_conn() as conn:
        conn.execute("INSERT INTO dossier_reviews VALUES (?,?,?,?,?)",
                     (run_id,token_key,datetime.now(timezone.utc).isoformat(),hashlib.sha256(raw.encode()).hexdigest(),
                      json.dumps(result,ensure_ascii=False)))
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("run_id")
    cli.add_argument("token_key")
    cli.add_argument("--assessments",type=Path,help="Revisión humana con citas de las fuentes archivadas")
    cli.add_argument("--offline",action="store_true",help="Evaluar solo con hechos guardados, sin llamar modelos")
    args = cli.parse_args()
    print(json.dumps(replay(args.run_id,args.token_key,json.loads(args.assessments.read_text(encoding="utf-8-sig"))
                            if args.assessments else None,not args.offline),ensure_ascii=False,indent=2))
