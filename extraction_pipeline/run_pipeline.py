"""Run the extraction pipeline, all steps or a chosen few.

  python run_pipeline.py                            every step (step 7 calls the OpenAI API)
  python run_pipeline.py --steps clean,filter       only these steps
  python run_pipeline.py --until segment            from the start up to and including segment
  python run_pipeline.py --dry-run                  no API calls and no database writes
  python run_pipeline.py --steps label --filing 0000320193-25-000079 --limit 5

Steps, in order (the number is the README's):
  1   fetch          check (or export) data/sections/*.jsonl
  2b  page_breaks    page_break_decisions.jsonl: yes/no for page breaks the rules leave gray (paid)
  2   clean          remove page headers, repair paragraphs
  3   filter         section_manifest.csv: which sections to extract from
  4b  headings       heading levels, lead sentences and tables from the filing's EDGAR HTML
                     (no LLM; needs SEC_USER_AGENT to download the HTML once)
  4   segment        sentences.jsonl: sentences, list groups and headings (units)
  5   risk_factors   risk factors and risk categories of every Item 1A unit (no LLM)
  5b  risk_themes    primary and secondary themes of each risk factor (paid: sends only when named)
  6a  subsidiaries   subsidiaries.jsonl: Exhibit 21 of each 10-K in filings.yaml (SEC EDGAR, no LLM)
  6b  entity_catalog entity_catalog.json: subsidiaries + XBRL segments a Claim may link to
  7   label          labels.jsonl: one label per sentence (paid)
  8   validate       errors.jsonl, review_queue.jsonl
  9   build_claims   claims.jsonl
  10  load           Neo4j (Claims, risk themes, subsidiaries, segments)

  python run_pipeline.py --steps subsidiaries,load --dry-run   subsidiaries only, no database

After segment, the repeats check (src/checks/unit_repeats.py, no step reads it) writes
data/checks/unit_repeats/. A run that includes any step up to entity_catalog ends with the
review report, data/checks/report/report.md (python -m src.checks.report).

Paid steps besides label:
  page_breaks  not in a default run; once written, clean reads its answers on every later run:
               python run_pipeline.py --steps page_breaks,clean,filter   (clean must have run once)
  risk_themes  in a default run it only reads the saved replies; it sends when named:
               python run_pipeline.py --steps risk_themes --dry-run      (requests and cost only)
               python run_pipeline.py --steps risk_themes --limit 3      (then without --limit)
"""

from __future__ import annotations

import argparse
import sys
import time

from src import build_claims, clean, fetch, filter_sections, headings, label, load, page_breaks, segment, validate
from src.checks import report, unit_repeats
from src.entities import catalog as entity_catalog, subsidiaries
from src.risk import factors as risk_factors, themes as risk_themes

STEPS = ["fetch", "page_breaks", "clean", "filter", "headings", "segment", "risk_factors", "risk_themes",
         "subsidiaries", "entity_catalog", "label", "validate", "build_claims", "load"]
OPT_IN = {"page_breaks"}                  # paid; only when named in --steps
SEND_WHEN_NAMED = {"risk_themes"}         # paid; in a default run it reads saved replies only
# Steps the review report covers (no labels needed): a run with any of them ends with the report.
REPORTED = set(STEPS[:STEPS.index("entity_catalog") + 1])


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
    named = False                          # risk_themes was named in --steps: it may send
    if args.steps:
        steps = [s.strip() for s in args.steps.split(",")]
        unknown = [s for s in steps if s not in STEPS]
        if unknown:
            sys.exit(f"Unknown steps: {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
        named = bool(SEND_WHEN_NAMED & set(steps))
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
        "risk_themes": lambda: risk_themes.run(dry_run=args.dry_run, limit=args.limit, workers=args.workers,
                                               send=named),
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
            if step == "segment":         # a check, not a step: label finds the repeats itself
                try:
                    unit_repeats.run()
                except Exception as e:    # a check never fails a run
                    print(f"[unit_repeats] not written: {type(e).__name__}: {e}", flush=True)
    if REPORTED & set(steps):
        try:
            report.run()
        except Exception as e:            # the report never fails a run
            print(f"[report] not written: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
