"""Pruebas de persistencia, repetición y cobertura con SQLite y fuentes simuladas."""
import json
import sqlite3
import sys
import unittest
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SCHEMA, _MIGRATIONS
from app import filters, project_rubric
from scripts import capture_public as cap, cohort_report as report, replay_filters


class ShadowCaptureTests(unittest.TestCase):
    def test_failure_retry_and_replay(self):
        with TemporaryDirectory() as tmp, ExitStack() as stack:
            path = Path(tmp) / "test.sqlite"

            @contextmanager
            def local():
                db = sqlite3.connect(path)
                db.row_factory = sqlite3.Row
                try:
                    with db:
                        yield db
                finally:
                    db.close()

            with local() as db:
                db.executescript(SCHEMA)
                for sql in _MIGRATIONS:
                    try:
                        db.execute(sql)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column" not in str(exc):
                            raise

            def stub(obj, name, **kwargs):
                return stack.enter_context(patch.object(obj, name, **kwargs))

            for mod in (cap, report):
                stub(mod, "get_conn", new=local)
            stub(cap, "init_db")
            stub(cap.market_regime, "compute_regime", return_value={})
            stub(cap.horizon_tracking, "capture")
            stub(cap.project_evidence, "protocol_catalog", return_value={})
            stub(cap.news_evidence, "news_index", return_value={})
            stub(cap.dexscreener, "get_universe", return_value=[])
            sample = dict(price=1, liquidity=filters.settings.MIN_LIQUIDITY_USD,
                          volume24h=filters.settings.MIN_VOLUME_24H_USD,
                          marketCap=filters.settings.MAX_MARKET_CAP_USD, holders=5000)
            tokens = [dict(sample, chainName=c, contractAddress=str(i)) for c in ("bsc","solana") for i in range(4)]
            tokens.append(dict(sample, chainName="bsc", contractAddress="low", liquidity=1))
            fetch = stub(cap.binance_alpha, "get_alpha_token_list", return_value=tokens)
            calls = []

            def dossier(run, key, *args):
                calls.append(key)
                if len(calls) == 1:
                    raise ValueError("simulated failure")
                with local() as conn:
                    conn.execute("INSERT INTO research_dossiers VALUES (?,?,?,?)", (run,key,"test","{}"))

            stub(cap,"store_dossier", side_effect=dossier)
            with self.assertRaises(RuntimeError):
                cap.capture(run_id="shadow-test")
            self.assertEqual(len(report.inputs()[0]), 0)
            with local() as conn:
                row = conn.execute("SELECT * FROM shadow_captures").fetchone()
                data = json.loads(row["payload"])
                self.assertEqual(row["status"], "pending")
            self.assertEqual(replay_filters.compare(data, {})["entran"], [])
            self.assertEqual(replay_filters.compare(data, {})["salen"], [])
            self.assertEqual(replay_filters.compare(data, {"MIN_LIQUIDITY_USD": 1})["entran"], ["bsc:low"])
            result = cap.capture(run_id="shadow-test")
            self.assertEqual(result["eligible"], 8)
            self.assertEqual(result["dossiers"], 6)
            self.assertEqual(len(report.inputs()[0]), 8)
            self.assertEqual(len(calls), 7)
            self.assertEqual(calls[0], calls[-1])
            self.assertTrue(cap.capture(run_id="shadow-test")["reused"])
            self.assertEqual(fetch.call_count, 1)
            with local() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM analytics_snapshots").fetchone()[0], 1)

    def test_coverage_counts_only_completed_calendar_days(self):
        now = datetime(2026,11,22,12,tzinfo=timezone.utc)
        rows = [dict(status="complete", observed_at=(now-timedelta(days=d,hours=h)).isoformat(),
                     event="schedule",full_quota=1,feeds_ok=1) for d in range(1,15) for h in (0,8)]
        self.assertTrue(report.coverage_status(rows,now)["cumple"])
        rows[0]["event"] = "workflow_dispatch"
        self.assertEqual(report.coverage_status(rows,now)["dias_consecutivos"], 0)

    def test_contradiction_distinguishes_low_from_unknown_use(self):
        dossier = {"verified_assessments":{"producto":{}}, "protocol":{"metrics":[{
            "metric":"protocol_tvl_usd", "value":0, "slug":"p",
            "peers_by_same_category_and_chain":[{"tvl_usd":100},{"tvl_usd":200}]}]}}
        with patch.object(project_rubric, "corroborate", return_value=(75, "producto corroborado")):
            self.assertEqual(project_rubric.evaluate(dossier)["verdict"], "En disputa")
            dossier["protocol"]["metrics"] = []
            self.assertFalse(project_rubric.evaluate(dossier)["contradictions"])


if __name__ == "__main__":
    unittest.main()
