"""Investiga un contrato explícito sin introducirlo en el grupo de comparación."""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app import shadow_dexscreener as dexscreener, news_evidence, project_evidence
from app.db import get_conn
from scripts.capture_public import store_dossier

def normalize(key):
    chain,_,address = key.strip().partition(":")
    if chain == "bsc" and re.fullmatch(r"0x[0-9a-fA-F]{40}",address):
        return chain+":"+address.lower()
    if chain=="solana" and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}",address):
        return key.strip()
    raise ValueError("Contrato o cadena inválidos")

def process(request_id):
    with get_conn() as c:
        row=c.execute("SELECT * FROM research_requests WHERE id=?",(request_id,)).fetchone()
        if not row: raise ValueError("Solicitud inexistente")
        if row["status"]=="done": return
        key=normalize(row["token_key"])
        c.execute("UPDATE research_requests SET status='running',error=NULL WHERE id=?",(request_id,))
    run="request-"+request_id
    try:
        chain,addr=key.split(":",1)
        pairs=dexscreener._get_pairs_batch(chain,[addr])
        pair=dexscreener.best_pair(pairs,chain,addr)
        if not pair: raise ValueError("Sin mercado verificable para este contrato")
        token=dexscreener._to_token_dict(pair)
        stamp=datetime.now(timezone.utc).isoformat()
        with get_conn() as c:
            c.execute("INSERT OR IGNORE INTO survivor_observations (run_id,token_key,observed_at,symbol,source,price,liquidity,volume_24h,holders,eligible,shortlisted) "
                      "VALUES(?,?,?,?,?,?,?,?,?,0,0)", (run,key,stamp,token.get("symbol"),"consulta_manual",token.get("price"),
                      token.get("liquidity"),token.get("volume24h"),token.get("holders")))
            existing=c.execute("SELECT 1 FROM research_dossiers WHERE run_id=? AND token_key=?",(run,key)).fetchone()
        if not existing:
            store_dossier(run,key,token,project_evidence.protocol_catalog(),news_evidence.news_index())
        with get_conn() as c:
            c.execute("UPDATE research_requests SET status='done',result_run=? WHERE id=?",(run,request_id))
    except Exception as error:
        message = str(error) if isinstance(error,ValueError) else type(error).__name__
        with get_conn() as c:
            c.execute("UPDATE research_requests SET status='error',error=? WHERE id=?",(message[:180],request_id))
        raise RuntimeError("Consulta incompleta") from None

if __name__=="__main__":
    import os
    process(os.environ["CIS_REQUEST_ID"])
