"""Where a unit sits relative to the filing's HTML tables (step 4b, headings/tables.py).

`TableRows.where(unit)` returns the kind of table row the unit is in (`data`, `layout`,
`definition`), `outside` when it is in none, or None when the filing has no HTML; prefilter.py
decides from that. Every unit records it as `html_table` (null outside a table or without HTML).
"""

from __future__ import annotations

import bisect

LEADING = " |\t "          # pipes and spaces the export puts before a cell's text


class TableRows:
    """The HTML table rows of one section ([{start, end, kind}] in `content` positions), or None
    when the filing has no HTML."""

    def __init__(self, rows: list[dict] | None):
        self.known = rows is not None
        self.rows = sorted(rows or [], key=lambda r: r["start"])
        self.starts = [r["start"] for r in self.rows]

    def row_at(self, pos: int) -> dict | None:
        k = bisect.bisect_right(self.starts, pos) - 1
        if k >= 0 and self.rows[k]["start"] <= pos < self.rows[k]["end"]:
            return self.rows[k]
        return None

    def where(self, unit: dict) -> str | None:
        """data / layout / definition, `outside`, or None without HTML.

        In a row when the unit's first character of text (after leading "|" and spaces) lies in
        one, or when at least half of its characters do."""
        if not self.known:
            return None
        spans = unit["raw_spans"] or [[unit["char_start"], unit["char_end"]]]
        lead = len(unit["text"]) - len(unit["text"].lstrip(LEADING))
        row = self.row_at(spans[0][0] + lead)
        if row is not None:
            return row["kind"]
        total = inside = 0
        first = None
        for s, e in spans:
            total += e - s
            k = max(bisect.bisect_right(self.starts, s) - 1, 0)
            while k < len(self.rows) and self.rows[k]["start"] < e:
                ov = min(self.rows[k]["end"], e) - max(self.rows[k]["start"], s)
                if ov > 0:
                    inside += ov
                    first = first or self.rows[k]
                k += 1
        if first is not None and total and inside / total >= 0.5:
            return first["kind"]
        return "outside"
