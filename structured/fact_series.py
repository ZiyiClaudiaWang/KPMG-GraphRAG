"""Join facts that measure the same thing into a series over time.

A fact on its own is a number with a date attached. "137.791" means nothing
until you know it sits next to 106.265 and 87.464, and that the three are
Microsoft's Intelligent Cloud revenue for FY2026, FY2025 and FY2024. The
store holds all three, and nothing in it says they belong together.

This groups them. Facts join a series when they describe the same measure —
same filer, concept, dimensions and unit — and the series is then ordered by
period.

Two things make this less obvious than a `GROUP BY`:

**Period length belongs in the key.** A 10-Q reports Intelligent Cloud revenue
three ways at once: the quarter, the nine months to date, and the comparable
figures from a year earlier. Grouping on concept and dimensions alone puts a
quarter and a year in one series, where the quarter reads as a collapse.

**The same period arrives more than once.** 1,375 measure-periods in the
corpus appear in two or more filings, and 35 of those disagree on the value.
A filer restating a figure is not a duplicate to be dropped quietly: the
latest filing gives the point, and what it replaced is kept beside it.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

# Period lengths, in days, and what a reader calls them. A fiscal quarter runs
# 84 to 98 days and a fiscal year 350 to 380, so each is matched as a band.
PERIOD_BANDS = (
    (80, 100, "quarterly"),
    (160, 200, "half year"),
    (250, 290, "three quarters"),
    (350, 380, "annual"),
)

# A balance-sheet fact is measured at a moment, not over one. Those form
# series too — total assets at each year end — so they get their own bucket.
INSTANT = "instant"


def _dimension_pairs(dimensions: object) -> tuple:
    """Dimensions as a stable, comparable key.

    Sorted, because two filings may list the same axes in a different order,
    and a key that depends on ordering would split one series into two.
    """
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions or "[]")
        except json.JSONDecodeError:
            return ()
    pairs = []
    for entry in dimensions or []:
        if isinstance(entry, dict):
            pairs.append((str(entry.get("axis", "")), str(entry.get("member", ""))))
    return tuple(sorted(pairs))


def _days(start: object, end: object) -> int | None:
    """Days covered, from two ISO dates. None if either is missing or odd."""
    from datetime import date

    try:
        first = date.fromisoformat(str(start)[:10])
        last = date.fromisoformat(str(end)[:10])
    except (TypeError, ValueError):
        return None
    return (last - first).days + 1


def period_bucket(period_type: object, start: object, end: object) -> str:
    """What kind of period this is, so unlike periods do not share a series."""
    if str(period_type) == INSTANT or not start:
        return INSTANT
    days = _days(start, end)
    if days is None:
        return "unknown"
    for low, high, name in PERIOD_BANDS:
        if low <= days <= high:
            return name
    # Say the length rather than guess at a name for it. A 45-day transition
    # period is real, and calling it quarterly would be wrong. So is a
    # one-day period: that is how a filer tags an acquisition's adjustments.
    return "1 day" if days == 1 else f"{days} days"


def series_key(fact: dict) -> tuple:
    """What makes two facts the same measure."""
    return (
        str(fact.get("cik") or ""),
        str(fact.get("concept_name") or ""),
        _dimension_pairs(fact.get("dimensions")),
        str(fact.get("unit") or ""),
        period_bucket(
            fact.get("period_type"), fact.get("period_start"), fact.get("period_end")
        ),
    )


def _value(fact: dict) -> Decimal | None:
    raw = fact.get("numeric_value")
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def series_label(key: tuple, ticker: str | None = None) -> str:
    """The series, named the way a person would say it."""
    import concept_match
    import segments

    _, concept, dimensions, unit, bucket = key
    parts = [ticker] if ticker else []
    parts.append(" ".join(w.capitalize() for w in concept_match.concept_words(concept)))
    parts += [segments.label_for(member) for _, member in dimensions]
    tail = bucket if not unit else f"{bucket}, {unit}"
    return " - ".join(p for p in parts if p) + f" ({tail})"


def build_series(records: list, ticker_of: dict | None = None) -> list:
    """Group facts into series, newest period last.

    Each point carries the filings that reported it. Where they disagree the
    latest filing wins and `superseded` holds what it replaced, so a restated
    figure is visible rather than silently resolved.
    """
    grouped: dict = {}
    for fact in records:
        value = _value(fact)
        if value is None:
            continue
        key = series_key(fact)
        period = (str(fact.get("period_start") or ""), str(fact.get("period_end") or ""))
        grouped.setdefault(key, {}).setdefault(period, []).append(fact)

    built = []
    for key, periods in grouped.items():
        points = []
        for (start, end), facts in periods.items():
            # Latest filing first, so the current statement of the period leads.
            facts = sorted(
                facts, key=lambda f: str(f.get("filing_date") or ""), reverse=True
            )
            current, value = facts[0], _value(facts[0])
            superseded = [
                {
                    "value": _value(f),
                    "accession_number": f.get("accession_number"),
                    "filing_date": f.get("filing_date"),
                }
                for f in facts[1:]
                if _value(f) != value
            ]
            points.append({
                "period_start": start or None,
                "period_end": end,
                "value": value,
                "accession_number": current.get("accession_number"),
                "filing_date": current.get("filing_date"),
                "reported_by": len(facts),
                "restated": bool(superseded),
                "superseded": superseded,
            })

        points.sort(key=lambda p: (p["period_end"], p["period_start"] or ""))
        cik, concept, dimensions, unit, bucket = key
        ticker = (ticker_of or {}).get(cik)
        built.append({
            "cik": cik,
            "ticker": ticker,
            "concept": concept,
            "dimensions": [{"axis": a, "member": m} for a, m in dimensions],
            "unit": unit or None,
            "period_bucket": bucket,
            "label": series_label(key, ticker),
            "points": points,
            "restated": any(p["restated"] for p in points),
        })

    built.sort(key=lambda s: (s["cik"], s["concept"], s["period_bucket"]))
    return built
