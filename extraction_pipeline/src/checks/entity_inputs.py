"""Check the inputs to entity linking on real data, before any relabelling or load. No OpenAI calls.

Kelly's open points on #87 / #92: the 233-filing subsidiaries have not been checked for
repeats and review, and some companies seem to have no segments in the fact store. This
report reads what steps 6a and 6b wrote and says where to look; it changes nothing.

Input : data/output/subsidiaries.jsonl; data/checks/subsidiaries/subsidiary_qa.csv, subsidiary_renames.csv (6a)
        data/entity_catalog.json                                                (step 6b)
        config/filings.yaml
Output: printed summary, and in data/checks/entity_inputs/
          by_filing.csv            per filing: subsidiaries and segments it is offered
          subsidiaries_by_year.csv per company and 10-K: rows, and change from the 10-K before
          repeats.csv              the same subsidiary twice in one 10-K (a parser repeat), or
                                   several legal entities with one core name in one 10-K
                                   ("Example Asia LLC" and "Example Asia Limited"), which step 9
                                   can only report as ambiguous_catalog_match
          segments_by_company.csv  every segment, with the first and last period it is offered

Usage:
  python -m src.checks.entity_inputs
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict

from ..common import Paths, checks_dir, filings, log, read_jsonl, write_csv

STEP = "report_entity_inputs"
BIG_CHANGE = 0.5        # a 10-K whose row count moves by more than half from the year before


def _read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def subsidiaries_by_year(rows: list[dict]) -> list[dict]:
    per = Counter((r["parent_ticker"], r["reporting_period_end"], r["accession_number"]) for r in rows)
    out, last = [], {}
    for (ticker, period, acc), n in sorted(per.items()):
        before = last.get(ticker)
        change = "" if before is None else round((n - before) / before, 2)
        out.append({"ticker": ticker, "reporting_period_end": period, "accession": acc, "rows": n,
                    "rows_10k_before": "" if before is None else before, "change": change,
                    "flag": "BIG CHANGE" if change != "" and abs(change) > BIG_CHANGE else ""})
        last[ticker] = n
    return out


def repeats(rows: list[dict]) -> list[dict]:
    out = []
    by_acc_sub = Counter((r["accession_number"], r["sub_id"]) for r in rows)
    for (acc, sub), n in by_acc_sub.items():
        if n > 1:
            r = next(x for x in rows if x["accession_number"] == acc and x["sub_id"] == sub)
            out.append({"kind": "same sub_id twice in one 10-K", "ticker": r["parent_ticker"],
                        "accession": acc, "name": r["name"], "count": n, "sub_ids": sub})
    by_acc_key = defaultdict(set)
    for r in rows:
        by_acc_key[(r["parent_ticker"], r["accession_number"], r["entity_key"])].add((r["sub_id"], r["name"]))
    for (ticker, acc, key), subs in by_acc_key.items():
        if len(subs) > 1:
            out.append({"kind": "same core name, several legal entities in one 10-K (a mention of it is ambiguous)", "ticker": ticker,
                        "accession": acc, "name": " | ".join(sorted(n for _, n in subs)),
                        "count": len(subs), "sub_ids": " ".join(sorted(s for s, _ in subs))})
    return out


def by_filing(catalog: list[dict]) -> list[dict]:
    offered = defaultdict(Counter)
    for e in catalog:
        for acc in e.get("accessions", []):
            offered[acc][e["entity_type"]] += 1
    return [{"ticker": f["ticker"], "form": f["form"], "reporting_period_end": f["reporting_period_end"],
             "accession": f["accession"], "subsidiaries": offered[f["accession"]]["subsidiary"],
             "segments": offered[f["accession"]]["segment"]} for f in filings()]


def segments_by_company(catalog: list[dict]) -> list[dict]:
    period = {f["accession"]: (f["ticker"], str(f["reporting_period_end"])) for f in filings()}
    out = []
    for e in catalog:
        if e["entity_type"] != "segment":
            continue
        periods = sorted(period[a][1] for a in e["accessions"] if a in period)
        ticker = next((period[a][0] for a in e["accessions"] if a in period), "")
        out.append({"ticker": ticker, "segment": e["name"], "members": " ".join(e.get("members", [])),
                    "first_period": periods[0] if periods else "", "last_period": periods[-1] if periods else "",
                    "filings": len(periods)})
    return sorted(out, key=lambda r: (r["ticker"], r["first_period"], r["segment"]))


def run() -> None:
    out_dir = checks_dir("entity_inputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = list(read_jsonl(Paths.subsidiaries())) if Paths.subsidiaries().exists() else []
    qa = _read_csv(Paths.subsidiary_qa())
    renames = _read_csv(Paths.subsidiary_renames())
    cat_path = Paths.entity_catalog()
    catalog = json.loads(cat_path.read_text(encoding="utf-8")) if cat_path.exists() else []
    if not rows:
        log(STEP, "no subsidiaries.jsonl: run step 6a (subsidiaries) first")
    if not catalog:
        log(STEP, f"no {cat_path}: run step 6b (entity_catalog) first")

    years = subsidiaries_by_year(rows)
    reps = repeats(rows)
    filing_rows = by_filing(catalog)
    segs = segments_by_company(catalog)
    write_csv(out_dir / "subsidiaries_by_year.csv", years, list(years[0]) if years else ["ticker"])
    write_csv(out_dir / "repeats.csv", reps, ["kind", "ticker", "accession", "name", "count", "sub_ids"])
    write_csv(out_dir / "by_filing.csv", filing_rows, list(filing_rows[0]) if filing_rows else ["ticker"])
    write_csv(out_dir / "segments_by_company.csv", segs,
              ["ticker", "segment", "members", "first_period", "last_period", "filings"])

    ten_ks = [f for f in filings() if f["form"] == "10-K"]
    checks = [q for q in qa if q.get("status") != "OK"]
    print("SUBSIDIARIES (step 6a)")
    print(f"  10-Ks in filings.yaml: {len(ten_ks)}; read: {len(qa)}; flagged for checking: {len(checks)}")
    for q in checks:
        print(f"    {q['ticker']:5} {q['reporting_period_end']} {q['accession']}  {q['status']}")
    print(f"  rows: {len(rows)}; distinct subsidiaries: {len({r['sub_id'] for r in rows})}")
    same = sum(1 for r in reps if r["kind"].startswith("same sub_id"))
    print(f"  same subsidiary twice in one 10-K: {same}  (should be 0; repeats.csv)")
    print(f"  one core name, several legal entities in one 10-K: {len(reps) - same}  (repeats.csv)")
    print(f"  possible renames between years: {len(renames)}  (subsidiary_renames.csv)")
    big = [y for y in years if y["flag"]]
    print(f"  10-Ks whose row count moved by more than {int(BIG_CHANGE * 100)}% from the year before: {len(big)}")
    for y in big:
        print(f"    {y['ticker']:5} {y['reporting_period_end']}  {y['rows_10k_before']} -> {y['rows']}")

    print("\nCATALOG (step 6b)")
    types = Counter(e["entity_type"] for e in catalog)
    print(f"  subsidiaries: {types['subsidiary']}; segments: {types['segment']}; registrants: {types['registrant']}")
    no_seg = defaultdict(list)
    for r in filing_rows:
        if not r["segments"]:
            no_seg[r["ticker"]].append(r)
    total = Counter(r["ticker"] for r in filing_rows)
    print(f"  filings offered no segment: {sum(len(v) for v in no_seg.values())} of {len(filing_rows)}")
    for t in sorted(no_seg):
        label = "all filings" if len(no_seg[t]) == total[t] else f"{len(no_seg[t])} of {total[t]} filings"
        print(f"    {t:5} {label}")
    print("\n  Segments by company (first period .. last period, filings):")
    for s in segs:
        print(f"    {s['ticker']:5} {s['segment'][:45]:45} {s['first_period']} .. {s['last_period']}  ({s['filings']})")
    print(f"\nCSV files in {out_dir}")


if __name__ == "__main__":
    run()
