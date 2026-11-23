"""Estudio registrado antes de la ventana de observación."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import volume_study as study


class VolumeStudyTests(unittest.TestCase):
    def test_observation_window_and_missing_closes(self):
        def row(key, day, price):
            return dict(token_key=key, run_id="shadow-a", eligible=1, config_hash="v", price=price,
                        liquidity=100000, volume_24h=200000, observed_at=(study.START+timedelta(days=day)).isoformat())
        rows=[row("bsc:a",0,10),row("bsc:a",7,8),row("bsc:missing",0,10),
              row("solana:old",-1,5),row("solana:old",1,10),row("solana:late",60,10)]
        result=study.evaluate(rows,[],study.FINAL)
        self.assertEqual((result["medidos"],result["sin_cierre"],result["estado"]),(1,1,"evidencia_insuficiente"))
        self.assertEqual(study.evaluate(rows,[],study.START+timedelta(days=3))["inmaduros"],2)
        self.assertEqual(study.evaluate(rows,[],study.FINAL+timedelta(days=12)),result)

    def test_adjustment_and_missingness(self):
        group=[(("bsc","v",1),0,i,i>4) for i in range(10)]
        group[0]=((*group[0][:3],True))
        group[-1]=((*group[-1][:3],None))
        ratio,auc=study.strata_metrics(group)
        self.assertEqual(ratio,2)
        self.assertGreater(auc,.5)
        self.assertLess(study.strata_metrics(group,stress=True)[0],ratio)
        self.assertEqual(study.strata_metrics([(("bsc","v",1),0,1,i%2==0) for i in range(20)]),(None,None))
