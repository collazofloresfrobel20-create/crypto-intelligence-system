import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import matched_controls as m
from app.db import SCHEMA
from scripts.cohort_report import amm_net


class MatchedTests(unittest.TestCase):
    def test_global_assignment_and_order(self):
        # 100 tiene dos opciones; 130 solo puede usar 110.
        picks = [dict(key="a",liquidity=100),dict(key="b",liquidity=130)]
        controls = [dict(key="x",liquidity=50),dict(key="y",liquidity=110)]
        expected = {"a":"x","b":"y"}
        self.assertEqual(m.match_chain(picks,controls),expected)
        self.assertEqual(m.match_chain(picks[::-1],controls[::-1]),expected)
        self.assertEqual(m.match_chain([picks[1]],[controls[0]]),{})

    def test_freeze_does_not_pick_another_arm_or_chain(self):
        def rec(depth,price=1,eligible=True):
            return dict(eligible=eligible,token=dict(liquidity=depth,price=price))
        data=dict(records={"bsc:1":rec(100),"bsc:2":rec(110),"bsc:3":rec(115),
                           "solana:1":rec(100),"solana:2":rec(100,eligible=False)},
                  arms={"bsc:1":"ranked","bsc:2":"random","solana:1":"ranked"})
        rows=m.freeze(data)
        self.assertEqual(sum(r["control"] is not None for r in rows),1)
        matched=next(r for r in rows if r["control"])
        self.assertEqual((matched["pick"]["key"],matched["control"]["key"]),("bsc:2","bsc:3"))
        data["records"]["bsc:3"]["token"]["price"]=None
        self.assertEqual(m.freeze(data)[1]["control"]["key"],"bsc:3")

    def test_common_close_and_retry(self):
        db=sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory=sqlite3.Row
        db.executescript(SCHEMA)
        start=datetime(2026,11,26,tzinfo=timezone.utc)
        def capture(name,hours,tokens,entry=False):
            records={"bsc:"+key:dict(eligible=entry,token=dict(price=p,liquidity=d,symbol=key))
                     for key,(p,d) in tokens.items()}
            raw=dict(event="schedule" if entry else "workflow_dispatch",records=records,
                     arms={"bsc:a":"ranked"} if entry else {})
            db.execute("INSERT INTO shadow_captures VALUES(?,?,?,?,?)",(name,(start+timedelta(hours=hours)).isoformat(),
                "v","complete",json.dumps(raw)))
        capture("entry",0,{"a":(10,100000),"b":(5,101000)},True)
        capture("early",167,{"a":(100,100000),"b":(1,101000)})
        capture("single",168,{"a":(20,100000)})
        capture("other_single",169,{"b":(5,101000)})
        capture("joint",172,{"a":(12,100000),"b":(5,101000)})
        capture("later",180,{"a":(100,100000),"b":(1,101000)})
        def report(hours):
            data=m.report(db,start+timedelta(hours=hours),amm_net)
            return next(r for r in data["filas"] if r["dias"]==7)
        self.assertEqual(report(168)["pendientes"],1)
        result=report(192)
        self.assertEqual(result["medidos"],1)
        self.assertAlmostEqual(result["mediana_diferencia_pp"],amm_net(1.2,100000,100000,100,.003)-amm_net(1,101000,101000,100,.003))
        self.assertEqual(report(240),result)
        row=db.execute("SELECT count(*) n,min(close_run) close_run FROM matched_closes").fetchone()
        self.assertEqual((row["n"],row["close_run"]),(1,"joint"))

    def test_missing_close_and_cost(self):
        db=sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory=sqlite3.Row
        db.executescript(SCHEMA)
        start=datetime(2026,11,26,tzinfo=timezone.utc)
        for name,offset in (("entry",0),("late",9)):
            a={"eligible":offset==0,"token":{"liquidity":100000,"price":1}}
            raw=dict(event="schedule",arms={"bsc:p":"ranked"} if offset==0 else {},
                     records={"bsc:p":a,"bsc:q":a})
            db.execute("INSERT INTO shadow_captures VALUES(?,?,?,?,?)",(name,(start+timedelta(days=offset)).isoformat(),"v","complete",json.dumps(raw)))
        end=start+timedelta(days=10)
        def result():return next(r for r in m.report(db,end,amm_net)["filas"] if r["dias"]==7)
        self.assertEqual(result()["sin_precio"],1)
        raw["records"]["bsc:q"]={"eligible":False,"token":{"price":1,"liquidity":None}}
        db.execute("INSERT INTO shadow_captures VALUES(?,?,?,?,?)",("deferred",(start+timedelta(days=7)).isoformat(),"v","complete",json.dumps(raw)))
        self.assertEqual(result()["sin_costos"],1)
