"""Shared retrieval entry points and an Eval/CLI decision envelope.

The Agent-facing ``graph_search`` and ``structured_search`` functions keep the
existing list-of-evidence contract. ``search`` adds an internal envelope for
Eval and CLI consumers so no-answer calls can carry a request id plus an
explicit ``status`` / ``reason`` without breaking existing Agent callers.

Backends may continue returning a plain evidence list. A backend that can make
a stronger no-answer determination may instead return a dict with exactly
``status``, ``reason`` and ``results``.
"""

from __future__ import annotations

from collections.abc import Callable
import uuid
from typing import Any

from . import contract

Backend = Callable[[str, dict, int, str | None], object]

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


def _normalize_backend_outcome(raw: object, backend_name: str) -> tuple[str, str | None, list]:
    """Normalize a legacy evidence list or an explicit backend decision."""
    if isinstance(raw, list):
        if raw:
            return "answer", None, raw
        # The legacy list API cannot distinguish other abstention causes. An arm
        # with stronger knowledge should return an explicit decision dict below.
        return "abstain", "missing_fact", []

    if not isinstance(raw, dict):
        raise ValueError(
            f"{backend_name} backend must return a list of evidence dicts or a decision dict"
        )

    required = {"status", "reason", "results"}
    missing = required - set(raw)
    extra = set(raw) - required
    if missing:
        raise ValueError(
            f"{backend_name} backend decision missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise ValueError(
            f"{backend_name} backend decision has undocumented fields: {', '.join(sorted(extra))}"
        )

    status, reason = contract.validate_search_decision(raw["status"], raw["reason"])
    rows = raw["results"]
    if not isinstance(rows, list):
        raise ValueError(f"{backend_name} backend decision results must be a list")
    if status == "answer" and not rows:
        raise ValueError(f"{backend_name} backend answer decision must include evidence")
    if status != "answer" and rows:
        raise ValueError(f"{backend_name} backend non-answer decision must have empty results")
    return status, reason, rows


def _run(
    *,
    backend: Backend | None,
    backend_name: str,
    query: object,
    filters: object,
    top_k: object,
    trace_id: object,
    allowed_filters: frozenset[str],
) -> tuple[str, str | None, list[dict]]:
    clean_query = contract.validate_query(query)
    clean_top_k = contract.validate_top_k(top_k)
    clean_trace_id = contract.validate_trace_id(trace_id)
    clean_filters = contract.validate_filters(filters, allowed_filters)

    if backend is None:
        raise contract.RetrievalUnavailableError(
            f"{backend_name} retrieval backend is not configured"
        )

    try:
        raw = backend(clean_query, clean_filters, clean_top_k, clean_trace_id)
    except contract.RetrievalUnavailableError:
        raise
    except contract.FilterNotSupportedError:
        # Unsupported capability is not a store outage and should not be retried
        # as infrastructure noise.
        raise
    except Exception as exc:  # backend failures are not abstention
        raise contract.RetrievalUnavailableError(
            f"{backend_name} retrieval backend failed: {exc}"
        ) from exc

    status, reason, rows = _normalize_backend_outcome(raw, backend_name)

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
    return status, reason, normalized


def _tool_config(tool: str) -> tuple[Backend | None, str, frozenset[str]]:
    if tool == "graph":
        return _graph_backend, "graph", contract.GRAPH_FILTERS
    if tool == "structured":
        return _structured_backend, "structured", contract.STRUCTURED_FILTERS
    raise ValueError("tool must be 'graph' or 'structured'")


def search(
    query: str,
    *,
    tool: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return an Eval/CLI envelope with decision state and evidence results."""
    if request_id is None:
        request_id = str(uuid.uuid4())
    elif not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string or None")

    backend, backend_name, allowed_filters = _tool_config(tool)
    status, reason, results = _run(
        backend=backend,
        backend_name=backend_name,
        query=query,
        filters=filters,
        top_k=top_k,
        trace_id=trace_id,
        allowed_filters=allowed_filters,
    )
    contract.validate_search_decision(status, reason)
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "status": status,
        "reason": reason,
        "results": results,
    }


def graph_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> list[dict]:
    """Search graph retrieval using the unchanged Agent-facing evidence-list API."""
    _, _, results = _run(
        backend=_graph_backend,
        backend_name="graph",
        query=query,
        filters=filters,
        top_k=top_k,
        trace_id=trace_id,
        allowed_filters=contract.GRAPH_FILTERS,
    )
    return results


def structured_search(
    query: str,
    filters: dict | None = None,
    top_k: int = 10,
    trace_id: str | None = None,
) -> list[dict]:
    """Search structured retrieval using the unchanged Agent-facing evidence-list API."""
    _, _, results = _run(
        backend=_structured_backend,
        backend_name="structured",
        query=query,
        filters=filters,
        top_k=top_k,
        trace_id=trace_id,
        allowed_filters=contract.STRUCTURED_FILTERS,
    )
    return results
