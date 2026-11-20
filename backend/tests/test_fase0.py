"""
Verificaciones de la Fase 0 (Plan v3). Sin red ni base de producción: usa SQLite temporal y
dobles de prueba. Correr desde backend/:  python tests/test_fase0.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["TURSO_DATABASE_URL"] = ""  # nunca tocar producción
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import gemini_client, goplus, pipeline  # noqa: E402
from app.config import settings  # noqa: E402
from app import db  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402

assert not db.TURSO_DATABASE_URL, "las pruebas deben correr contra SQLite local"
init_db()


def test_quota_is_per_key_and_model():
    settings.GEMINI_API_KEY, settings.GEMINI_SMART_API_KEYS = "k1", ["k2"]
    gemini_client._exhausted_keys.clear()
    gemini_client._exhausted_keys.add(("k1", settings.GEMINI_MODEL_SMART))
    lite_pool = [k for k in gemini_client._key_pool(settings.GEMINI_MODEL_FAST)
                 if (k, settings.GEMINI_MODEL_FAST) not in gemini_client._exhausted_keys]
    assert lite_pool == ["k1", "k2"], lite_pool  # flash agotado no deja sin llaves a flash-lite


def test_no_verdict_without_analysts():
    def broken(_ctx):
        raise RuntimeError("Todas las API keys disponibles agotaron su cuota diaria para 'x'")
    saved, judged = pipeline.ALL_ANALYSTS.copy(), []
    pipeline.ALL_ANALYSTS.clear()
    pipeline.ALL_ANALYSTS.update({f"a{i}": broken for i in range(7)})
    orig_judge, pipeline.judge_agent = pipeline.judge_agent, lambda *a, **k: judged.append(1)
    try:
        token = {"symbol": "TST", "source": "dexscreener", "contractAddress": None}
        pipeline.research_token(token, security_report=None, market_stats={"current_price": 1})
        raise AssertionError("debió abortar")
    except RuntimeError as e:
        assert "cuota diaria" in str(e) and "0/7" in str(e), e  # el ciclo detecta cuota y se detiene
    finally:
        pipeline.ALL_ANALYSTS.clear()
        pipeline.ALL_ANALYSTS.update(saved)
        pipeline.judge_agent = orig_judge
    assert not judged


def test_goplus_retries_rate_limit():
    calls = []

    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake_get(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return Resp({"code": 4029, "message": "too many requests", "result": {}})
        return Resp({"code": 1, "result": {"0xabc": {"is_honeypot": "0"}}})

    orig_get, orig_sleep = goplus._session.get, goplus.time.sleep
    goplus._session.get, goplus.time.sleep = fake_get, lambda s: None
    try:
        assert goplus.get_token_security("bsc", "0xABC") == {"is_honeypot": "0"}
    finally:
        goplus._session.get, goplus.time.sleep = orig_get, orig_sleep
    assert len(calls) == 2


def test_lp_lock_uses_lp_holders():
    report = {
        "is_honeypot": "0",
        "holders": [{"percent": "0.9", "is_locked": 1}],  # supply bloqueado: NO es liquidez
        "lp_holders": [{"percent": "0.6", "is_locked": 1}, {"percent": "0.4", "is_locked": 0}],
    }
    assert goplus.extract_security_features(report)["lp_holders_locked_pct"] == 60.0
    assert goplus.extract_security_features({"is_honeypot": "0"})["lp_holders_locked_pct"] is None


def test_holder_history_latest_and_by_contract():
    with get_conn() as conn:
        for i in range(10):
            conn.execute(
                "INSERT INTO predictions (run_id, symbol, contract_address, holders, created_at) "
                "VALUES ('r', 'BOB', '0xreal', ?, ?)", (i, f"2026-11-{10 + i:02d}"))
        conn.execute("INSERT INTO predictions (run_id, symbol, contract_address, holders, created_at) "
                     "VALUES ('r', 'BOB', '0xclon', 999, '2026-11-25')")
    hist = pipeline.get_holder_history("BOB", limit=3, contract_address="0xreal")
    assert [h["holders"] for h in hist] == [7, 8, 9], hist  # las 3 más recientes, en orden


def test_stale_runs_closed():
    with get_conn() as conn:
        conn.execute("INSERT INTO pipeline_runs (id, started_at, status) VALUES ('old', '2026-01-01T00:00:00+00:00', 'running')")
    pipeline.create_run()
    with get_conn() as conn:
        assert conn.execute("SELECT status FROM pipeline_runs WHERE id='old'").fetchone()["status"] == "error"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
