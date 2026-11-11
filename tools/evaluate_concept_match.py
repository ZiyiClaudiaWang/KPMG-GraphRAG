"""Score the structured arm's question matching against the gold questions.

`structured/msft_gold_questions_v1.json` holds ten natural-language questions,
each recording the concept that answers it. That makes it an answer key for
whether a typed question reaches the right fact.

    MSFT_FACT_DB_PATH=~/Downloads/msft_financial_facts_v1.duckdb \
        python3 tools/evaluate_concept_match.py

Reports top-1 and top-3: whether the right concept is the first one returned,
and whether it is in the first three.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "structured"))

import concept_match  # noqa: E402

GOLD = ROOT / "structured" / "msft_gold_questions_v1.json"
DEFAULT_DB = ROOT / "msft_financial_facts_v1.duckdb"


# This axis marks a row as coming from the segment note. It is not a slice of
# the business, so the answer key does not record it and comparisons ignore it.
STRUCTURAL_AXES = frozenset({"srt:ConsolidationItemsAxis"})


def slice_of(dimensions):
    """The axes that say which part of the business a fact covers."""
    if isinstance(dimensions, str):
        dimensions = json.loads(dimensions or "[]")
    return sorted(
        (str(d.get("axis")), str(d.get("member")))
        for d in (dimensions or [])
        if str(d.get("axis")) not in STRUCTURAL_AXES
    )


def ranked_facts(rows, question, filers=None, ticker=None, limit=3):
    """The facts this question reaches, best first.

    `ticker` narrows the candidates the way the query path's filters do. The
    store used to hold one company, so there was no wrong company to return.
    Across twenty filings, eight of the ten original questions were answered
    by a fact from another company that happened to carry the right concept.
    """
    terms = concept_match.question_terms(question)
    alias = concept_match.aliased_concept(terms)
    filers = filers or {}

    scored = []
    for fact_id, concept, dimensions, period_end, row_ticker in rows:
        if ticker and row_ticker != ticker:
            continue
        score = concept_match.score_fact(
            terms, concept, dimensions, alias,
            filers.get(row_ticker, frozenset())
        )
        if score > 0:
            scored.append((score, period_end or "", fact_id, concept, dimensions))
    scored.sort(key=lambda r: (-r[0], [-ord(c) for c in r[1]], r[2]))
    return scored[:limit]


def main():
    db = os.environ.get("MSFT_FACT_DB_PATH") or str(DEFAULT_DB)
    db = os.path.expanduser(db)
    if not os.path.exists(db):
        raise SystemExit(f"No fact store at {db}. Set MSFT_FACT_DB_PATH.")

    con = duckdb.connect(db, read_only=True)
    rows = con.execute(
        "SELECT fact_id, concept_name, dimensions, period_end, ticker "
        "FROM financial_facts_v1"
    ).fetchall()
    # The scorer ignores the filer's own words when matching dimensions, so the
    # evaluator has to hand it the same words the query path does. Without
    # this it scores the behaviour we replaced.
    filers = {
        ticker: concept_match.filer_words(ticker, name)
        for ticker, name in con.execute(
            "SELECT DISTINCT ticker, company_name FROM filings_v1"
        ).fetchall()
    }
    con.close()

    gold = json.loads(GOLD.read_text())
    by_concept = by_slice = 0

    print(f"{len(rows)} facts, {len(gold)} questions\n")
    for item in gold:
        want_concept = item["concept"]
        want_slice = slice_of(item.get("dimensions"))
        got = ranked_facts(rows, item["question"], filers, item.get("ticker"))

        concept = got[0][3] if got else None
        got_slice = slice_of(got[0][4]) if got else None
        concept_ok = concept == want_concept
        slice_ok = concept_ok and got_slice == want_slice
        by_concept += concept_ok
        by_slice += slice_ok

        mark = "ok  " if slice_ok else ("DIM " if concept_ok else "MISS")
        print(f"  {mark}  {item['question'][:50]:52} {_name(got_slice)}")
        if not concept_ok:
            print(f"{'':59}want {want_concept}, got {concept or 'nothing'}")
        elif not slice_ok:
            print(f"{'':59}want {_name(want_slice)}")

    n = len(gold)
    print(f"\n  right concept  {by_concept}/{n}")
    print(f"  right slice    {by_slice}/{n}   <- concept and dimensions both")
    print("\n  Scoring the concept alone reported 10/10 through two defects that")
    print("  returned the wrong slice of the right concept. The slice line is")
    print("  the one that would have caught them.")


def _name(dimension_slice):
    """A short label for a slice, for reading the run."""
    if dimension_slice is None:
        return "nothing returned"
    if not dimension_slice:
        return "consolidated"
    return " + ".join(member.split(":")[-1] for _, member in dimension_slice)


if __name__ == "__main__":
    main()
