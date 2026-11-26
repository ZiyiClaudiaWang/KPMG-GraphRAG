"""Prefilter: units marked not_a_claim by rule, before the LLM (step 5) sees them.

  section_title    the Item title at the top of a section (set where the unit is made)
  heading          a heading or category heading (set where the unit is made)
  table_residue    a table row or cell, over 30% digits, a footnote marker on its own line, or a
                   table note listed in TABLE_NOTES
  cross_reference  "See ...", "Refer to ...", "incorporated by reference"
  too_short        under segment.min_words words; never for a split list item (lists.py)
"""

from __future__ import annotations

import re

from ..clean import has_table_cells

CROSS_REFERENCE = re.compile(
    r"^(see|refer to)\b|incorporated (herein )?by reference|^\(?(none|not applicable)\.?\)?$",
    re.IGNORECASE,
)


def note_key(text: str) -> str:
    """'NM - Not Meaningful.' and 'nm - not meaningful' match the same table note."""
    return " ".join(text.lower().rstrip(".").split())


# Table legends and footnotes, listed by hand: a paragraph that is exactly one of these
# (ignoring case, extra spaces and a final period) is table_residue, never a heading.
# Add a line when a new one turns up.
TABLE_NOTES = [
    "bp - basis point",                          # MCK
    "bp - basis points",                         # MCK (plural in most filings)
    "Percentages may not add due to rounding",   # NTAP
    "NM - Not Meaningful",                       # NTAP
    "(3)Not meaningful (NM)",                    # MDT
    "Shares that May",                           # MCK share-repurchase table header
    "Shares",                                    # MCK 10-Q share-repurchase table header
    # Table cells cut out of a table (one "|" left), README Remaining issues 2.3
    "(Dollars and gallons in thousands, except per share amounts) |",   # CASY
    "Programs (3) (4) |",                                               # MCK
    "Satya Nadella |", "Chief Executive Officer |",                     # MSFT executive table
    "Amy E. Hood |", "Executive Vice President and Chief Financial Officer |",
    "Alice L. Jolla |", "Corporate Vice President and Chief Accounting Officer |",
]
TABLE_NOTE_KEYS = frozenset(note_key(t) for t in TABLE_NOTES)
# A footnote marker on its own line under a table: "(a)", "(1)". Not a heading.
FOOTNOTE_MARKER = re.compile(r"^\([a-z0-9]{1,2}\)$", re.IGNORECASE)


def prefilter(text: str, min_words: int) -> str | None:
    """Rule-based not_a_claim, applied before the LLM."""
    if has_table_cells(text) or note_key(text) in TABLE_NOTE_KEYS or FOOTNOTE_MARKER.match(text):
        return "table_residue"
    if CROSS_REFERENCE.search(text) and not re.search(
            r"\b(?:there have been (?:no )?material changes|have not materially changed)\b", text, re.I):
        return "cross_reference"
    digits = len(re.findall(r"\d", text))
    if digits / max(len(text), 1) > 0.3:
        return "table_residue"
    if len(text.split()) < min_words:
        return "too_short"
    return None
