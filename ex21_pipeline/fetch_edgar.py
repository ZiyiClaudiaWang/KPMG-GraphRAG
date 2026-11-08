"""Step 1 (optional): download Exhibit 21 from SEC EDGAR for one or more companies.

    export SEC_USER_AGENT="Your Name your.email@school.edu"    # SEC requires a contact

    python fetch_edgar.py AAPL MSFT --years 3          # the 3 most recent 10-Ks of each
    python fetch_edgar.py AAPL MSFT --fy 2025          # the FY2025 10-K of each
    python fetch_edgar.py AAPL:2025 MSFT:2024,2026     # chosen years per ticker
    python fetch_edgar.py --from-filings ../extraction_pipeline/config/filings.yaml
                                                       # exactly the 10-K accessions listed there

Ways to choose filings (they can be combined in one command):
  TICKER              the most recent --years 10-Ks (default 3)
  TICKER --fy Y [Y]   the 10-Ks of these fiscal years, for every plain TICKER
  TICKER:Y[,Y]        the 10-Ks of these fiscal years, for this ticker only
  --from-filings F    every 10-K row of a filings.yaml (ticker, cik, accession); 10-Q rows are
                      skipped, since only a 10-K carries Exhibit 21

Fiscal year = the calendar year of the 10-K's period end (SEC "reportDate"), the same rule
the file names use: Apple's 10-K for the year ending 2025-09-27 is FY2025, Nike's for the
year ending 2026-05-31 is FY2026. A requested year with no 10-K is listed in fetch_log.csv.

It downloads the exhibit whose type is EX-21 / EX-21.1 of each chosen 10-K, saves it to
data/raw/ as TICKER_FY<year>_<accession>_ex21.htm and records it in data/manifest.csv.
Filings without an EX-21 attachment are listed in data/fetch_log.csv so nothing disappears silently. Already-downloaded filings are skipped,
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


def ten_k_filings(cik: str, years: set[str] | None = None) -> tuple[str, list[dict]]:
    """Company name and its original 10-K filings, newest first.

    The submissions file lists about the last 1,000 filings; older ones sit in extra pages,
    which are read only when a requested fiscal year is not found in the first page.
    """
    sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    pages = [sub["filings"]["recent"]]
    found = lambda: {f["fiscal_year"] for f in _tenks(pages)}
    for extra in sub["filings"].get("files", []):
        if not years or years <= found():
            break
        pages.append(json.loads(get(f"https://data.sec.gov/submissions/{extra['name']}")))
    return sub.get("name") or "", _tenks(pages)


def _tenks(pages: list[dict]) -> list[dict]:
    out = []
    for page in pages:
        for i, form in enumerate(page["form"]):
            if form != "10-K":                     # 10-K/A is left out on purpose
                continue
            period = page["reportDate"][i] or ""
            out.append({"accession": page["accessionNumber"][i], "period_end": period,
                        "fiscal_year": period[:4]})
    return out


def parse_targets(a, tmap_fn) -> tuple[list[dict], list[dict]]:
    """Turn the command line into (filings to fetch, log rows for what could not be found).

    Each target is {ticker, cik, name, accession, period_end, fiscal_year}.
    """
    targets, log = [], []

    if a.from_filings:
        import yaml                                # only needed for this option
        rows = yaml.safe_load(Path(a.from_filings).read_text(encoding="utf-8"))["filings"]
        for r in rows:
            if str(r.get("form", "")).upper() != "10-K":
                continue
            cik = str(r["cik"]).zfill(10)
            period = str(r.get("period_end", ""))
            targets.append({"ticker": r["ticker"].upper(), "cik": cik, "name": None,
                            "accession": r["accession"], "period_end": period,
                            "fiscal_year": period[:4]})

    if a.tickers:
        tmap = tmap_fn()
        for arg in a.tickers:
            ticker, _, year_list = arg.partition(":")
            ticker = ticker.upper()
            years = [y.strip() for y in year_list.split(",") if y.strip()] or a.fy
            if ticker not in tmap:
                log.append({"ticker": ticker, "accession": "", "status": "ticker not found"})
                continue
            cik, title = tmap[ticker]
            name, tenks = ten_k_filings(cik, set(years) if years else None)
            if years:
                chosen = [f for f in tenks if f["fiscal_year"] in years]
                for y in sorted(set(years) - {f["fiscal_year"] for f in chosen}):
                    log.append({"ticker": ticker, "accession": "",
                                "status": f"no 10-K with fiscal year {y} (period end in {y})"})
            else:
                chosen = tenks[: a.years]
            targets += [{"ticker": ticker, "cik": cik, "name": name or title, **f} for f in chosen]
    return targets, log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", help="TICKER or TICKER:YEAR[,YEAR]")
    ap.add_argument("--years", type=int, default=3, help="number of most recent 10-Ks (when no year is given)")
    ap.add_argument("--fy", nargs="+", metavar="YEAR", help="fiscal years for every plain TICKER")
    ap.add_argument("--from-filings", metavar="YAML", help="fetch the 10-K accessions listed in this filings.yaml")
    a = ap.parse_args()
    if not a.tickers and not a.from_filings:
        ap.error("give at least one TICKER, or --from-filings")

    manifest = DATA / "manifest.csv"
    existing = []
    if manifest.exists():
        with open(manifest, newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    have = {r["accession"] for r in existing}
    (DATA / "raw").mkdir(parents=True, exist_ok=True)

    targets, log = parse_targets(a, ticker_map)
    names = {}
    for t in targets:
        acc = t["accession"]
        if acc in have:
            log.append({"ticker": t["ticker"], "accession": acc, "status": "already in manifest"})
            continue
        if not t["name"]:                          # --from-filings rows carry no company name
            if t["cik"] not in names:
                names[t["cik"]] = json.loads(get(f"https://data.sec.gov/submissions/CIK{t['cik']}.json")).get("name", "")
            t["name"] = names[t["cik"]] or t["ticker"]
        url = ex21_url(t["cik"], acc)
        if not url:
            log.append({"ticker": t["ticker"], "accession": acc, "status": "no EX-21 attached; check 10-K exhibit index"})
            continue
        ext = Path(url).suffix or ".htm"
        local = f"raw/{t['ticker']}_FY{t['fiscal_year']}_{acc}_ex21{ext}"
        (DATA / local).write_bytes(get(url))
        existing.append({"ticker": t["ticker"], "cik": t["cik"], "parent_name": t["name"], "form": "10-K",
                         "fiscal_year": t["fiscal_year"], "period_end": t["period_end"], "accession": acc,
                         "source_url": url, "local_file": local})
        have.add(acc)
        log.append({"ticker": t["ticker"], "accession": acc, "status": f"downloaded {local}"})

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
