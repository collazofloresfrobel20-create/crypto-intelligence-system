"""Noticias como pistas. Solo lee dominios de medios definidos en código."""
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import requests


FEEDS = {"www.coindesk.com": "https://www.coindesk.com/arc/outboundfeeds/rss",
         "decrypt.co": "https://decrypt.co/feed"}


def fetch(url):
    with requests.get(url, headers={"User-Agent":"CIS personal research/1.0"}, timeout=(4,8),
                      allow_redirects=False, stream=True) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("non-200 response")
        content = bytearray()
        for chunk in response.iter_content(32768):
            content.extend(chunk)
            if len(content) > 2_000_000:
                raise ValueError("document too large")
        return bytes(content)


def news_index():
    entries, status, seen = [], {}, set()
    for host, feed in FEEDS.items():
        try:
            root = ElementTree.fromstring(fetch(feed))
            count = 0
            for item in root.findall(".//item"):
                parsed = urlsplit((item.findtext("link") or "").strip())
                if parsed.scheme != "https" or parsed.netloc.lower() != host:
                    continue
                url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                title = (item.findtext("title") or "").strip()
                if not title or url in seen:
                    continue
                seen.add(url)
                entries.append(dict(title=title, url=url, publisher=host, published_at=item.findtext("pubDate")))
                count += 1
            status[host] = {"items": count}
        except (ValueError, requests.RequestException, ElementTree.ParseError) as exc:
            status[host] = {"error": type(exc).__name__}
    return {"entries": entries, "feeds": status}


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_paragraph = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.in_paragraph = True

    def handle_endtag(self, tag):
        if tag == "p":
            self.in_paragraph = False
            self.chunks.append("\n")

    def handle_data(self, data):
        if self.in_paragraph:
            self.chunks.append(data)


def investigate(token, index):
    name = str(token.get("name") or "").strip()
    result = {"feed_coverage": index["feeds"], "leads": []}
    if len(name) < 3:
        return result
    pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
    for item in index["entries"]:
        if not pattern.search(item["title"]):
            continue
        lead = dict(item, read_at=datetime.now(timezone.utc).isoformat(), status="headline_only")
        try:
            parser = ArticleText()
            parser.feed(fetch(item["url"]).decode("utf-8", errors="replace"))
            body = "".join(parser.chunks).strip()
            if body:
                lead["text"] = body
                address = token.get("contractAddress") or ""
                flags = re.IGNORECASE if address.lower().startswith("0x") else 0
                contract = re.compile(r"(?<![A-Za-z0-9])" + re.escape(address) + r"(?![A-Za-z0-9])", flags)
                lead["status"] = "contract_mentioned" if address and contract.search(body) else "identity_unconfirmed"
        except (requests.RequestException, ValueError) as exc:
            lead["error"] = type(exc).__name__
        result["leads"].append(lead)
        if len(result["leads"]) >= 2:
            break
    return result
