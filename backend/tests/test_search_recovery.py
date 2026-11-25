"""Índice de capturas grandes y recuperación después de una publicación interrumpida."""
import json
import sqlite3
import unittest
from contextlib import nullcontext
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SCHEMA, ensure_search_columns
from scripts import capture_public, cohort_report, index_search


class SearchRecoveryTests(unittest.TestCase):
    def test_partial_retry_and_replay_preserve_first_and_latest(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        # Reproduce el esquema existente antes de incorporar precios al buscador.
        db.execute("DROP TABLE search_archive")
        db.execute("CREATE TABLE search_archive(token_key TEXT,eligible INTEGER,observed_at TEXT,run_id TEXT,"
                   "symbol TEXT,name TEXT,reasons TEXT,PRIMARY KEY(token_key,eligible))")
        db.execute("INSERT INTO search_archive VALUES('bsc:0',0,'2026-11-22','old','A','A','[]')")
        ensure_search_columns(db)
        ensure_search_columns(db)
        for run, date, price, status in (("old","2026-11-22",1,"captured"),("new","2026-11-23",2,"captured"),
                                          ("pending","2026-11-24",3,"pending")):
            records = {"bsc:"+str(n): dict(token={"symbol":"A","price":price},eligible=False,reasons=["liquidez"])
                       for n in range(index_search.BATCH_SIZE + 5)}
            db.execute("INSERT INTO shadow_captures VALUES (?,?,?,?,?)", (run,date,"v",status,json.dumps({"records":records})))
        with patch.object(capture_public,"get_conn",return_value=nullcontext(db)), patch.object(cohort_report,"publish_snapshot") as publish:
            publish.side_effect=RuntimeError("publicación interrumpida")
            with self.assertRaises(RuntimeError):
                capture_public.publish_captures("new")
            self.assertEqual(db.execute("SELECT count(*) FROM shadow_captures WHERE status='captured'").fetchone()[0],2)
            publish.side_effect=None
            capture_public.publish_captures("new")
        index_search.index_capture(db,"old")
        count,first,last = db.execute("SELECT count(*), min(first_price), max(last_price) FROM search_archive").fetchone()
        self.assertEqual((count,first,last),(index_search.BATCH_SIZE+5,1,2))
        self.assertEqual(db.execute("SELECT count(*) FROM shadow_captures WHERE status='complete'").fetchone()[0],2)
        self.assertEqual(db.execute("SELECT status FROM shadow_captures WHERE run_id='pending'").fetchone()[0],"pending")

    def test_network_coverage_uses_distinct_contracts_and_scheduled_dossiers(self):
        from datetime import datetime, timezone
        now = datetime(2026,11,25,tzinfo=timezone.utc)
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        stamp = now.isoformat()
        for run, event in (("first","schedule"),("repeat","schedule"),("extra","workflow_dispatch")):
            db.execute("INSERT INTO shadow_captures VALUES (?,?,?,?,?)",(run,stamp,"v","complete",json.dumps({"event":event})))
            for key in ("bsc:1","solana:A"):
                db.execute("INSERT INTO survivor_observations (run_id,token_key,observed_at,eligible,shortlisted) VALUES (?,?,?,1,1)",(run,key,stamp))
        for run,key in (("repeat","bsc:1"),("extra","solana:A")):
            db.execute("INSERT INTO research_dossiers VALUES (?,?,?,?)",(run,key,stamp,"{}"))
        rows = cohort_report.research_coverage(db,now)
        self.assertEqual([(r["elegibles"],r["investigados"]) for r in rows],[(1,1),(1,0)])
