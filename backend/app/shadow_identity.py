from .db import get_conn
from . import selection_policy

def _asset_key(chain, address, alpha_id):
    chain = str(chain or "").strip().lower()
    chain = {"bnb": "bsc", "bnb smart chain": "bsc", "bnb chain": "bsc", "56": "bsc",
             "sol": "solana"}.get(chain, chain)
    address = str(address or "").strip()
    if chain and address:
        if address.startswith(("0x", "0X")):
            address = address.lower()
        return f"{chain}:{address}"
    return f"alpha:{alpha_id}" if alpha_id else None

def _token_key(token):
    return _asset_key(token.get("chainName"), token.get("contractAddress"), token.get("alphaId"))

def _market_signals(key, run_id):
    with get_conn() as conn:
        current = conn.execute(
            "SELECT * FROM survivor_observations WHERE token_key=? AND run_id=?", (key, run_id)
        ).fetchone()
        if current is None:
            return {}
        previous = conn.execute(
            "SELECT * FROM survivor_observations WHERE token_key=? AND observed_at<? "
            "ORDER BY observed_at DESC LIMIT 2", (key, current["observed_at"])
        ).fetchall()
    return selection_policy.market_signals([dict(row) for row in reversed(previous)] + [dict(current)])
