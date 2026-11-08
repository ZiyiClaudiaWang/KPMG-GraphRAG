from __future__ import annotations

import sys
import types

import pytest

from graphrag import backends, contract, retrieve


@pytest.fixture(autouse=True)
def clear_backends():
    """Importing backends registers whatever this machine happens to have.

    Clear it around every test so the assertions are about the environment the
    test sets up, not the one it ran on.
    """
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)
    yield
    retrieve.configure_graph_backend(None)
    retrieve.configure_structured_backend(None)


@pytest.fixture
def bare_env(monkeypatch):
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_USER",
                 "NEO4J_PASSWORD", "NEO4J_DATABASE", "MSFT_FACT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)


def _fake_graph_backend(monkeypatch):
    """Stand in for graph_backend, which is still on its own branch."""
    module = types.ModuleType("graphrag.graph_backend")
    module.search = lambda query, filters, top_k, trace_id: []
    monkeypatch.setitem(sys.modules, "graphrag.graph_backend", module)
    return module


def test_no_environment_registers_nothing(bare_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # so the default store path does not exist
    status = backends.configure_from_env()
    assert status["graph"].startswith("skipped")
    assert status["structured"].startswith("skipped")


def test_nothing_registered_still_reads_as_unavailable(bare_env, monkeypatch, tmp_path):
    """The whole reason this lives outside retrieve.py: the failure stays loud."""
    monkeypatch.chdir(tmp_path)
    backends.configure_from_env()
    with pytest.raises(contract.RetrievalUnavailableError, match="not configured"):
        retrieve.graph_search("revenue")
    with pytest.raises(contract.RetrievalUnavailableError, match="not configured"):
        retrieve.structured_search("revenue")


def test_skipped_arms_name_what_is_missing(bare_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    status = backends.configure_from_env()
    assert "NEO4J_URI" in status["graph"]
    assert "MSFT_FACT_DB_PATH" in status["structured"]


def test_graph_registers_when_the_uri_is_set(bare_env, monkeypatch):
    _fake_graph_backend(monkeypatch)
    monkeypatch.setenv("NEO4J_URI", "bolt://example:7687")
    status = backends.configure_from_env()
    assert status["graph"].startswith("registered")
    assert retrieve.graph_search("revenue") == []


def test_graph_is_skipped_when_the_module_is_absent(bare_env, monkeypatch):
    monkeypatch.setitem(sys.modules, "graphrag.graph_backend", None)
    monkeypatch.setenv("NEO4J_URI", "bolt://example:7687")
    status = backends.configure_from_env()
    assert status["graph"].startswith("skipped")


def test_structured_registers_when_the_store_exists(bare_env, monkeypatch, tmp_path):
    store = tmp_path / "facts.duckdb"
    store.touch()
    monkeypatch.setenv("MSFT_FACT_DB_PATH", str(store))
    status = backends.configure_from_env()
    assert status["structured"].startswith("registered")


def test_structured_is_skipped_when_the_store_is_missing(bare_env, monkeypatch, tmp_path):
    monkeypatch.setenv("MSFT_FACT_DB_PATH", str(tmp_path / "absent.duckdb"))
    status = backends.configure_from_env()
    assert status["structured"].startswith("skipped")
    assert "absent.duckdb" in status["structured"]


def test_configure_is_safe_to_call_again(bare_env, monkeypatch, tmp_path):
    """A caller that sets the environment after import can pick it up."""
    monkeypatch.chdir(tmp_path)
    assert backends.configure_from_env()["structured"].startswith("skipped")
    store = tmp_path / "facts.duckdb"
    store.touch()
    monkeypatch.setenv("MSFT_FACT_DB_PATH", str(store))
    assert backends.configure_from_env()["structured"].startswith("registered")


def test_search_entry_points_are_re_exported():
    """The switch off the mock is one import, so both names live here."""
    assert backends.graph_search is retrieve.graph_search
    assert backends.structured_search is retrieve.structured_search
