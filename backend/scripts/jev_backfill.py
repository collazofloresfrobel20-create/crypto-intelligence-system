"""
Fase 1 de PROPUESTA_JEV_CIS.md -- experimento OFFLINE: etiqueta con tags_v1 (solo nombre/símbolo/
cadena) los tokens de Binance Alpha y guarda las respuestas CRUDAS (todas las probabilidades) en
un archivo LOCAL. No escribe en Turso ni toca producción.

Uso (desde backend/):
    python scripts/jev_backfill.py --limit 10      # prueba corta con la key real
    python scripts/jev_backfill.py                 # los ~680 tokens (~$0.01)
    python scripts/jev_backfill.py --mock          # sin key: respuestas falsas, archivo aparte

Es reanudable: si se interrumpe, al relanzar salta los tokens ya etiquetados.
"""
import argparse
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import binance_alpha, jev_client
from app.jev_questions import TAGS_V1, TAGS_V1_QUESTIONS, NARRATIVE_OPTIONS, build_state
from app.config import settings

OUT_DIR = Path(__file__).resolve().parent.parent / "jev_out"


def _mock_answers(alpha_id: str) -> dict:
    """Respuestas deterministas y FALSAS solo para probar la tubería sin key."""
    rnd = random.Random(int(hashlib.md5(alpha_id.encode()).hexdigest(), 16))
    probs = [rnd.random() for _ in NARRATIVE_OPTIONS]
    total = sum(probs)
    dist = {k: p / total for k, p in zip(NARRATIVE_OPTIONS, probs)}
    top = max(dist, key=dist.get)
    answers = {"narrative": {"type": "choice", "choice": top, "probabilities": dist, "confidence": dist[top]}}
    for q in TAGS_V1_QUESTIONS:
        if q != "narrative":
            answers[q] = {"type": "noul", "noul": rnd.random()}
    return {"model": "MOCK", "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    if not args.mock and not settings.TYPESAFE_API_KEY:
        sys.exit("Falta TYPESAFE_API_KEY en backend/.env (ponla tú, no en el chat). Para probar sin key usa --mock.")

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / (f"{TAGS_V1}.mock.json" if args.mock else f"{TAGS_V1}.json")
    done = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}

    tokens = binance_alpha.get_alpha_token_list()
    todo = [t for t in tokens if t.get("alphaId") not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(tokens)} tokens en Binance Alpha; {len(done)} ya etiquetados; {len(todo)} por etiquetar.")

    failures = 0
    for i, t in enumerate(todo, start=1):
        alpha_id = t.get("alphaId")
        state = build_state(t.get("name"), t.get("symbol"), t.get("chainName"))
        resp = _mock_answers(alpha_id) if args.mock else jev_client.system_one(state, TAGS_V1_QUESTIONS)
        if resp is None:
            failures += 1
            print(f"  [{i}/{len(todo)}] {t.get('symbol')}: sin respuesta (se reintenta al relanzar)")
            if failures >= 10 and not args.mock:
                print("10 fallos: se detiene (revisa la key / el servicio).")
                break
            continue
        done[alpha_id] = {
            "symbol": t.get("symbol"), "name": t.get("name"), "chain": t.get("chainName"),
            "is_stock": bool(t.get("stockState") or t.get("rwaInfo")),
            "question_set": TAGS_V1, "model": resp.get("model"),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "answers": resp["answers"], "usage": resp.get("usage"),
        }
        if i % 25 == 0:
            out_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            print(f"  [{i}/{len(todo)}] guardado parcial")
        if not args.mock:
            time.sleep(0.05)
    out_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    used = sum((v.get("usage") or {}).get("input_tokens") or 0 for v in done.values())
    print(f"Listo: {len(done)} etiquetados, {failures} fallos, {used} tokens de entrada. Archivo: {out_path}")


if __name__ == "__main__":
    main()
