"""Expediente en sombra. Cada lectura queda fechada y vinculada al contrato."""
import os
import math
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlsplit

import requests


def read_document(url, headers=None, max_bytes=1_000_000):
    doc = {"url": url, "read_at": datetime.now(timezone.utc).isoformat()}
    try:
        with requests.get(url, headers=headers, timeout=(4, 8), allow_redirects=False, stream=True) as response:
            doc["http_status"] = response.status_code
            if response.status_code != 200:
                return doc
            chunks = bytearray()
            for chunk in response.iter_content(32768):
                chunks.extend(chunk)
                if len(chunks) > max_bytes:
                    return {**doc, "error": "document_too_large"}
            doc["data"] = json.loads(chunks)
    except (requests.RequestException, ValueError) as error:
        doc["error"] = type(error).__name__
    return doc


def protocol_catalog():
    return read_document("https://api.llama.fi/protocols", max_bytes=24_000_000)


def protocol_context(token_key, catalog):
    rows = catalog.get("data")
    if not isinstance(rows, list):
        return {"status": "catalog_unavailable", "metrics": []}
    chain = token_key.partition(":")[0]
    def identity(row):
        address = row.get("address")
        if not isinstance(address, str) or ":" not in address:
            return None
        prefix, contract = address.split(":", 1)
        return prefix.lower() + ":" + (contract.lower() if prefix.lower() == "bsc" else contract)
    def usable(row):
        tvl = row.get("tvl")
        return isinstance(tvl, (int, float)) and math.isfinite(tvl) and tvl >= 0
    matching = [r for r in rows if identity(r) == token_key and usable(r)]
    if not matching:
        return {"status": "no_contract_match", "metrics": []}
    # Un token puede financiar varios productos. No sumar TVL potencialmente solapado.
    project_ids = {r.get("parentProtocol") or r.get("id") for r in matching}
    metrics = []
    for product in sorted(matching, key=lambda r: r["tvl"], reverse=True)[:3]:
        peers = []
        families = set(project_ids)
        required_chain = {"bsc": "Binance", "solana": "Solana"}.get(chain)
        comparable = [r for r in rows if r.get("category") == product.get("category")
                      and required_chain in (r.get("chains") or []) and usable(r)]
        for row in sorted(comparable, key=lambda r: r["tvl"], reverse=True):
            family = row.get("parentProtocol") or row.get("id")
            if not family or family in families or identity(row) == token_key:
                continue
            families.add(family)
            peers.append({"slug": row.get("slug"), "tvl_usd": row["tvl"]})
            if len(peers) == 2:
                break
        metrics.append({"metric": "protocol_tvl_usd", "slug": product.get("slug"),
                        "value": product["tvl"], "source": catalog["url"], "read_at": catalog["read_at"],
                        "provenance": "defillama", "category": product.get("category"),
                        "scope": "all_chains_of_protocol", "peers_by_same_category_and_chain": peers})
    return {"status": "matched", "metrics": metrics}


def collect(token_key):
    chain, _, address = token_key.partition(":")
    platform = {"bsc": "binance-smart-chain", "solana": "solana"}.get(chain)
    if not platform:
        return {"status": "unsupported_chain", "documents": []}
    key = os.getenv("COINGECKO_DEMO_API_KEY")
    metadata = read_document(
        f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{quote(address, safe='')}",
        {"x-cg-demo-api-key": key} if key else None,
    )
    documents = [metadata]
    data = metadata.get("data")
    if not isinstance(data, dict):
        return {"status": "metadata_unavailable", "documents": documents}
    actual = (data.get("platforms") or {}).get(platform) or ""
    if (actual.lower() != address.lower() if chain == "bsc" else actual != address):
        return {"status": "contract_unconfirmed", "documents": documents}
    repos = ((data.get("links") or {}).get("repos_url") or {}).get("github") or []
    for link in repos:
        if not isinstance(link, str):
            continue
        parsed = urlsplit(link)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            continue
        match = re.fullmatch(r"/([\w.-]+)/([\w.-]+)/?", parsed.path, re.ASCII)
        if not match or any(part in (".", "..") for part in match.groups()):
            continue
        org, repo = match.groups()
        release = read_document(f"https://api.github.com/repos/{org}/{repo}/releases/latest")
        documents.append(release)
        since = (datetime.now(timezone.utc)-timedelta(days=90)).isoformat()
        activity = read_document(f"https://api.github.com/repos/{org}/{repo}/commits?per_page=100&since={quote(since)}")
        activity["kind"] = "commits_90d"
        documents.append(activity)
        break
    return {"status": "catalogued", "documents": documents,
            "provenance": "project_supplied_description_and_repository",
            "independent_confirmation": False}

def analyst_facts(dossier):
    """Only typed observations leave the reader. Free-form project text stays in the archive."""
    facts = []
    documents = dossier.get("documents", [])
    if dossier.get("status") != "catalogued" or not documents:
        return dossier.get("protocol", {}).get("metrics", [])
    catalog = documents[0]
    data = catalog.get("data") or {}
    market = data.get("market_data") or {}
    for field in ("circulating_supply", "total_supply", "max_supply"):
        value = market.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
            facts.append({"metric": field, "value": value, "source": catalog["url"],
                          "read_at": catalog["read_at"], "provenance": "catalogue",
                          "updated_at": data.get("last_updated") if is_timestamp(data.get("last_updated")) else None})
    for release in documents[1:]:
        record = release.get("data") or {}
        published = record.get("published_at") if isinstance(record, dict) else None
        if is_timestamp(published) and datetime.fromisoformat(published.replace("Z", "+00:00")) <= datetime.fromisoformat(release["read_at"]):
            facts.append({"metric": "latest_repository_release", "value": published,
                          "source": release["url"], "read_at": release["read_at"],
                          "provenance": "project_repository"})
    return facts + dossier.get("protocol", {}).get("metrics", [])


def is_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False
