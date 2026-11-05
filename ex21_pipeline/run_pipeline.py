"""Step 2: turn every Exhibit 21 in data/raw/ into the parent–subsidiary report.

    python run_pipeline.py

Outputs (in output/):
    parent_subsidiary_report.xlsx   human-readable report (all tables live in this workbook)
    graph_edges.csv                 parent -> subsidiary edges for Graph RAG / graph databases

Re-run it whenever you add filings; both outputs are rebuilt from scratch.
"""
import argparse
from pathlib import Path

from ex21.build import run

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="data/manifest.csv", help="filing list (default: data/manifest.csv)")
    ap.add_argument("--out", default="output", help="output folder (default: output/)")
    a = ap.parse_args()
    result = run(Path(a.manifest), Path(a.out))
    for k, v in result.items():
        print(f"{k:>24}: {v}")
