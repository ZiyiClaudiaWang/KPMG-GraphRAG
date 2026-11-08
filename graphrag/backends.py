"""Register the real retrieval arms from the environment.

Importing this module configures whichever arms the environment describes, so
moving off the mock is one import and nothing else:

    from mock.mock_retrieval_tools import graph_search   # today
    from graphrag.backends import graph_search           # later

Same four arguments, same envelope back. What each arm needs:

    graph        NEO4J_URI, NEO4J_USERNAME (or NEO4J_USER), NEO4J_PASSWORD,
                 and optionally NEO4J_DATABASE. On Aura the username is the
                 instance id, not `neo4j`.
    structured   MSFT_FACT_DB_PATH, the DuckDB store the loader writes

This is deliberately not in `retrieve.py`. That module raising when no backend
is registered is the property it exists for: it keeps *retrieval is down* apart
from *no evidence*. Registering inside it would make that failure quiet, which
is the opposite of the point.

An arm whose environment is missing stays unregistered, and calling it still
raises `RetrievalUnavailableError`. Ask `configure_from_env()` what happened
and it names the reason for each arm rather than leaving you to guess.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from . import retrieve
from .retrieve import graph_search, structured_search  # noqa: F401  (re-exported)

__all__ = ["configure_from_env", "graph_search", "structured_search", "status"]

# The structured arm lives outside the package and imports `concept_match` by
# bare name, so its own directory has to be importable before it loads.
_STRUCTURED_DIR = Path(__file__).resolve().parent.parent / "structured"

# Filled in by configure_from_env(), so a caller that got an unconfigured-backend
# error can ask why without re-deriving it.
status: dict[str, str] = {}


def _register_graph() -> str:
    """Register the graph arm, or say why it was skipped."""
    if not os.environ.get("NEO4J_URI"):
        return "skipped: NEO4J_URI is not set"
    try:
        graph_backend = importlib.import_module("graphrag.graph_backend")
    except ImportError as exc:
        # graph_backend.py is still on its own branch. Say so plainly instead
        # of failing the import for everyone who only wants the other arm.
        return f"skipped: graphrag.graph_backend is not importable ({exc})"
    retrieve.configure_graph_backend(graph_backend.search)
    return f"registered: {os.environ['NEO4J_URI']}"


def _register_structured() -> str:
    """Register the structured arm, or say why it was skipped."""
    store = Path(os.environ.get("MSFT_FACT_DB_PATH", "msft_financial_facts_v1.duckdb"))
    if not store.exists():
        return f"skipped: no DuckDB store at {store}; set MSFT_FACT_DB_PATH"
    if str(_STRUCTURED_DIR) not in sys.path:
        sys.path.insert(0, str(_STRUCTURED_DIR))
    try:
        structured_query = importlib.import_module("structured_query_v1")
    except ImportError as exc:
        return f"skipped: structured_query_v1 is not importable ({exc})"
    retrieve.configure_structured_backend(structured_query.structured_search)
    return f"registered: {store}"


def configure_from_env() -> dict[str, str]:
    """Register every arm the environment describes, and report on all of them.

    Safe to call again. Re-reading the environment is how a caller picks up a
    store path or a password that was set after import.
    """
    status.clear()
    status["graph"] = _register_graph()
    status["structured"] = _register_structured()
    return dict(status)


configure_from_env()
