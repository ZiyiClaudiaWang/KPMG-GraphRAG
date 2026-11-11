"""Register the real retrieval arms from the environment.

Importing this module configures whichever arms the environment describes, so
moving off the mock is one import and nothing else:

    from mock.mock_retrieval_tools import graph_search   # today
    from graphrag.backends import graph_search           # later

Same four arguments, same envelope back. What each arm needs:

    graph        NEO4J_URI, NEO4J_USERNAME (or NEO4J_USER), NEO4J_PASSWORD,
                 and optionally NEO4J_DATABASE. On Aura the username is the
                 instance id, not `neo4j`.
    structured   MSFT_FACT_DB_PATH, the DuckDB store a loader writes
    cypher       the same Neo4j settings as the graph arm, plus whatever
                 `graphrag/cypher_backend.py` needs to write a query

There are two graph arms and they stay separate on purpose. `graph_search`
runs one fixed query; `cypher_search` writes a query per question. Scoring one
should never hand you rows from the other, so they are two names rather than
one name with a mode.

Two loaders write that store and the arm reads either: `fact_loader_v1.py`
parses EDGAR HTML, `fact_loader_data_api.py` reads the Data team's Postgres.
Moving to Postgres changes where the facts come from, not what this reads, so
you still point `MSFT_FACT_DB_PATH` at the store either way.

Keep this out of `retrieve.py`. That module raises when nothing has registered
a backend, which is what tells you retrieval is down rather than that it found
no evidence. Register inside it and that failure goes quiet.

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
from .retrieve import cypher_search, graph_search, structured_search  # noqa: F401  (re-exported)

__all__ = [
    "configure_from_env",
    "cypher_search",
    "graph_search",
    "structured_search",
    "status",
]

# The structured arm lives outside the package and imports `concept_match` by
# bare name, so Python needs that directory on the path before the import works.
_STRUCTURED_DIR = Path(__file__).resolve().parent.parent / "structured"

# configure_from_env() fills this in, so a caller that hits an
# unconfigured-backend error can ask why instead of working it out again.
status: dict[str, str] = {}


def _register_graph() -> str:
    """Register the graph arm, or say why it did not."""
    if not os.environ.get("NEO4J_URI"):
        return "skipped: NEO4J_URI is not set"
    try:
        graph_backend = importlib.import_module("graphrag.graph_backend")
    except ImportError as exc:
        # graph_backend.py may not be present yet. Say so plainly instead of
        # failing the import for everyone who only wants the other arm.
        return f"skipped: graphrag.graph_backend is not importable ({exc})"
    retrieve.configure_graph_backend(graph_backend.search)
    return f"registered: {os.environ['NEO4J_URI']}"


def _register_structured() -> str:
    """Register the structured arm, or say why it did not."""
    store = Path(os.environ.get("MSFT_FACT_DB_PATH", "msft_financial_facts_v1.duckdb"))
    if not store.exists():
        # Two loaders write this store and the arm reads either, so say both
        # rather than sending everyone to the one that needs no credentials.
        return (
            f"skipped: no DuckDB store at {store}. Point MSFT_FACT_DB_PATH at one, "
            "or build it: tools/build_fact_store.py --output <path> (from EDGAR, "
            "needs SEC_USER_AGENT), or structured/fact_loader_data_api.py --output "
            "<path> (from the Data team's Postgres, needs their dotenv)"
        )
    if str(_STRUCTURED_DIR) not in sys.path:
        sys.path.insert(0, str(_STRUCTURED_DIR))
    try:
        structured_query = importlib.import_module("structured_query_v1")
    except ImportError as exc:
        return f"skipped: structured_query_v1 is not importable ({exc})"
    retrieve.configure_structured_backend(structured_query.structured_search)
    return f"registered: {store}"


def _register_cypher() -> str:
    """Register the generated-Cypher arm, or say why it did not."""
    if not os.environ.get("NEO4J_URI"):
        return "skipped: NEO4J_URI is not set"
    try:
        cypher_backend = importlib.import_module("graphrag.cypher_backend")
    except ImportError as exc:
        # This arm is being built. Until the module lands, say so and leave the
        # other two working rather than failing the import for everyone.
        return f"skipped: graphrag.cypher_backend is not importable ({exc})"
    retrieve.configure_cypher_backend(cypher_backend.search)
    return f"registered: {os.environ['NEO4J_URI']}"


def configure_from_env() -> dict[str, str]:
    """Register every arm the environment describes, and report on all of them.

    Safe to call again. Call it again to pick up a store path or password set
    after import.
    """
    status.clear()
    status["graph"] = _register_graph()
    status["structured"] = _register_structured()
    status["cypher"] = _register_cypher()
    return dict(status)


configure_from_env()
