"""Turn a company's own fiscal labels into the dates a filing actually covers.

The contract offers `fiscal_year` and `fiscal_period` to every arm, and until
now no arm applied either. They were accepted and dropped, so a question about
FY2023 came back with facts from any year, filtered-looking and unfiltered.
That is not a corner: 129 of the 140 questions in Eval's benchmark carry one
of these keys.

The reason nobody applied them is that a fiscal year is not a calendar year
and the year end moves by company. Microsoft's ends 30 June, Apple's in late
September, Nike's in May. Several filers use a 52 or 53 week year, so the end
date moves by days from one year to the next — Micron's FY2026 ends on 3
September because that year had 53 weeks, while FY2025 ended on 28 August.

So the calendar is not hard-coded. It is read out of the filings themselves:

* **A fiscal year** is any duration fact covering 350 to 380 days. Its start
  and end are the year's real boundaries, 53-week years included.
* **Its label** is the calendar year the period ends in. Microsoft's year
  ending 30 June 2026 is FY2026; Apple's ending 27 September 2025 is FY2025.
* **A quarter** is a duration of 80 to 100 days. Which quarter it is comes
  from how far its start sits from the fiscal year's start.

The label rule has one known limit. A filer whose year ends in January or
February may call that year by the year it began, and this would call it by
the year it ended. No filer in the corpus ends a year then, so the rule holds
here; it is the first thing to check when the corpus widens.

Durations are matched by range rather than exactly because a 52/53 week year
is never 365 days, and a quarter is never 91.
"""

from __future__ import annotations

import datetime

# A duration this long is the fiscal year itself. 53-week years run to 371
# days, and a year ending on a weekday can fall a few days either side.
YEAR_DAYS = (350, 380)

# A duration this long is one quarter. Not a half year (about 182) and not
# nine months (about 273), both of which a 10-Q also reports.
QUARTER_DAYS = (80, 100)

# Nominal length of a fiscal quarter, for working out which one a period is.
_QUARTER_LENGTH = 365.25 / 4


def _as_date(value: object) -> datetime.date | None:
    """Dates arrive as text from one store and as dates from another."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def duration_days(start: object, end: object) -> int | None:
    start, end = _as_date(start), _as_date(end)
    if start is None or end is None:
        return None
    return (end - start).days


def is_fiscal_year(start: object, end: object) -> bool:
    days = duration_days(start, end)
    return days is not None and YEAR_DAYS[0] <= days <= YEAR_DAYS[1]


def is_quarter(start: object, end: object) -> bool:
    days = duration_days(start, end)
    return days is not None and QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def year_label(end: object) -> int | None:
    """Which fiscal year a period ending on this date belongs to."""
    end = _as_date(end)
    return end.year if end else None


def quarter_index(year_start: object, quarter_start: object) -> int | None:
    """Which quarter of its fiscal year a period starting here is.

    Measured from the start rather than the end, because a 53-week year adds
    its extra week to the last quarter and would otherwise push Q4 out.
    """
    year_start, quarter_start = _as_date(year_start), _as_date(quarter_start)
    if year_start is None or quarter_start is None:
        return None
    index = round((quarter_start - year_start).days / _QUARTER_LENGTH) + 1
    return index if 1 <= index <= 4 else None


def normalize_period(value: object) -> str | None:
    """`Q3`, `q3` and `3` all mean the third quarter. `FY` means the year."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in {"FY", "FULL YEAR", "ANNUAL", "YEAR"}:
        return "FY"
    if text.startswith("Q"):
        text = text[1:]
    if text in {"1", "2", "3", "4"}:
        return f"Q{text}"
    raise ValueError(
        f"fiscal_period must be Q1, Q2, Q3, Q4 or FY, not {value!r}"
    )


def build_calendar(periods) -> dict:
    """Read each filer's fiscal calendar out of the periods it reported.

    `periods` is any iterable of `(ticker, period_start, period_end)`. Every
    arm can produce that from its own store, so the rules live here once and
    the reading stays with whoever holds the data.

    Returns `{ticker: {"Q3 2026": (start, end), "FY 2026": (start, end)}}`,
    keyed by the labels a caller asks in.
    """
    years: dict[str, dict[int, tuple]] = {}
    quarters: dict[str, list[tuple]] = {}

    for ticker, start, end in periods:
        if not ticker:
            continue
        start_date, end_date = _as_date(start), _as_date(end)
        if start_date is None or end_date is None:
            continue
        if is_fiscal_year(start_date, end_date):
            label = year_label(end_date)
            # A filing and its comparatives report the same year more than
            # once. They agree, so the first one wins.
            years.setdefault(ticker, {}).setdefault(label, (start_date, end_date))
        elif is_quarter(start_date, end_date):
            quarters.setdefault(ticker, []).append((start_date, end_date))

    calendar: dict[str, dict[str, tuple]] = {}
    for ticker, by_year in years.items():
        entries = {f"FY {year}": span for year, span in by_year.items()}

        # The year in progress has quarters but no annual period yet, because
        # it has not ended. Its start is the day after the last one ended, so
        # its quarters can still be numbered. No `FY` entry is added for it:
        # we know where the year began and not where it ends, and a 52/53
        # week filer makes guessing that wrong by days.
        spans = dict(by_year)
        last_end = max(end for _, end in by_year.values())
        in_progress_start = last_end + datetime.timedelta(days=1)
        spans[year_label(in_progress_start + datetime.timedelta(days=364))] = (
            in_progress_start, in_progress_start + datetime.timedelta(days=380))

        for q_start, q_end in quarters.get(ticker, []):
            for year, (y_start, y_end) in spans.items():
                if y_start <= q_start <= y_end:
                    index = quarter_index(y_start, q_start)
                    if index:
                        entries.setdefault(f"Q{index} {year}", (q_start, q_end))
                    break
        calendar[ticker] = entries
    return calendar


def window(calendar: dict, ticker: object, fiscal_year: object,
           fiscal_period: object = None) -> tuple | None:
    """The dates a filer's own fiscal label covers, or None if unknown.

    None means the calendar cannot answer, which is not the same as the
    period being empty. A caller that treats it as empty would report no
    evidence for a year we simply have no filings for.
    """
    if fiscal_year is None:
        return None
    try:
        year = int(str(fiscal_year)[:4])
    except (TypeError, ValueError):
        raise ValueError(f"fiscal_year must be a year, not {fiscal_year!r}")

    period = normalize_period(fiscal_period) or "FY"
    entries = calendar.get(str(ticker or ""))
    if not entries:
        return None
    return entries.get(f"{period} {year}")
