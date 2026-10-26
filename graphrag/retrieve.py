"""Shared retrieval entry points, ready to wrap the real backends.

The public signatures are fixed by ``graphrag.contract`` and the INT-0 mocks.
This module deliberately does *not* pretend that a real Neo4j or DuckDB backend
exists in this branch.  Backends are registered explicitly; until then a call
raises ``RetrievalUnavailableError`` rather than silently falling back to mock
or toy data.

When Kelly/Claudia's graph retriever and Ashley's structured query layer land,
register thin adapters that return contract-shaped evidence.  The wrapper below
handles input validation, trace propagation, deterministic ordering/ranking,
and final conformance validation in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import contract

Backend = Callable[[str, dict, int, str | None], list[dict[str, Any]]]

_graph_backend: Backend | None = None
_structured_backend: Backend | None = None


def configure_graph_backend(backend: Backend | None) -> None:
    """Register (or clear with ``None``) the real graph retrieval backend."""
    global _graph_backend
    if backend is not None and not callable(backend):
        raise TypeError("graph backend must be callable or None")
    _graph_backend = backend


def configure_structured_backend(backend: Backend | None) -> None:
    """Register (or clear with ``None``) the real structured retrieval backend."""
    global _structured_backend
    if backend is not None and not callable(backend):
        raise TypeError("structured backend must be callable or None")
    _structured_backend = backend


def _run(
    *,
    backend: Backend | None,
    backend_name: str,
    query: object,
    filters: object,
    top_k: object,
    trace_id: object,
    allowed_filters: frozenset[str],
) -> list[dict]:
    clean_query = contract.validate_query(query)
    clean_top_k = contract.validate_top_k(top_k)
    clean_trace_id = contract.validate_trace_id(trace_id)
    clean_filters = contract.validate_filters(filters, allowed_filters)

    if backend is None:
        raise contract.RetrievalUnavailableError(
            f"{backend_name} retrieval backend is not configured"
        )

    try:
        rows = backend(clean_query, clean_filters, clean_top_k, clean_trace_id)
    except contract.RetrievalUnavailableError:
        raise
    except Exception as exc:  # backend failures are not 'no evidence'
        raise contract.RetrievalUnavailableError(
            f"{backend_name} retrieval backend failed: {exc}"
        ) from exc

    if not isinstance(rows, list):
        raise ValueError(f"{backend_name} backend must return a list of evidence dicts")

    # The wrapper owns trace propagation and deterministic ranking so every
    # implementation behaves like the shared contract, even if a backend does not.
    normalized: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{backend_name} backend returned a non-dict evidence item")
        item = dict(row)
        metadata = dict(item.get("metadata") or {})
        metadata["trace_id"] = clean_trace_id
        item["metadata"] = metadata
        normalized.append(item)

    normalized.sort(key=contract.sort_key)
    normalized = normalized[:clean_top_k]
    for rank, item in enumerate(normalized, start=1):
        item["rank"] = rank

    contract.validate_results(normalized, clean_top_k)
    return normalized


def graph_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> list[dict]:
    """Search the configured graph backend using the shared Retrieval API v0."""
    return _run(
        backend=_graph_backend,
        backend_name="graph",
        query=query,
        filters=filters,
        top_k=top_k,
        trace_id=trace_id,
        allowed_filters=contract.GRAPH_FILTERS,
    )


def structured_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> list[dict]:
    """Search the configured structured backend using the shared Retrieval API v0."""
    return _run(
        backend=_structured_backend,
        backend_name="structured",
        query=query,
        filters=filters,
        top_k=top_k,
        trace_id=trace_id,
        allowed_filters=contract.STRUCTURED_FILTERS,
    )
