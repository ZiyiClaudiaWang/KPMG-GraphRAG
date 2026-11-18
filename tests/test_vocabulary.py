"""What each arm does with a filter, reported rather than remembered.

These run on a bare checkout. The ticker test needs a fact store and skips
without one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Every module here puts the repo root on the path itself. Collection is
# alphabetical and nothing guarantees another file has already done it.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graphrag import contract, vocabulary  # noqa: E402


def test_every_offered_filter_is_classified_exactly_once():
    """A key the contract offers an arm has to land in one of the three
    states. One that lands in none is a key nobody decided about."""
    offered = {"graph": contract.GRAPH_FILTERS,
               "structured": contract.STRUCTURED_FILTERS}
    for arm, keys in offered.items():
        detail = vocabulary.filters(arm)
        if "unavailable" in detail:
            pytest.skip(f"{arm} arm is not importable here")
        classified = (detail[vocabulary.APPLIED]
                      + detail[vocabulary.IGNORED]
                      + detail[vocabulary.REFUSED])
        assert sorted(classified) == sorted(keys)
        assert len(classified) == len(set(classified))


def test_the_two_period_filters_are_still_ignored_by_both_arms():
    """This is the defect the Agent team hit: they send these on every call
    and neither arm applies either one.

    When that is fixed, this test fails and should be rewritten to assert the
    new behaviour. It is here so the fix is a decision rather than a drift.
    """
    for arm in ("graph", "structured"):
        detail = vocabulary.filters(arm)
        if "unavailable" in detail:
            pytest.skip(f"{arm} arm is not importable here")
        assert "fiscal_year" in detail[vocabulary.IGNORED]
        assert "fiscal_period" in detail[vocabulary.IGNORED]


def test_an_unknown_arm_is_refused_by_name():
    with pytest.raises(ValueError, match="no such arm"):
        vocabulary.filters("vector")


def test_no_store_says_so_rather_than_reporting_an_empty_corpus(monkeypatch):
    """An empty list would read as "we hold no companies" and be acted on."""
    monkeypatch.delenv("MSFT_FACT_DB_PATH", raising=False)
    with pytest.raises(RuntimeError, match="MSFT_FACT_DB_PATH"):
        vocabulary.tickers()


@pytest.mark.skipif(not os.environ.get("MSFT_FACT_DB_PATH"),
                    reason="needs a DuckDB fact store")
def test_the_ticker_list_comes_from_the_store():
    held = vocabulary.tickers()
    assert held == sorted(held)
    assert all(t and t.isupper() for t in held)


def test_the_summary_names_both_arms():
    text = vocabulary.summary()
    assert "graph" in text and "structured" in text
