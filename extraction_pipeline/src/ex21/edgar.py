"""SEC EDGAR client for Exhibit 21: find a 10-K's EX-21 document and download it.

Library code: imports nothing from the pipeline. A change to SEC's site touches only this file.

SEC requires a contact on every request: set SEC_USER_AGENT="Name email" in the environment
(the pipeline also reads it from extraction_pipeline/.env before calling this module).
SEC allows 10 requests a second; get() waits 0.15 s after each request.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from html.parser import HTMLParser

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SUBMISSIONS = "https://data.sec.gov/submissions"


class MissingUserAgent(RuntimeError):
    """SEC_USER_AGENT is not set, so no request is sent."""


def user_agent() -> str | None:
    return os.environ.get("SEC_USER_AGENT") or None


def get(url: str) -> bytes:
    ua = user_agent()
    if not ua:
        raise MissingUserAgent("Set SEC_USER_AGENT='Name email' (SEC requires a contact)")
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(0.15)
                return r.read()
        except Exception:                  # 429 / transient network
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(url)


class _IndexRows(HTMLParser):
    """(cells, link) rows of an EDGAR '-index.html' page."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._href, self._in_td = [], [], "", None, False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row, self._href = [], None
        elif tag == "td":
            self._in_td, self._cell = True, ""
        elif tag == "a" and self._in_td:
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "td":
            self._row.append(self._cell.strip())
            self._in_td = False
        elif tag == "tr" and self._row:
            self.rows.append((self._row, self._href))

    def handle_data(self, data):
        if self._in_td:
            self._cell += data


def ex21_url(cik: str, accession: str) -> str | None:
    """Link to the filing's EX-21 / EX-21.1 document, or None when it has none."""
    base = f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}/"
    p = _IndexRows()
    p.feed(get(base + f"{accession}-index.html").decode("utf-8", "replace"))
    for cells, href in p.rows:
        if href and any(re.fullmatch(r"EX-21(\.\d+)?", c.strip(), re.I) for c in cells):
            href = href.split("/ix?doc=")[-1]
            return "https://www.sec.gov" + href if href.startswith("/") else base + href
    return None


def registrant_name(cik: str) -> str:
    """EDGAR's conformed name ("MCKESSON CORP"). Used only to recognise a row that is the
    registrant itself; it is not the company's proper name and is not loaded into the graph."""
    return json.loads(get(f"{SUBMISSIONS}/CIK{str(cik).zfill(10)}.json")).get("name") or ""
