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
        docs=[dict(url=f"https://{host}/nota",publisher=host,status="contract_mentioned",
                   text=f"{host} publica su hallazgo. {q}",published_at="Fri, 20 Nov 2026 13:00:00 GMT",
                   read_at="2026-11-21T00:00:00+00:00") for host in ("a.test","b.test")]
        dossier={"news":{"leads":docs},"retrieved_at":"2026-11-22T00:00:00+00:00"}
        item=dict(level=75,reason="razón",reviewer="revisor",observable="hito",invalidation="cancelado",
                  independence_reviewed=True,due_at="2026-12-10T00:00:00+00:00",
                  citations=[dict(url=d["url"],quote=d["text"]) for d in docs])
        self.assertIsNotNone(project_rubric.corroborate(dossier,"detonador",item))
        duplicate=dict(item,citations=[dict(url=d["url"],quote=q) for d in docs])
        self.assertIsNone(project_rubric.corroborate(dossier,"producto",duplicate))
        docs[0]["published_at"]="2027-01-01T00:00:00+00:00"
        self.assertIsNone(project_rubric.corroborate(dossier,"producto",item))
        docs[0]["published_at"]="Fri, 20 Nov 2026 13:00:00 GMT"
        item["due_at"]=dossier["retrieved_at"]
        self.assertIsNone(project_rubric.corroborate(dossier,"detonador",item))
        item["citations"][0]["quote"]="cita inventada que no está en el documento"
        self.assertIsNone(project_rubric.corroborate(dossier,"producto",item))

    def test_news_reads_two_publishers_and_ignores_future_news(self):
        from app.news_evidence import matching_articles, publication_time
        import re
        entries=[dict(title="Coin", publisher=host, published_at=f"2026-11-{day}T00:00:00Z")
                 for host,day in [("a",18),("a",21),("b",19),("c",25)]]
        picked=matching_articles(entries,re.compile("Coin"),publication_time("2026-11-22T00:00:00Z"))
        self.assertEqual(picked,[entries[1],entries[2]])

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
