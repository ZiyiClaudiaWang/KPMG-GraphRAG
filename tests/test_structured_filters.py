"""The structured arm accepts every filter the contract allows.

Eval fills `filters` on each gold question from the contract's key list. When
the arm accepted only seven of the thirteen, 148 of the 155 round-1 questions
raised before any retrieval happened -- and nobody saw it, because the mock
accepts all thirteen and quietly ignores four.

These tests pin both halves: the key sets agree, and the keys we do not apply
are accepted rather than raised on.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "structured"))

from graphrag import contract  # noqa: E402
import structured_query_v1 as sq  # noqa: E402


def test_supported_filters_match_the_contract():
    """A key the contract allows must not raise in the arm that implements it."""
    assert sq.SUPPORTED_FILTERS == set(contract.STRUCTURED_FILTERS)


def test_ignored_filters_are_a_subset_of_supported():
    assert sq.IGNORED_FILTERS <= sq.SUPPORTED_FILTERS


def test_the_keys_eval_sends_are_all_accepted():
    """form, fiscal_year and fiscal_period appear on 148 of 155 gold questions."""
    for key in ("form", "fiscal_year", "fiscal_period"):
        assert key in sq.SUPPORTED_FILTERS


@pytest.mark.parametrize("key", sorted({"nonsense", "chunk_id", "query"}))
def test_a_key_outside_the_contract_still_raises(key, monkeypatch):
    """Widening the set must not turn it into "accept anything"."""
    with pytest.raises(ValueError, match="unsupported filter"):
        sq.query_database("revenue", filters={key: "x"})


def test_a_renamed_key_is_hinted_not_guessed():
    with pytest.raises(ValueError, match=r"use 'accession_number'"):
        sq.query_database("revenue", filters={"accession": "x"})


def test_renamed_hints_point_at_keys_we_accept():
    """A hint naming a key we would also reject would send the caller in circles."""
    for old, new in sq.RENAMED_FILTERS.items():
        if new == "query":       # not a filter at all, it is the first argument
            continue
        assert new in sq.SUPPORTED_FILTERS, f"{old} hints at {new}, which we reject"
