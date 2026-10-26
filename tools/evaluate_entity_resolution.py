"""Evaluate the conservative subsidiary-name resolver on the 50-pair answer key.

This script does not train or tune on the labels.  It reports three-way output:
match / no_match / ambiguous.  Ambiguous is intentionally treated as unresolved,
not silently forced into a yes/no decision.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graphrag.entity_resolution import (  # noqa: E402
    AMBIGUOUS,
    MATCH,
    NO_MATCH,
    resolve_subsidiary_names,
)


def evaluate(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    counts = {MATCH: 0, NO_MATCH: 0, AMBIGUOUS: 0}
    tp = fp = tn = fn = 0
    ambiguous_yes = ambiguous_no = 0
    details = []

    for row in rows:
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

    report = evaluate(args.pairs)
    print(f"Pairs: {report['n']}")
    print(
        "Decisions: "
        f"match={report['counts'][MATCH]}, "
        f"no_match={report['counts'][NO_MATCH]}, "
        f"ambiguous={report['counts'][AMBIGUOUS]}"
    )
    print(
        "Confusion (automatic decisions only): "
        f"TP={report['tp']} FP={report['fp']} TN={report['tn']} FN={report['fn']}"
    )
    print(
        f"Ambiguous: positive={report['ambiguous_yes']} negative={report['ambiguous_no']}"
    )
    print(
        f"Precision={report['precision']:.3f} "
        f"Recall={report['recall']:.3f} "
        f"F1={report['f1']:.3f} "
        f"Coverage={report['coverage']:.3f}"
    )

    if args.show_all:
        for pair_id, truth, decision, score, reason in report["details"]:
            print(f"{pair_id:>2} truth={truth:<3} pred={decision:<9} score={score:.3f} | {reason}")


if __name__ == "__main__":
    main()
