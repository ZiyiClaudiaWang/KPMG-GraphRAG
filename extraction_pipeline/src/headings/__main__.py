import argparse

from . import step

parser = argparse.ArgumentParser(description=step.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--no-fetch", action="store_true", help="do not download missing HTML from EDGAR")
args = parser.parse_args()
step.run(fetch=not args.no_fetch)
