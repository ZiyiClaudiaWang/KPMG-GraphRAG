"""Step 1 (optional): download Exhibit 21 from SEC EDGAR for one or more companies.

    export SEC_USER_AGENT="Your Name your.email@school.edu"    # SEC requires a contact
    python fetch_edgar.py AAPL MSFT NVDA --years 3

For each ticker it finds the most recent N Form 10-K filings, downloads the exhibit whose
type is EX-21 / EX-21.1, saves it to data/raw/ as TICKER_FY<year>_<accession>_ex21.htm and
records it in data/manifest.csv. Filings without an EX-21 attachment are listed in
data/fetch_log.csv so nothing disappears silently. Already-downloaded filings are skipped,
so it is safe to re-run.

SEC fair-access rules: identify yourself in the User-Agent and stay under 10 requests/second
(this script pauses between requests).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST_COLS = ["ticker", "cik", "parent_name", "form", "fiscal_year", "period_end",
                 "accession", "source_url", "local_file"]


def get(url: str) -> bytes:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        sys.exit("Set SEC_USER_AGENT to 'Name email' first (SEC requirement).")
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "identity"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(0.15)
                return r.read()
        except Exception as e:  # 429 / transient network
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(url)


class _IndexRows(HTMLParser):
    """Collect (document link, type) rows from an EDGAR '-index.html' page."""

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


def ticker_map() -> dict:
    data = json.loads(get("https://www.sec.gov/files/company_tickers.json"))
    return {v["ticker"].upper(): (str(v["cik_str"]).zfill(10), v["title"]) for v in data.values()}


def ex21_url(cik: str, accession: str) -> str | None:
    nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}/"
    p = _IndexRows()
    p.feed(get(base + f"{accession}-index.html").decode("utf-8", "replace"))
    for cells, href in p.rows:
        # columns: Seq | Description | Document | Type | Size
        if any(re.fullmatch(r"EX-21(\.\d+)?", c.strip(), re.I) for c in cells) and href:
            href = href.split("/ix?doc=")[-1]
            return "https://www.sec.gov" + href if href.startswith("/") else base + href
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--years", type=int, default=3, help="number of most recent 10-Ks")
    a = ap.parse_args()

    manifest = DATA / "manifest.csv"
    existing = []
    if manifest.exists():
        with open(manifest, newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    have = {r["accession"] for r in existing}
    log = []
    tmap = ticker_map()
    (DATA / "raw").mkdir(parents=True, exist_ok=True)

    for t in a.tickers:
        t = t.upper()
        if t not in tmap:
            log.append({"ticker": t, "accession": "", "status": "ticker not found"})
            continue
        cik, title = tmap[t]
        sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
        recent = sub["filings"]["recent"]
        name = sub.get("name") or title
        tenks = [i for i, f in enumerate(recent["form"]) if f == "10-K"][: a.years]
        for i in tenks:
            acc, period = recent["accessionNumber"][i], recent["reportDate"][i]
            if acc in have:
                log.append({"ticker": t, "accession": acc, "status": "already in manifest"})
                continue
            url = ex21_url(cik, acc)
            if not url:
                log.append({"ticker": t, "accession": acc, "status": "no EX-21 attached; check 10-K exhibit index"})
                continue
            fy = period[:4]
            ext = Path(url).suffix or ".htm"
            local = f"raw/{t}_FY{fy}_{acc}_ex21{ext}"
            (DATA / local).write_bytes(get(url))
            existing.append({"ticker": t, "cik": cik, "parent_name": name, "form": "10-K",
                             "fiscal_year": fy, "period_end": period, "accession": acc,
                             "source_url": url, "local_file": local})
            have.add(acc)
            log.append({"ticker": t, "accession": acc, "status": f"downloaded {local}"})

    with open(manifest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLS)
        w.writeheader()
        w.writerows(existing)
    with open(DATA / "fetch_log.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "accession", "status"])
        w.writeheader()
        w.writerows(log)
    for row in log:
        print(row)


if __name__ == "__main__":
    main()
