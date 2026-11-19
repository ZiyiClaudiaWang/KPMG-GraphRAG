"""Build the fact store for the whole corpus, one filing at a time.

Reads the same filing list the extraction pipeline uses, so the structured arm
and the graph arm describe the same filings. They have been a year apart once
already, when the fact table sat on an FY2025 filing the Data team had never
processed.

    export SEC_USER_AGENT="Your Name you@example.com"
    python3 tools/build_fact_store.py --output facts.duckdb

Fetches each filing's primary document from EDGAR, runs the loader with
--append, and prints a row per filing. A filing that fails is reported and
skipped rather than taking the run down, so one bad document does not cost
the other nineteen.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "structured"))

import fact_loader_v1  # noqa: E402

FILINGS_YAML = ROOT / "extraction_pipeline" / "config" / "filings.yaml"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
# EDGAR asks for no more than ten requests a second. This is well under.
PAUSE_SECONDS = 0.4


def read_filings(path=FILINGS_YAML):
    """Parse the pipeline's filing list without taking a yaml dependency.

    Every entry is one flat line of `key: value` pairs, so a regex is enough
    and the loader keeps working on a bare checkout.
    """
    filings = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line.startswith("- {"):
            continue
        entry = dict(re.findall(r'(\w+):\s*"?([^,"}]+)"?', line))
        if "accession" in entry:
            filings.append({k: v.strip() for k, v in entry.items()})
    return filings


def primary_document(cik, accession, user_agent):
    """The filing's own XBRL document, found from its EDGAR index.

    Named <ticker>-<date>.htm by convention, but the convention is not
    reliable enough to guess, so the index is read.
    """
    folder = f"{ARCHIVES}/{int(cik)}/{accession.replace('-', '')}"
    request = urllib.request.Request(folder + "/", headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=60) as response:
        index = response.read().decode("utf-8", "replace")

    names = re.findall(r'href="[^"]*?/([a-zA-Z0-9][a-zA-Z0-9._-]*\.htm)"', index)
    # The primary document is the one named for a period, not an exhibit.
    candidates = [n for n in names
                  if re.fullmatch(r"[a-z0-9]+-\d{8}\.htm", n)]
    if not candidates:
        candidates = [n for n in names
                      if "-ex" not in n.lower() and "index" not in n.lower()]
    if not candidates:
        raise ValueError(f"no primary document in {folder}")
    return f"{folder}/{candidates[0]}"


def fetch(url, user_agent, into):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=120) as response:
        into.write_bytes(response.read())
    return into


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache", default="/tmp/edgar-cache")
    parser.add_argument("--only", help="comma-separated tickers")
    args = parser.parse_args()

    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        raise SystemExit('Set SEC_USER_AGENT to "Your Name you@example.com".')

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    output = Path(args.output).expanduser()
    if output.exists():
        output.unlink()   # --append builds it up; start from nothing

    filings = read_filings()
    if args.only:
        wanted = {t.strip().upper() for t in args.only.split(",")}
        filings = [f for f in filings if f["ticker"].upper() in wanted]

    print(f"{len(filings)} filings -> {output}\n")
    loaded = failed = 0
    totals = {"raw": 0, "canonical": 0, "review": 0}

    for n, filing in enumerate(filings, 1):
        label = f"{filing['ticker']:5} {filing['form']:5} {filing['accession']}"
        try:
            document = cache / f"{filing['ticker']}_{filing['accession']}.htm"
            if not document.exists():
                fetch(primary_document(filing["cik"], filing["accession"], user_agent),
                      user_agent, document)
                time.sleep(PAUSE_SECONDS)

            metadata = fact_loader_v1.filing_metadata_from_edgar(
                filing["cik"], filing["accession"])
            time.sleep(PAUSE_SECONDS)

            raw, canonical, review = fact_loader_v1.process_filing(
                str(document), filing["cik"], filing["ticker"],
                filing["accession"], metadata["filing_date"])

            fact_loader_v1.save_to_duckdb(
                raw, canonical, review, str(output),
                filing={"accession_number": filing["accession"],
                        "cik": str(filing["cik"]).zfill(10),
                        "ticker": filing["ticker"], **metadata},
                append=True)

            totals["raw"] += len(raw)
            totals["canonical"] += len(canonical)
            totals["review"] += len(review)
            loaded += 1
            print(f"  [{n:2}/{len(filings)}] {label}  "
                  f"{len(raw):5} raw  {len(canonical):5} canonical  "
                  f"{len(review):3} review   {metadata['company_name']}")
        except Exception as exc:
            failed += 1
            print(f"  [{n:2}/{len(filings)}] {label}  FAILED: "
                  f"{type(exc).__name__}: {str(exc)[:90]}")

    print(f"\n{loaded} loaded, {failed} failed")
    print(f"{totals['raw']} raw, {totals['canonical']} canonical, "
          f"{totals['review']} held for review")

    import duckdb
    con = duckdb.connect(str(output), read_only=True)
    version = con.execute(
        "SELECT value FROM store_meta_v1 WHERE key = 'corpus_version'").fetchone()
    print(f"corpus_version: {version[0] if version else 'none'}")
    con.close()


if __name__ == "__main__":
    main()
