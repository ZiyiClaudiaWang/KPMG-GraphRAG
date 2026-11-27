"""Count how many reader phrases reach the concept they name.

The gold set contains no phrase a reader would actually type, so it cannot
see the alias work at all. This measures it on its own terms: ask the
structured arm for each alias phrase and check whether rank 1 is the concept
that phrase is supposed to mean.

The phrase list is read from a JSON file rather than from the tree, so the
same 45 questions can be run against a tree that has no alias table — which
is how the "before" number is produced.

    python3 tools/measure_reader_phrases.py --phrases docs/reader_phrases.json \
        --store ~/Projects/kpmg-data/corpus_facts_postgres.duckdb

Writes the phrase list out of the current tree with --dump.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "structured"))


def dump_phrases(path):
    import concept_match
    pairs = [{"phrase": list(p), "concept": c}
             for p, c in concept_match.CONCEPT_ALIASES]
    Path(path).write_text(json.dumps(pairs, indent=2) + "\n")
    return pairs


def measure(pairs, ticker, top_k):
    import structured_query_v1 as sq

    hits, misses = 0, []
    for pair in pairs:
        # The table holds each phrase already split into words.
        phrase = pair["phrase"]
        if not isinstance(phrase, str):
            phrase = " ".join(phrase)
        question = f"{ticker} {phrase}"
        records = sq.query_database(question, {"ticker": ticker}, 50, None)
        ranked = sq.rank_records(records, question, {"ticker": ticker}, top_k)
        if not ranked:
            misses.append((phrase, None))
            continue
        got = ranked[0][1]["concept_name"]
        # The alias table holds the local name; the store holds it prefixed.
        if got.split(":")[-1].lower() == pair["concept"].split(":")[-1].lower():
            hits += 1
        else:
            misses.append((phrase, got))
    return hits, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrases", default=str(ROOT / "docs" / "reader_phrases.json"))
    ap.add_argument("--store", required=False)
    ap.add_argument("--ticker", default="MSFT")
    ap.add_argument("--top-k", type=int, default=1)
    ap.add_argument("--dump", action="store_true",
                    help="write this tree's alias table to --phrases and stop")
    args = ap.parse_args()

    if args.dump:
        pairs = dump_phrases(args.phrases)
        print(f"wrote {len(pairs)} phrases to {args.phrases}")
        return

    if args.store:
        os.environ["MSFT_FACT_DB_PATH"] = os.path.expanduser(args.store)

    pairs = json.loads(Path(args.phrases).read_text())
    hits, misses = measure(pairs, args.ticker, args.top_k)
    print(f"{hits}/{len(pairs)} reader phrases return their concept at rank 1")
    for phrase, got in misses:
        print(f"  miss: {phrase!r} -> {got or 'no rows'}")


if __name__ == "__main__":
    main()
