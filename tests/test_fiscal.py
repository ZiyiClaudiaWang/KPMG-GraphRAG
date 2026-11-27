"""Reading a filer's fiscal calendar out of the periods it reported.

These run on a bare checkout. The corpus test needs a fact store and skips
without one.
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

import fiscal  # noqa: E402

D = datetime.date

# Microsoft FY2026 and its four quarters, as filed.
MSFT = [
    ("MSFT", D(2025, 7, 1), D(2026, 6, 30)),
    ("MSFT", D(2025, 7, 1), D(2025, 9, 30)),
    ("MSFT", D(2025, 10, 1), D(2025, 12, 31)),
    ("MSFT", D(2026, 1, 1), D(2026, 3, 31)),
    ("MSFT", D(2026, 4, 1), D(2026, 6, 30)),
]


def test_a_fiscal_year_is_read_from_the_filing_not_assumed():
    calendar = fiscal.build_calendar(MSFT)
    assert calendar["MSFT"]["FY 2026"] == (D(2025, 7, 1), D(2026, 6, 30))
    assert calendar["MSFT"]["Q3 2026"] == (D(2026, 1, 1), D(2026, 3, 31))


def test_a_fifty_three_week_year_is_still_one_year():
    """Micron's FY2026 runs 370 days and ends in September, where FY2025
    ended in August. A rule keyed on the month would put them in different
    years."""
    micron = [("MU", D(2025, 8, 29), D(2026, 9, 3)),
              ("MU", D(2026, 2, 27), D(2026, 5, 28))]
    calendar = fiscal.build_calendar(micron)
    assert calendar["MU"]["FY 2026"] == (D(2025, 8, 29), D(2026, 9, 3))
    assert calendar["MU"]["Q3 2026"] == (D(2026, 2, 27), D(2026, 5, 28))


def test_a_year_to_date_period_is_neither_a_year_nor_a_quarter():
    """A 10-Q reports the quarter and the year to date side by side. Nine
    months is 272 days and must not be mistaken for either."""
    assert not fiscal.is_fiscal_year(D(2025, 7, 1), D(2026, 3, 31))
    assert not fiscal.is_quarter(D(2025, 7, 1), D(2026, 3, 31))
    assert fiscal.is_quarter(D(2026, 1, 1), D(2026, 3, 31))
    assert fiscal.is_fiscal_year(D(2025, 7, 1), D(2026, 6, 30))


def test_quarters_of_a_year_still_running_are_numbered():
    """The year in progress has quarters and no annual period, because it has
    not ended. Without this, every question about the current year misses."""
    apple = [("AAPL", D(2024, 9, 29), D(2025, 9, 27)),   # FY2025, complete
             ("AAPL", D(2025, 12, 28), D(2026, 3, 28))]  # Q2 FY2026, in progress
    calendar = fiscal.build_calendar(apple)
    assert calendar["AAPL"]["Q2 2026"] == (D(2025, 12, 28), D(2026, 3, 28))
    # And no FY 2026: we know where that year began, not where it ends.
    assert "FY 2026" not in calendar["AAPL"]


def test_the_year_is_labelled_by_when_it_ends():
    assert fiscal.year_label(D(2026, 6, 30)) == 2026
    assert fiscal.year_label(D(2025, 9, 27)) == 2025


def test_a_period_the_calendar_cannot_place_is_none_not_empty():
    """None means we cannot answer. Treated as an empty range it would report
    no evidence for a year we simply hold no filings for."""
    calendar = fiscal.build_calendar(MSFT)
    assert fiscal.window(calendar, "MSFT", 2019, "Q1") is None
    assert fiscal.window(calendar, "NVDA", 2026, "Q1") is None
    assert fiscal.window(calendar, "MSFT", None) is None


def test_the_quarter_can_be_written_several_ways():
    for given in ("Q3", "q3", "3"):
        assert fiscal.normalize_period(given) == "Q3"
    assert fiscal.normalize_period("FY") == "FY"
    with pytest.raises(ValueError, match="fiscal_period"):
        fiscal.normalize_period("Q9")


def test_several_periods_can_be_asked_for_at_once():
    # Deriving the quarter nobody files needs the year and a quarter in the
    # same question, which used to be refused outright.
    assert fiscal.normalize_periods(["FY", "Q3"]) == ["FY", "Q3"]
    assert fiscal.normalize_periods("Q3") == ["Q3"]
    assert fiscal.normalize_periods(None) == [None]
    # Two spellings of one quarter are one window, and the order asked for
    # is the order returned.
    assert fiscal.normalize_periods(["q3", "3", "FY"]) == ["Q3", "FY"]
    with pytest.raises(ValueError, match="fiscal_period"):
        fiscal.normalize_periods([])
    with pytest.raises(ValueError, match="fiscal_period"):
        fiscal.normalize_periods(["Q3", "Q9"])


def test_a_fiscal_year_that_is_not_a_year_is_refused():
    with pytest.raises(ValueError, match="fiscal_year"):
        fiscal.window({}, "MSFT", "not a year")


@pytest.mark.skipif(not os.environ.get("MSFT_FACT_DB_PATH"),
                    reason="needs a DuckDB fact store")
def test_the_calendar_builds_from_the_real_corpus():
    import duckdb

    conn = duckdb.connect(os.environ["MSFT_FACT_DB_PATH"], read_only=True)
    rows = conn.execute(
        "SELECT DISTINCT ticker, period_start, period_end FROM financial_facts_v1 "
        "WHERE period_type = 'duration' AND period_start IS NOT NULL "
        "AND period_end IS NOT NULL"
    ).fetchall()
    conn.close()

    calendar = fiscal.build_calendar(rows)
    # Every filer in the corpus gets a calendar, and the one we can check by
    # hand is right: Microsoft's third quarter of FY2026 is January to March.
    assert len(calendar) >= 10
    assert fiscal.window(calendar, "MSFT", 2026, "Q3") == (D(2026, 1, 1), D(2026, 3, 31))
