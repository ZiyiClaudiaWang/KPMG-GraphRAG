"""Step 1 - Fetch: make sure every pilot filing is exported from the Data team database.

Two modes:
  default            check that data/sections/<TICKER>_<accession>.jsonl exists for every
                     filing in config/filings.yaml, and report what each file holds.
  --from-db FOLDER   export the filings with tools/graphrag_data.py. FOLDER must hold
                     graphrag_data.py and .env.aws (see docs/data-access.md).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .common import Paths, data_dir, filings, log, read_jsonl

STEP = "fetch"


def export_from_db(tool_folder: Path) -> None:
    """Export all pilot filings by calling graphrag_data.py from its own folder."""
    tool_folder = tool_folder.resolve()
    out_dir = data_dir()
    cwd = Path.cwd()
    os.chdir(tool_folder)  # graphrag_data.py reads .env.aws from the working directory
    sys.path.insert(0, str(tool_folder))
    try:
        import graphrag_data as gd  # noqa: WPS433 (runtime import on purpose)

        accessions = [f["accession"] for f in filings()]
        with gd.connect() as conn:
            found = gd.find_filings(conn, accessions=accessions)
            if found.empty:
                raise SystemExit("No matching filings in the database.")
            gd.export_sections(conn, found, out_dir=out_dir)
    finally:
        os.chdir(cwd)


def check_exports() -> bool:
    """Report each export; return False if any is missing or looks wrong."""
    ok = True
    for f in filings():
        path = Paths.sections(f)
        if not path.exists():
            log(STEP, f"MISSING {path}")
            ok = False
            continue
        rows = list(read_jsonl(path))
        sets = {r.get("processing_set_id") for r in rows}
        forms = {r.get("form") for r in rows}
        if forms != {f["form"]}:
            log(STEP, f"WARNING {path.name}: form {forms} does not match config {f['form']}")
            ok = False
        if len(sets) != 1:
            log(STEP, f"WARNING {path.name}: more than one processing_set_id {sets}")
            ok = False
        log(STEP, f"{path.name}: {len(rows)} sections, processing_set_id={next(iter(sets))}")
    if not ok:
        accessions = " ".join(f["accession"] for f in filings())
        log(STEP, "Export missing filings with:  uv run graphrag_data.py fetch --accessions " + accessions)
    return ok


def run(from_db: str | None = None) -> None:
    if from_db:
        export_from_db(Path(from_db))
    if not check_exports():
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-db", metavar="FOLDER", help="folder holding graphrag_data.py and .env.aws")
    run(parser.parse_args().from_db)


if __name__ == "__main__":
    main()
