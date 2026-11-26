"""Prefilter: units marked not_a_claim by rule, before the LLM (step 5) sees them.

  section_title    the Item title at the top of a section (set where the unit is made)
  heading          a heading (set where the unit is made)
  table_residue    a row of an HTML data table that is not a full sentence; without HTML, a row
                   or cell with a "|"; also, anywhere: over 30% digits, a footnote marker on its
                   own line, a table note listed in TABLE_NOTES
  definition       a row of an HTML term | definition table (MSFT metric glossaries); kept out of
                   Claims for now, for a possible metric glossary later
  cross_reference  "See ...", "Refer to ...", "incorporated by reference"
  list_intro       under segment.min_words words and ending in ":" ("Solutions include:",
                   "•TAC includes:"): it only opens what follows; split list items point back to it
                   (list_intro_id), and the LLM sees it as context
  too_short        under segment.min_words words and not a sentence: a fragment ("Number of",
                   "During", "Avg."); a short complete sentence ("No material items.", "Revenue
                   decreased slightly.") goes to the LLM. Never for a split list item (lists.py)

The HTML decides tables when the filing has it (step 4b, headings/tables.py, html_tables.py):
  data table    full sentences (footnotes) go on to the cross_reference check and the LLM; the
                rest is table_residue
  layout table  ordinary text: a table used to lay out bullets, bios or paragraphs
  outside       ordinary text; a "|" in the text no longer makes it a table (TTWO "Xbox Series X|S")
Without HTML (where = None) the "|" rule stays.
"""

from __future__ import annotations

import re

from ..clean import has_table_cells
from .sentences import TERMINAL

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


SENTENCE_MIN_WORDS = 6          # a full sentence in a data table: this many words and . ? ! at the end


def reads_as_sentence(text: str) -> bool:
    core = text.strip().strip("|").strip()
    return bool(TERMINAL.search(core)) and len(core.split()) >= SENTENCE_MIN_WORDS


def is_short_sentence(text: str) -> bool:
    """A complete sentence however short: at least 2 words, opens with a capital letter (after
    a quote) and ends in . ? !. "Avg." and "based considerations)." are not; neither are
    "** Filed herewith." or "(b)Use of Proceeds.", which open with a marker."""
    core = text.lstrip("\"“‘")
    return len(core.split()) >= 2 and core[:1].isupper() and bool(TERMINAL.search(core))


def is_cross_reference(text: str) -> bool:
    return bool(CROSS_REFERENCE.search(text)) and not re.search(
        r"\b(?:there have been (?:no )?material changes|have not materially changed)\b", text, re.I)


def prefilter(text: str, min_words: int, where: str | None = None) -> str | None:
    """Rule-based not_a_claim, applied before the LLM.

    where: the kind of HTML table row the unit sits in (data / layout / definition), "outside",
    or None when the filing has no HTML (html_tables.TableRows.where)."""
    if where == "definition":
        return "definition"
    if where == "data":
        if not reads_as_sentence(text):
            return "table_residue"
        return "cross_reference" if is_cross_reference(text) else None
    if ((where is None and has_table_cells(text)) or note_key(text) in TABLE_NOTE_KEYS
            or FOOTNOTE_MARKER.match(text)):
        return "table_residue"
    if is_cross_reference(text):
        return "cross_reference"
    digits = len(re.findall(r"\d", text))
    if digits / max(len(text), 1) > 0.3:
        return "table_residue"
    if len(text.split()) < min_words:
        if text.rstrip().endswith(":"):
            return "list_intro"
        return None if is_short_sentence(text) else "too_short"
    return None
