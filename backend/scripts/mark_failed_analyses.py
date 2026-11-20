"""
Marca como category='analyzed_failed' los análisis cuyo veredicto se emitió con menos de
MIN_ANALYSTS_OK analistas respondiendo (bug de cuota de Gemini, ver PLAN_V3_CIS.md, A1).

No borra nada: todas las consultas que filtran category='analyzed' (estadísticas, calibración,
dashboard, bloqueo de 7 días) dejan de verlos, y siguen evaluándose contra precio real como
control "sin IA". Reversible con:
  UPDATE predictions SET category='analyzed' WHERE category='analyzed_failed'

Uso:  python scripts/mark_failed_analyses.py            (solo cuenta)
      python scripts/mark_failed_analyses.py --apply    (escribe)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.db import get_conn  # noqa: E402
from app.pipeline import MIN_ANALYSTS_OK  # noqa: E402


def analysts_ok(agent_findings: str | None) -> int:
    try:
        analysts = (json.loads(agent_findings or "{}") or {}).get("analysts") or {}
    except (TypeError, ValueError):
        return 0
    return sum(1 for f in analysts.values() if isinstance(f, dict) and "error" not in f)


def main(apply: bool) -> None:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, verdict, agent_findings FROM predictions WHERE category = 'analyzed'"
        ).fetchall()]
    failed = [r for r in rows if analysts_ok(r["agent_findings"]) < MIN_ANALYSTS_OK]
    by_verdict: dict[str, int] = {}
    for r in failed:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    print(f"Analizados: {len(rows)}. Con menos de {MIN_ANALYSTS_OK}/7 analistas: {len(failed)} {by_verdict}")
    if not apply or not failed:
        print("Nada escrito (usa --apply)." if not apply else "Nada que marcar.")
        return
    ids = [r["id"] for r in failed]
    with get_conn() as conn:
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            conn.execute(
                f"UPDATE predictions SET category = 'analyzed_failed' WHERE id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
    print(f"Marcadas {len(ids)} filas como analyzed_failed.")


if __name__ == "__main__":
    main("--apply" in sys.argv)
