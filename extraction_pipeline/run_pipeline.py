"""Run the extraction pipeline, all steps or a chosen few.

  python run_pipeline.py                            every step (step 5 calls the OpenAI API)
  python run_pipeline.py --steps clean,filter       only these steps
  python run_pipeline.py --until segment            from the start up to and including segment
  python run_pipeline.py --dry-run                  no API calls and no database writes
  python run_pipeline.py --steps label --filing 0000320193-25-000079 --limit 5

Steps, in order:
  fetch         check (or export) data/sections/*.jsonl
  subsidiaries  subsidiaries.jsonl: Exhibit 21 of each 10-K in filings.yaml (SEC EDGAR, no LLM)
  entity_catalog entity_catalog.json: subsidiaries (step 1b) + XBRL segments a Claim may link to
  page_breaks   page_break_decisions.jsonl: yes/no for page breaks the rules leave gray (OpenAI)
  clean         remove page headers, repair paragraphs
  filter        section_manifest.csv: which sections to extract from
  headings      heading_levels.jsonl: how the sub-headings of a section nest, from the
                filing's EDGAR HTML (no LLM; needs SEC_USER_AGENT to download the HTML once)
  segment       sentences.jsonl: numbered sentences and list groups
  label         labels.jsonl: one label per sentence (OpenAI)
  validate      errors.jsonl: every sentence has exactly one valid label
  risk_factors  risk_categories.csv: risk category and themes of each Item 1A sentence
  build_claims  claims.jsonl
  load          Neo4j (Claims and subsidiaries)

  python run_pipeline.py --steps subsidiaries,load --dry-run   subsidiaries only, no database

A run that includes any step up to segment (or risk_factors) ends with the review report,
data/checks/report/report.md (python -m src.report): stale steps, numbers changed since the last
report, and the check files to open.

page_breaks is not in a default run: it costs money, and its output file, once written, is
read by clean on every later run. Run it on purpose:
  python run_pipeline.py --steps page_breaks,clean,filter          (clean must have run once)
"""

from __future__ import annotations

import argparse
import sys
import time

from src import (build_claims, clean, entity_catalog, fetch, filter_sections, headings, label, load,
                 page_breaks, report, risk_factors, segment, subsidiaries, validate)

STEPS = ["fetch", "subsidiaries", "entity_catalog", "page_breaks", "clean", "filter", "headings", "segment", "label",
         "validate", "risk_factors", "build_claims", "load"]
OPT_IN = {"page_breaks"}       # paid; only when named in --steps
# Steps the review report covers (no labels needed): a run with any of them ends with the report.
REPORTED = {"fetch", "subsidiaries", "entity_catalog", "page_breaks", "clean", "filter", "headings",
            "segment", "risk_factors"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", help="comma-separated steps to run, e.g. clean,filter")
    parser.add_argument("--until", choices=STEPS, help="run from the first step up to this one")
    parser.add_argument("--dry-run", action="store_true", help="label and load without API or database")
    parser.add_argument("--filing", metavar="ACCESSION", help="label: only call the API for this filing")
    parser.add_argument("--limit", type=int, help="label: at most this many API batches")
    parser.add_argument("--from-db", metavar="FOLDER", help="fetch: export from the database via this folder")
    args = parser.parse_args()

    steps = [s for s in STEPS if s not in OPT_IN]
    if args.steps:
        steps = [s.strip() for s in args.steps.split(",")]
        unknown = [s for s in steps if s not in STEPS]
        if unknown:
            sys.exit(f"Unknown steps: {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
    elif args.until:
        steps = [s for s in STEPS[:STEPS.index(args.until) + 1] if s not in OPT_IN]

    actions = {
        "fetch": lambda: fetch.run(args.from_db),
        "subsidiaries": subsidiaries.run,
        "entity_catalog": entity_catalog.run,
        "page_breaks": lambda: page_breaks.run(dry_run=args.dry_run, limit=args.limit),
        "clean": clean.run,
        "filter": filter_sections.run,
        "headings": headings.run,
        "segment": segment.run,
        "label": lambda: label.run(dry_run=args.dry_run, filing=args.filing, limit=args.limit),
        "validate": validate.run,
        "risk_factors": risk_factors.run,
        "build_claims": build_claims.run,
        "load": lambda: load.run(dry_run=args.dry_run),
    }
    for step in STEPS:                    # always run in pipeline order
        if step in steps:
            started = time.time()
            actions[step]()
            print(f"  ({step} took {time.time() - started:.1f}s)\n", flush=True)
    if REPORTED & set(steps):
        try:
            report.run()
        except Exception as e:            # the report never fails a run
            print(f"[report] not written: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
