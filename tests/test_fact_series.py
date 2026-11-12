"""Joining facts that measure the same thing into a series over time.

The store holds Microsoft's Intelligent Cloud revenue for three fiscal years
and says nothing about the three belonging together. These check they are
joined, that unlike periods are not, and that a restated figure stays
visible. None of them need a database.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "structured"))

import fact_series  # noqa: E402

CLOUD = json.dumps([{
    "axis": "us-gaap:StatementBusinessSegmentsAxis",
    "member": "msft:IntelligentCloudMember",
    "type": "explicit",
}])
REVENUE = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def fact(start, end, value, accession="acc-1", filing_date="2026-07-29",
         concept=REVENUE, dimensions=CLOUD, period_type="duration", unit="USD"):
    return {
        "cik": "0000789019", "ticker": "MSFT", "concept_name": concept,
        "dimensions": dimensions, "unit": unit, "period_type": period_type,
        "period_start": start, "period_end": end, "numeric_value": str(value),
        "accession_number": accession, "filing_date": filing_date,
    }


# ---- what kind of period a fact covers ------------------------------------

def test_a_fiscal_year_is_annual_even_though_it_is_not_365_days():
    assert fact_series.period_bucket("duration", "2025-07-01", "2026-06-30") == "annual"
    assert fact_series.period_bucket("duration", "2023-07-01", "2024-06-30") == "annual"


def test_a_quarter_and_a_nine_month_stretch_are_told_apart():
    assert fact_series.period_bucket("duration", "2026-01-01", "2026-03-31") == "quarterly"
    assert fact_series.period_bucket(
        "duration", "2025-07-01", "2026-03-31"
    ) == "three quarters"


def test_a_balance_sheet_fact_is_measured_at_a_moment():
    assert fact_series.period_bucket("instant", None, "2026-06-30") == "instant"


def test_an_odd_period_is_named_by_its_length_not_guessed_at():
    assert fact_series.period_bucket("duration", "2025-09-18", "2025-09-18") == "1 day"
    assert fact_series.period_bucket("duration", "2026-01-01", "2026-02-14") == "45 days"


def test_a_missing_or_broken_date_is_not_a_crash():
    assert fact_series.period_bucket("duration", "2026-01-01", None) == "unknown"
    assert fact_series.period_bucket("duration", "2026-01-01", "not a date") == "unknown"


# ---- grouping -------------------------------------------------------------

def test_three_fiscal_years_become_one_series():
    series = fact_series.build_series([
        fact("2025-07-01", "2026-06-30", 137791000000),
        fact("2023-07-01", "2024-06-30", 87464000000),
        fact("2024-07-01", "2025-06-30", 106265000000),
    ])
    assert len(series) == 1
    assert [p["value"] for p in series[0]["points"]] == [
        Decimal("87464000000"), Decimal("106265000000"), Decimal("137791000000")
    ]


def test_the_series_is_ordered_oldest_first_whatever_order_it_arrives_in():
    series = fact_series.build_series([
        fact("2024-07-01", "2025-06-30", 2),
        fact("2025-07-01", "2026-06-30", 3),
        fact("2023-07-01", "2024-06-30", 1),
    ])
    assert [p["period_end"] for p in series[0]["points"]] == [
        "2024-06-30", "2025-06-30", "2026-06-30"
    ]


def test_a_quarter_does_not_join_the_annual_series():
    """A 10-Q reports both at once. Mixed, the quarter reads as a collapse."""
    series = fact_series.build_series([
        fact("2025-07-01", "2026-06-30", 137791000000),
        fact("2026-01-01", "2026-03-31", 34681000000),
        fact("2025-07-01", "2026-03-31", 98485000000),
    ])
    assert sorted(s["period_bucket"] for s in series) == [
        "annual", "quarterly", "three quarters"
    ]
    assert all(len(s["points"]) == 1 for s in series)


def test_a_segment_and_the_consolidated_figure_are_different_series():
    series = fact_series.build_series([
        fact("2025-07-01", "2026-06-30", 137791000000),
        fact("2025-07-01", "2026-06-30", 281724000000, dimensions="[]"),
    ])
    assert len(series) == 2


def test_the_same_dimensions_in_a_different_order_stay_one_series():
    axis_a = {"axis": "us-gaap:StatementBusinessSegmentsAxis", "member": "msft:A"}
    axis_b = {"axis": "us-gaap:StatementGeographicalAxis", "member": "country:US"}
    series = fact_series.build_series([
        fact("2024-07-01", "2025-06-30", 1, dimensions=json.dumps([axis_a, axis_b])),
        fact("2025-07-01", "2026-06-30", 2, dimensions=json.dumps([axis_b, axis_a])),
    ])
    assert len(series) == 1
    assert len(series[0]["points"]) == 2


def test_a_fact_with_no_value_is_left_out():
    series = fact_series.build_series([
        fact("2025-07-01", "2026-06-30", 137791000000),
        fact("2024-07-01", "2025-06-30", ""),
    ])
    assert len(series[0]["points"]) == 1


# ---- the same period, reported twice --------------------------------------

def test_two_filings_agreeing_make_one_point():
    series = fact_series.build_series([
        fact("2024-07-01", "2025-06-30", 106265000000, "acc-10q", "2026-04-29"),
        fact("2024-07-01", "2025-06-30", 106265000000, "acc-10k", "2026-07-29"),
    ])
    points = series[0]["points"]
    assert len(points) == 1
    assert points[0]["reported_by"] == 2
    assert points[0]["restated"] is False


def test_a_restated_figure_takes_the_latest_filing_and_keeps_the_old_one():
    series = fact_series.build_series([
        fact("2024-07-01", "2025-06-30", 933000000, "acc-10q", "2026-05-01"),
        fact("2024-07-01", "2025-06-30", 1315000000, "acc-10k", "2026-07-29"),
    ])
    point = series[0]["points"][0]
    assert point["value"] == Decimal("1315000000")
    assert point["accession_number"] == "acc-10k"
    assert point["restated"] is True
    assert point["superseded"][0]["value"] == Decimal("933000000")
    assert point["superseded"][0]["accession_number"] == "acc-10q"
    assert series[0]["restated"] is True


# ---- naming ---------------------------------------------------------------

def test_the_series_is_named_the_way_a_person_would_say_it():
    series = fact_series.build_series(
        [fact("2025-07-01", "2026-06-30", 137791000000)],
        ticker_of={"0000789019": "MSFT"},
    )
    assert series[0]["label"] == (
        "MSFT - Revenue From Contract With Customer Excluding Assessed Tax"
        " - Intelligent Cloud (annual, USD)"
    )


def test_a_series_with_no_ticker_still_has_a_name():
    series = fact_series.build_series([fact("2025-07-01", "2026-06-30", 1)])
    assert series[0]["label"].startswith("Revenue From Contract")
    assert series[0]["ticker"] is None
