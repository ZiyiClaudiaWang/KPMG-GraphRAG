"""The structured arm applying fiscal_year and fiscal_period.

Both keys were accepted and dropped until now, so a question about FY2023
returned facts from any year. These check that the dates a label resolves to
are the ones the filer itself reported.

Only the last test needs a store.
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

import pytest

# Every module here puts the repo root on the path itself. Collection is
# alphabetical and nothing guarantees another file has already done it.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "structured"))

import structured_query_v1  # noqa: E402

D = datetime.date

# Two filers whose FY2026 are different stretches of time.
CALENDAR = {
    "MSFT": {"FY 2026": (D(2025, 7, 1), D(2026, 6, 30)),
             "Q3 2026": (D(2026, 1, 1), D(2026, 3, 31))},
    "AAPL": {"Q2 2026": (D(2025, 12, 28), D(2026, 3, 28))},
}


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    """Rank and filter without a database."""
    monkeypatch.setattr(structured_query_v1, "_calendar", lambda conn: CALENDAR)


def test_a_quarter_matches_only_periods_inside_it():
    """A 10-Q reports the quarter and the year to date side by side, and they
    end on the same day. Matching on the end alone answers "the third
    quarter" with nine months of it."""
    clause, params = structured_query_v1._fiscal_clause(
        None, {"ticker": "MSFT", "fiscal_year": 2026, "fiscal_period": "Q3"})
    assert "period_start" in clause and "period_end" in clause
    assert params == ["MSFT", "2026-01-01", "2026-01-01", "2026-03-31"]


def test_each_filer_gets_its_own_window():
    """FY2026 is a different stretch of time for each company, so a question
    naming no ticker cannot use one window for all of them."""
    clause, params = structured_query_v1._fiscal_clause(
        None, {"fiscal_year": 2026, "fiscal_period": "Q3"})
    # Only MSFT has a Q3 2026, so there is one window and it is theirs.
    assert clause.count("ticker = ?") == 1
    assert "MSFT" in params and "AAPL" not in params

    clause, params = structured_query_v1._fiscal_clause(
        None, {"fiscal_year": 2026, "fiscal_period": "Q2"})
    assert "AAPL" in params and "2025-12-28" in params


def test_a_period_no_filing_covers_matches_nothing_rather_than_everything():
    """Dropping the filter would return every year's facts and call them
    FY2019. Matching nothing says what is true: we hold no such filings."""
    clause, params = structured_query_v1._fiscal_clause(
        None, {"ticker": "MSFT", "fiscal_year": 2019})
    assert clause.strip() == "AND 1 = 0"
    assert params == []


def test_a_quarter_without_a_year_is_refused():
    """Q3 on its own names no stretch of time."""
    with pytest.raises(ValueError, match="fiscal_period needs fiscal_year"):
        structured_query_v1._fiscal_clause(None, {"fiscal_period": "Q3"})


def test_several_years_can_be_asked_for_at_once():
    clause, params = structured_query_v1._fiscal_clause(
        None, {"ticker": "MSFT", "fiscal_year": [2026, 2019]})
    assert clause.count("ticker = ?") == 1   # 2019 is not in the calendar
    assert "2025-07-01" in params


def test_several_periods_can_be_asked_for_at_once():
    # Three questions in the gold set ask for the year and a quarter together,
    # to derive the fourth quarter Microsoft does not file. Both arms used to
    # raise on the list rather than answer.
    clause, params = structured_query_v1._fiscal_clause(
        None, {"ticker": "MSFT", "fiscal_year": 2026, "fiscal_period": ["FY", "Q3"]})
    assert clause.count("ticker = ?") == 2
    assert "2026-06-30" in params    # the year ends here
    assert "2026-03-31" in params    # the quarter ends here


def test_one_period_still_makes_one_window():
    clause, _ = structured_query_v1._fiscal_clause(
        None, {"ticker": "MSFT", "fiscal_year": 2026, "fiscal_period": "Q3"})
    assert clause.count("ticker = ?") == 1


@pytest.mark.skipif(not os.environ.get("MSFT_FACT_DB_PATH"),
                    reason="needs a DuckDB fact store")
def test_the_third_quarter_returns_three_months_not_nine(monkeypatch):
    """Against the real corpus, with the real calendar."""
    monkeypatch.undo()
    rows = structured_query_v1.structured_search(
        "revenue", filters={"ticker": "MSFT", "fiscal_year": 2026,
                            "fiscal_period": "Q3"}, top_k=1, trace_id="t")
    assert rows
    meta = rows[0]["metadata"]
    days = (D.fromisoformat(str(meta["period_end"])[:10])
            - D.fromisoformat(str(meta["period_start"])[:10])).days
    assert 80 <= days <= 100, f"expected a quarter, got {days} days"
