"""Rúbrica experimental de proyecto. Expresa la incertidumbre por peso faltante."""
from math import isfinite

from .project_evidence import analyst_facts

WEIGHTS = {"producto": 20, "uso": 20, "competencia": 15, "distribucion": 15,
           "desarrollo": 10, "seguridad": 10, "detonador": 10}


def numeric(value):
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (ValueError, TypeError):
        return None


def evaluate(dossier):
    rows = {k: {"weight": w, "level": None, "reason": "Evidencia insuficiente"} for k, w in WEIGHTS.items()}
    facts = analyst_facts(dossier)
    def rate(key, level, reason):
        rows[key].update(level=level, reason=reason)
    # La comparación mide adopción relativa del producto más grande documentado.
    products = sorted((f for f in facts if f["metric"] == "protocol_tvl_usd"
                       and numeric(f.get("value")) is not None), key=lambda f: f["value"], reverse=True)
    if products:
        product = products[0]
        peers = product.get("peers_by_same_category_and_chain") or []
        peer_tvls = [numeric(p.get("tvl_usd")) for p in peers]
        if len(peer_tvls) >= 2 and all(x is not None and x > 0 for x in peer_tvls):
            ratio = product["value"] / (sum(peer_tvls) / len(peer_tvls))
            level = sum(ratio >= threshold for threshold in (0.01, 0.1, 0.5, 1)) * 25
            rate("uso", level, f"TVL de {product['slug']} respecto de la media de sus comparables: {ratio:.2f}× (DefiLlama).")
    report = dossier.get("security_at_read") or {}
    vetoes = list(dossier.get("thesis_review", {}).get("vetos", []))
    mint = report.get("mintable")
    if str(report.get("is_mintable")) == "1" or isinstance(mint, dict) and str(mint.get("status")) == "1":
        vetoes.append("Emisión abierta según GoPlus")
    flags = [str(report.get(k)) for k in ("is_honeypot","is_mintable","hidden_owner","is_open_source")]
    taxes = [numeric(report.get(k)) for k in ("buy_tax","sell_tax")]
    if vetoes:
        rate("seguridad", 0, " / ".join(vetoes))
    elif all(v in ("0","1") for v in flags) and all(x is not None and 0 <= x <= 1 for x in taxes):
        level = 25 * sum((flags[0] == "0", flags[1] == "0", flags[2] == "0", flags[3] == "1"))
        rate("seguridad", level, "Cuatro verificaciones del contrato en GoPlus.")
    for doc in dossier.get("documents", []):
        if doc.get("kind") != "commits_90d" or not isinstance(doc.get("data"), list):
            continue
        commits = [c for c in doc["data"] if isinstance(c,dict) and c.get("sha")]
        contributors = {c["author"]["id"] for c in commits if isinstance(c.get("author"),dict)
                        and c["author"].get("type") == "User" and c["author"].get("id")}
        level = 25 * sum((len(commits) >= 1, len(commits) >= 10, len(contributors) >= 2,
                          len(commits) >= 30 and len(contributors) >= 3))
        rate("desarrollo", level, f"GitHub: {len(commits)} commits observados y {len(contributors)} cuentas contribuyentes en 90 días (primera página).")
    # Campos cualitativos: el revisor debe citar hechos corroborados en el expediente.
    for criterion, assessment in dossier.get("verified_assessments", {}).items():
        if criterion in ("producto","competencia","distribucion","detonador"):
            checked = corroborate(dossier, criterion, assessment)
            if checked:
                rate(criterion, *checked)
    covered = sum(r["weight"] for r in rows.values() if r["level"] is not None)
    lower = sum(r["weight"] * (r["level"] or 0) / 100 for r in rows.values())
    score = 100*lower/covered if covered >= 60 else None
    contradictions = []
    if (rows["producto"]["level"] or 0) >= 75 and rows["uso"]["level"] == 0:
        contradictions.append("Producto bien calificado con adopción en el tramo inferior frente a sus comparables.")
    verdict = "Insufficient Evidence"
    consensus = dossier.get("blind_review", {}).get("estado")
    if vetoes or score is not None and score < 35:
        verdict = "Reject"
    elif consensus == "en_disputa" or contradictions:
        verdict = "En disputa"
    elif score is not None:
        if score >= 50 and dossier.get("market_signals", {}).get("price_up_liquidity_down") is True:
            verdict = "High Risk"
        elif (score >= 75 and covered >= 80 and rows["uso"]["level"] and rows["detonador"]["level"]
              and consensus == "requiere_verificacion_de_tesis" and rows["producto"]["level"]):
            verdict = "Strong Opportunity"
        elif score >= 60:
            verdict = "Watchlist"
    return {"dimensions": rows, "coverage": covered, "range": [round(lower,1),round(lower+100-covered,1)],
            "score": round(score, 2) if score is not None else None, "verdict": verdict, "vetoes": vetoes,
            "contradictions": contradictions}


def corroborate(dossier, criterion, item):
    """Revisión explícita y atribuible; dos publicaciones guardadas deben sustentar el juicio."""
    from datetime import datetime
    if not isinstance(item, dict) or type(item.get("level")) is not int or item["level"] not in (0,25,50,75,100):
        return None
    if not all(isinstance(item.get(k),str) and item[k].strip() for k in ("reason","reviewer","observable","invalidation")):
        return None
    articles = {a.get("url"): a for a in dossier.get("news", {}).get("leads", [])
                if a.get("status") == "contract_mentioned" and a.get("text") and a.get("publisher")}
    sources = set()
    for citation in item.get("citations", []):
        if not isinstance(citation, dict):
            return None
        doc = articles.get(citation.get("url"))
        quote = citation.get("quote")
        if not doc or not isinstance(quote,str) or len(quote.strip()) < 20 or quote not in doc["text"]:
            return None
        sources.add(doc["publisher"])
    if len(sources) < 2 or item.get("independence_reviewed") is not True:
        return None
    if criterion == "detonador":
        try:
            due = datetime.fromisoformat(item["due_at"])
            if due <= datetime.fromisoformat(dossier["retrieved_at"]):
                return None
        except (KeyError, ValueError, TypeError):
            return None
    return item["level"], item["reason"]
