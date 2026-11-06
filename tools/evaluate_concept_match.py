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


def ranked_concepts(rows, question, limit=3):
    """The distinct concepts this question reaches, best first."""
    terms = concept_match.question_terms(question)
    alias = concept_match.aliased_concept(terms)

    scored = []
    for fact_id, concept, dimensions, period_end in rows:
        score = concept_match.score_fact(terms, concept, dimensions, alias)
        if score > 0:
            scored.append((score, period_end or "", fact_id, concept))
    scored.sort(key=lambda r: (-r[0], [-ord(c) for c in r[1]], r[2]))

    concepts = []
    for _, _, _, concept in scored:
        if concept not in concepts:
            concepts.append(concept)
        if len(concepts) == limit:
            break
    return concepts


def main():
    db = os.environ.get("MSFT_FACT_DB_PATH") or str(DEFAULT_DB)
    db = os.path.expanduser(db)
    if not os.path.exists(db):
        raise SystemExit(f"No fact store at {db}. Set MSFT_FACT_DB_PATH.")

    con = duckdb.connect(db, read_only=True)
    rows = con.execute(
        "SELECT fact_id, concept_name, dimensions, period_end FROM financial_facts_v1"
    ).fetchall()
    con.close()

    gold = json.loads(GOLD.read_text())
    top1 = top3 = 0

    print(f"{len(rows)} facts, {len(gold)} questions\n")
    for item in gold:
        want = item["concept"]
        got = ranked_concepts(rows, item["question"])
        first = got[0] if got else None
        hit1, hit3 = first == want, want in got
        top1 += hit1
        top3 += hit3
        mark = "ok " if hit1 else ("..3" if hit3 else "MISS")
        print(f"  {mark}  {item['question'][:48]:50} {first or 'nothing returned'}")
        if not hit1:
            print(f"{'':57}want {want}")

    n = len(gold)
    print(f"\n  top-1 {top1}/{n}   top-3 {top3}/{n}")
    print("\n  Before question matching, the arm ordered by fact_id, which is a")
    print("  hash, and scored every row 1.0 — so top-1 was 0/10 by construction.")


if __name__ == "__main__":
    main()
