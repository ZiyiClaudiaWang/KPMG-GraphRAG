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
  risk_themes   risk_themes.jsonl: primary and secondary themes of each Item 1A risk factor (OpenAI)
  risk_factors  risk_assignments.jsonl: each Item 1A unit's category heading, its group (no LLM)
                and the themes of its risk factor (from risk_themes)
  label         labels.jsonl: one label per sentence (OpenAI)
  validate      errors.jsonl: every sentence has exactly one valid label
  build_claims  claims.jsonl
  load          Neo4j (Claims and subsidiaries)

  python run_pipeline.py --steps subsidiaries,load --dry-run   subsidiaries only, no database

A run that includes any step up to risk_factors ends with the review report,
data/checks/report/report.md (python -m src.report): stale steps, numbers changed since the last
report, and the check files to open.

page_breaks and risk_themes are not in a default run: they cost money, and their output file,
once written, is read by the next step on every later run. Run them on purpose:
  python run_pipeline.py --steps page_breaks,clean,filter          (clean must have run once)
  python run_pipeline.py --steps risk_themes,risk_factors --dry-run   (requests and cost only)
  python run_pipeline.py --steps risk_themes,risk_factors --limit 3   (then without --limit)
risk_themes and risk_factors need only step 4's output, so they run before label; Claims pick
the themes up by unit_id in build_claims.
"""

from __future__ import annotations

import argparse
import sys
import time

from src import build_claims, clean, fetch, filter_sections, headings, label, load, page_breaks, segment, validate
from src.checks import report
from src.entities import catalog as entity_catalog, subsidiaries
from src.risk import factors as risk_factors, themes as risk_themes

STEPS = ["fetch", "subsidiaries", "entity_catalog", "page_breaks", "clean", "filter", "headings", "segment",
         "risk_themes", "risk_factors", "label", "validate", "build_claims", "load"]
OPT_IN = {"page_breaks", "risk_themes"}       # paid; only when named in --steps
# Steps the review report covers (no labels needed): a run with any of them ends with the report.
REPORTED = {"fetch", "subsidiaries", "entity_catalog", "page_breaks", "clean", "filter", "headings",
            "segment", "risk_themes", "risk_factors"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", help="comma-separated steps to run, e.g. clean,filter")
    parser.add_argument("--until", choices=STEPS, help="run from the first step up to this one")
    parser.add_argument("--dry-run", action="store_true", help="paid steps and load without API or database")
    parser.add_argument("--filing", metavar="ACCESSION", help="label: only call the API for this filing")
    parser.add_argument("--limit", type=int, help="paid steps: at most this many new API requests")
    parser.add_argument("--workers", type=int, help="label, risk_themes: requests in flight at once")
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
        "risk_themes": lambda: risk_themes.run(dry_run=args.dry_run, limit=args.limit, workers=args.workers),
        "label": lambda: label.run(dry_run=args.dry_run, filing=args.filing, limit=args.limit,
                                   workers=args.workers),
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
