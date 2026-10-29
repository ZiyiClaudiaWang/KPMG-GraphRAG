"""Evaluate the conservative subsidiary-name resolver on the 50-pair answer key.

This script does not train or tune on the labels.  It reports three-way output:
match / no_match / ambiguous.  Ambiguous is intentionally treated as unresolved,
not silently forced into a yes/no decision.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graphrag.entity_resolution import (  # noqa: E402
    AMBIGUOUS,
    EX21,
    MATCH,
    NO_MATCH,
    Provenance,
    TEXT,
    resolve_subsidiary_names,
    resolve_subsidiary_names_with_provenance,
)

# The fixture records where a name was read, as "MSFT Ex21 FY2024" or
# "JPM 10-K FY2025 text (...)". Parsing that shape belongs here, not in the library.
_EX21_SOURCE = re.compile(r"^(?P<registrant>[A-Za-z.]+)\s+Ex21\s+FY(?P<fiscal_year>\d{4})")
_TEXT_SOURCE = re.compile(r"^(?P<registrant>[A-Za-z.]+)\s+10-K\s+FY(?P<fiscal_year>\d{4})\s+text")


def provenance_from_source(source: str) -> Provenance | None:
    """Read the fixture's source column into a Provenance, or None if unrecognised."""
    source = source.strip()
    hit = _EX21_SOURCE.match(source)
    if hit:
        return Provenance(hit["registrant"], EX21, int(hit["fiscal_year"]))
    hit = _TEXT_SOURCE.match(source)
    if hit:
        return Provenance(hit["registrant"], TEXT, int(hit["fiscal_year"]))
    return None


def evaluate(path: Path, *, second_pass: bool = False) -> dict:
    """Score the resolver on the answer key.

    With ``second_pass``, an ambiguous pair may be settled by Exhibit 21
    succession. Nothing else about the scoring changes, so the two runs are
    directly comparable.
    """
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    counts = {MATCH: 0, NO_MATCH: 0, AMBIGUOUS: 0}
    tp = fp = tn = fn = 0
    ambiguous_yes = ambiguous_no = 0
    details = []

    for row in rows:
        if second_pass:
            result = resolve_subsidiary_names_with_provenance(
                row["name_a"],
                row["name_b"],
                provenance_a=provenance_from_source(row["source_a"]),
                provenance_b=provenance_from_source(row["source_b"]),
            )
        else:
            result = resolve_subsidiary_names(row["name_a"], row["name_b"])
        truth = row["label"].strip().lower() == "yes"
        counts[result.decision] += 1

        if result.decision == MATCH:
            if truth:
                tp += 1
            else:
                fp += 1
        elif result.decision == NO_MATCH:
            if truth:
                fn += 1
            else:
                tn += 1
        else:
            if truth:
                ambiguous_yes += 1
            else:
                ambiguous_no += 1

        details.append((row["pair_id"], row["label"], result.decision, result.score, result.reason))

    decided = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    # Recall counts unresolved positives as misses because they still need a second-stage resolver.
    recall = tp / (tp + fn + ambiguous_yes) if (tp + fn + ambiguous_yes) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    coverage = decided / len(rows) if rows else 0.0

    return {
        "n": len(rows),
        "counts": counts,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "ambiguous_yes": ambiguous_yes,
        "ambiguous_no": ambiguous_no,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "coverage": coverage,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pairs",
        type=Path,
        default=ROOT / "toy_graph" / "50_subsidiary_pairs.csv",
    )
    parser.add_argument("--show-all", action="store_true")
    args = parser.parse_args()

    def show(label: str, report: dict) -> None:
        print(f"\n{label}")
        print(
            "  Decisions: "
            f"match={report['counts'][MATCH]}, "
            f"no_match={report['counts'][NO_MATCH]}, "
            f"ambiguous={report['counts'][AMBIGUOUS]}"
        )
        print(
            "  Confusion (automatic decisions only): "
            f"TP={report['tp']} FP={report['fp']} TN={report['tn']} FN={report['fn']}"
        )
        print(f"  Ambiguous: positive={report['ambiguous_yes']} negative={report['ambiguous_no']}")
        print(
            f"  Precision={report['precision']:.3f} "
            f"Recall={report['recall']:.3f} "
            f"F1={report['f1']:.3f} "
            f"Coverage={report['coverage']:.3f}"
        )

    baseline = evaluate(args.pairs)
    print(f"Pairs: {baseline['n']}")
    show("Pass 1 - names only", baseline)

    report = evaluate(args.pairs, second_pass=True)
    show("Pass 2 - plus Exhibit 21 succession", report)

    gained = report["tp"] - baseline["tp"]
    print(
        f"\nSecond pass settled {gained} ambiguous pair(s); "
        f"false merges added: {report['fp'] - baseline['fp']}"
    )

    if args.show_all:
        for pair_id, truth, decision, score, reason in report["details"]:
            print(f"{pair_id:>2} truth={truth:<3} pred={decision:<9} score={score:.3f} | {reason}")


if __name__ == "__main__":
    main()
