import argparse
from pathlib import Path

from . import step

parser = argparse.ArgumentParser(description=step.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--no-fetch", action="store_true", help="do not download missing HTML from EDGAR")
parser.add_argument("--compare-with", type=Path, metavar="JSONL",
                    help="an older heading_levels.jsonl (e.g. step 4b's LLM output) to compare in the review files")
args = parser.parse_args()
step.run(fetch=not args.no_fetch, compare_with=args.compare_with)
