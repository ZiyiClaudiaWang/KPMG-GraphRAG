"""Compare the subsidiaries step with the standalone Exhibit 21 pipeline (ex21_pipeline/).

Moving Exhibit 21 into this pipeline must not change what it finds. For every 10-K the
subsidiaries step read, this checks that the standalone run's graph_edges.csv lists the same
subsidiaries with the same jurisdiction and country. 10-Ks only one side has (the standalone
run also keeps Apple's and Microsoft's earlier years) are reported, not counted as
differences.

Usage (from extraction_pipeline/, after `python run_pipeline.py --steps subsidiaries`):
  python compare_ex21.py                                   # against ../ex21_pipeline/output/graph_edges.csv
  python compare_ex21.py --standalone path/to/graph_edges.csv

Exit code 0 when every shared 10-K matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from src.common import ROOT
from src.subsidiaries import normalised_name, read_rows

ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


def standalone(path: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """accession -> normalised name -> (jurisdiction as filed, country), from graph_edges.csv."""
    out: dict[str, dict] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["relation"] != "HAS_SUBSIDIARY":
                continue
            acc = ACCESSION.search(r["evidence"] or "")      # "Exhibit 21 (0000320193-25-000079)"
            if not acc:
                continue
            out[acc.group()][normalised_name(r["target"])] = (r["target_jurisdiction"], r["target_country"])
    return out


def pipeline() -> dict[str, dict[str, tuple[str, str]]]:
    out: dict[str, dict] = defaultdict(dict)
    for r in read_rows():
        out[r["accession_number"]][r["normalised_name"]] = (r["jurisdiction_raw"], r["country"])
    return out


def compare(new: dict, old: dict) -> list[str]:
    """Differences on the 10-Ks both sides have, one line each."""
    problems = []
    for acc in sorted(set(new) & set(old)):
        a, b = new[acc], old[acc]
        for name in sorted(set(b) - set(a)):
            problems.append(f"{acc}: only standalone has '{name}'")
        for name in sorted(set(a) - set(b)):
            problems.append(f"{acc}: only the pipeline has '{name}'")
        for name in sorted(set(a) & set(b)):
            if a[name] != b[name]:
                problems.append(f"{acc}: '{name}' pipeline {a[name]} vs standalone {b[name]}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--standalone", type=Path,
                        default=ROOT.parent / "ex21_pipeline" / "output" / "graph_edges.csv")
    args = parser.parse_args()
    new, old = pipeline(), standalone(args.standalone)
    if not new:
        sys.exit("No subsidiaries output yet: run `python run_pipeline.py --steps subsidiaries` first.")

    print(f"{'accession':<22} {'pipeline':>8} {'standalone':>10}")
    for acc in sorted(set(new) | set(old)):
        print(f"{acc:<22} {len(new.get(acc, {})) or '-':>8} {len(old.get(acc, {})) or '-':>10}")
    only_new = sorted(set(new) - set(old))
    if only_new:
        print(f"\nNot in the standalone output (cannot compare): {', '.join(only_new)}")

    problems = compare(new, old)
    shared = len(set(new) & set(old))
    if problems:
        print(f"\n{len(problems)} difference(s) on {shared} shared 10-K(s):")
        print("\n".join("  " + p for p in problems))
        sys.exit(1)
    print(f"\nSame subsidiaries, jurisdictions and countries on all {shared} shared 10-K(s).")


if __name__ == "__main__":
    main()
