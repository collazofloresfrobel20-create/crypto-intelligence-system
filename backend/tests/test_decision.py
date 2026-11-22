"""Pruebas de la rúbrica y métricas prospectivas sin servicios externos."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import project_rubric,blind_review
from scripts.cohort_report import amm_net

class DecisionTests(unittest.TestCase):
    def test_empty_dossier_has_no_rating(self):
        card=project_rubric.evaluate({})
        self.assertIsNone(card["score"])
        self.assertEqual(card["range"],[0,100])
        self.assertEqual(card["verdict"],"Insufficient Evidence")

    def test_open_mint_veto_even_without_score(self):
        for report in ({"is_mintable":"1"},{"mintable":{"status":"1"}}):
            self.assertEqual(project_rubric.evaluate({"security_at_read":report})["verdict"],"Reject")

    def test_exact_quotes_and_expiry(self):
        q="Un hecho contrastado con evidencia en la fecha consignada."
        docs=[dict(url=f"https://{host}/nota",publisher=host,status="contract_mentioned",text=q) for host in ("a.test","b.test")]
        dossier={"news":{"leads":docs},"retrieved_at":"2026-11-22T00:00:00+00:00"}
        item=dict(level=75,reason="razón",reviewer="revisor",observable="hito",invalidation="cancelado",
                  independence_reviewed=True,due_at="2026-12-10T00:00:00+00:00",
                  citations=[dict(url=d["url"],quote=q) for d in docs])
        self.assertIsNotNone(project_rubric.corroborate(dossier,"detonador",item))
        item["due_at"]=dossier["retrieved_at"]
        self.assertIsNone(project_rubric.corroborate(dossier,"detonador",item))
        item["citations"][0]["quote"]="cita inventada que no está en el documento"
        self.assertIsNone(project_rubric.corroborate(dossier,"producto",item))

    def test_execution_costs(self):
        self.assertLess(amm_net(1,20000,20000,100,.003),0)
        self.assertLess(amm_net(1,2000,2000,100,.003),amm_net(1,20000,20000,100,.003))
        self.assertIsNone(amm_net(1,20000,None,100,.003))

    def test_no_judges_without_evidence(self):
        def forbidden(_):
            self.fail("No debe consumir cuota")
        self.assertEqual(blind_review.evaluate({},{"test":forbidden})["estado"],"evidencia_insuficiente")

if __name__=="__main__":
    unittest.main()
