"""What each arm will actually do with a filter, and what is in the corpus.

Two questions kept costing other teams real time, and neither had an answer
in code:

1. **Which filters work?** The contract offers every arm the same keys. An arm
   may apply a key, accept it and ignore it, or refuse it by name. Only the
   first does what the caller expects, and nothing said which was which. The
   Agent team sent `fiscal_year` and `fiscal_period` on every call; both arms
   ignore both, so their questions came back unfiltered and nothing raised.

2. **Which companies are in the corpus?** The Agent team's parser recognised
   seven companies, two of which we hold. For the other eight it set no ticker
   at all, so a question about one company was answered from another.

Both answers are derived here rather than written down. A list in a document
drifts from the code the first time the code changes; this cannot, because it
reads the same declarations the arms filter on.

    from graphrag import vocabulary
    vocabulary.filters()["graph"]["ignored"]   # -> ['fiscal_period', 'fiscal_year']
    vocabulary.tickers()                       # -> ['AAPL', 'CASY', ...]
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from . import contract

# The structured arm lives outside the package and imports `concept_match` by
# bare name, so its directory has to be on the path before it will import.
# Same reason as in backends.py.
_STRUCTURED_DIR = Path(__file__).resolve().parent.parent / "structured"

# What an arm does with a filter key it is offered.
APPLIED = "applied"      # reaches the query and narrows the result
IGNORED = "ignored"      # accepted, never reaches the query, nothing raised
REFUSED = "refused"      # raises FilterNotSupportedError, by name


def _structured_module():
    if str(_STRUCTURED_DIR) not in sys.path:
        sys.path.insert(0, str(_STRUCTURED_DIR))
    return importlib.import_module("structured_query_v1")


def _classify(offered, applied, ignored) -> dict[str, list[str]]:
    """Sort the keys an arm is offered into what it does with each."""
    return {
        APPLIED: sorted(k for k in offered if k in applied),
        IGNORED: sorted(k for k in offered if k in ignored),
        REFUSED: sorted(k for k in offered if k not in applied and k not in ignored),
    }


def filters(arm: str | None = None) -> dict:
    """What each arm does with every filter the contract offers it.

    Read from the arms themselves, so it cannot disagree with them. An arm
    that is not importable is reported as such rather than guessed at.
    """
    report: dict[str, dict] = {}

    try:
        graph_backend = importlib.import_module("graphrag.graph_backend")
    except ImportError as exc:
        report["graph"] = {"unavailable": f"graphrag.graph_backend is not importable ({exc})"}
    else:
        report["graph"] = _classify(
            contract.GRAPH_FILTERS,
            set(graph_backend._FILTER_TO_PROPERTY) | {"max_hops"},
            graph_backend._IGNORED_FILTERS,
        )

    try:
        structured_query = _structured_module()
    except ImportError as exc:
        report["structured"] = {"unavailable": f"structured_query_v1 is not importable ({exc})"}
    else:
        report["structured"] = _classify(
            contract.STRUCTURED_FILTERS,
            set(structured_query.SUPPORTED_FILTERS) - set(structured_query.IGNORED_FILTERS),
            structured_query.IGNORED_FILTERS,
        )

    if arm is not None:
        if arm not in report:
            raise ValueError(f"no such arm: {arm}. Try one of: {', '.join(sorted(report))}")
        return report[arm]
    return report


def tickers() -> list[str]:
    """Every ticker the corpus holds.

    Read from the fact store, which is the arm that can answer without waking
    a remote database. Raises if no store is configured rather than returning
    an empty list, because an empty list reads as "the corpus is empty" and
    would be acted on.
    """
    store = os.environ.get("MSFT_FACT_DB_PATH")
    if not store:
        raise RuntimeError(
            "set MSFT_FACT_DB_PATH to a DuckDB fact store to read the ticker list"
        )

    import duckdb

    conn = duckdb.connect(store, read_only=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM financial_facts_v1 "
            "WHERE ticker IS NOT NULL ORDER BY ticker"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def summary() -> str:
    """The same two answers as text, for pasting into a reply to another team."""
    lines = ["Filters, by arm:"]
    for arm, detail in filters().items():
        if "unavailable" in detail:
            lines.append(f"  {arm}: {detail['unavailable']}")
            continue
        lines.append(f"  {arm}:")
        for state in (APPLIED, IGNORED, REFUSED):
            lines.append(f"    {state:<8} {', '.join(detail[state]) or '(none)'}")

    try:
        held = tickers()
        lines.append(f"\nTickers in the corpus ({len(held)}): {', '.join(held)}")
    except RuntimeError as exc:
        lines.append(f"\nTickers: {exc}")
    return "\n".join(lines)


if __name__ == "__main__":
    # So another team can read this without writing Python:
    #   MSFT_FACT_DB_PATH=... python3 -m graphrag.vocabulary
    print(summary())
