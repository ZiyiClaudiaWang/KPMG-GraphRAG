"""What each arm does with a filter, reported rather than remembered.

These run on a bare checkout. None of them need a database.
"""

from __future__ import annotations

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


def test_the_report_names_what_an_arm_does_with_a_key_it_drops():
    """The two period filters are the reason this module exists: the Agent
    team sent them on every call and both arms dropped them in silence.

    This asserts the report can say so, not that it still has to. PR #90
    applies them in the structured arm, and this test passes either way —
    what it protects is that a dropped key is never reported as applied.
    """
    for arm in ("graph", "structured"):
        detail = vocabulary.filters(arm)
        if "unavailable" in detail:
            pytest.skip(f"{arm} arm is not importable here")
        for key in ("fiscal_year", "fiscal_period"):
            states = [s for s in (vocabulary.APPLIED, vocabulary.IGNORED,
                                  vocabulary.REFUSED) if key in detail[s]]
            assert len(states) == 1, f"{arm}/{key} is reported as {states}"


def test_a_key_the_arm_serves_without_storing_it_is_reported_as_applied():
    """fiscal_year and fiscal_period are not properties on any node.

    The graph arm turns them into a reporting_period_end window, so they
    narrow the query as much as ticker does. This module read the property
    map alone and reported them refused, which is the same defect as
    reporting max_hops applied, pointing the other way.
    """
    graph_backend = pytest.importorskip("graphrag.graph_backend")
    detail = vocabulary.filters("graph")
    if "unavailable" in detail:
        pytest.skip("graph arm is not importable here")
    for key in graph_backend._DERIVED_FILTERS:
        assert key in detail[vocabulary.APPLIED], key


def test_an_unknown_arm_is_refused_by_name():
    with pytest.raises(ValueError, match="no such arm"):
        vocabulary.filters("vector")


def test_the_summary_names_both_arms():
    text = vocabulary.summary()
    assert "graph" in text and "structured" in text
