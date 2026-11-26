"""Headings in step 4: which lines are headings, and the heading path above each unit.

Four questions, answered in three places:

  A. Is this line a heading?   here, from the plain text (the only place that decides it):
       line heading     is_heading_like: a short line without sentence punctuation
                        ("Results of Operations"); is_colon_heading: a short title-case line
                        ending in ":" that no list follows ("Executive Summary:")
       run-in heading   is_period_heading: a short title-case first sentence ending in "."
                        ("Flexible Work.")
       never            a line ending on DANGLING_WORDS ("Number of"), a table note
                        (prefilter.TABLE_NOTES), a footnote marker ("(a)")
  B. How deep is it?           step 4b (src/headings/levels.py) from the filing's HTML, read
                               back here through `Levels`:
       1, 2, ...        a heading at that depth
       0                NOT_A_HEADING: a column header, "(In millions)", a table of contents;
                        the unit is still a heading unit but changes no path
       -1               SENTENCE_LEVEL: the line is a sentence (a lead sentence styled as a
                        heading); it is split and labelled like any paragraph
       not listed       the level of the heading before it; without the file, every heading is
                        level 1 and replaces the one before it
  C. Category or sub-heading?  Item 1A only (is_risk_category): level 1 is a risk category
                               (`category`); deeper headings are sub-headings. A heading with no
                               level is a category when it looks like one (is_category).
  D. Lead sentences are not headings: they change no path. See link_leads.py; the rule that
     proposes them in Item 1A (`rule_heading`) is in risk.py.

`HeadingPath` holds the open headings; every unit copies its `category`, `subheading`
(the deepest open heading), `heading_path` and `section_path` from it.
"""

from __future__ import annotations

import re

from ..clean import DANGLING_WORDS, raw_spans
from ..headings.candidates import NOT_A_HEADING, SENTENCE_LEVEL
from .lists import is_bullet
from .prefilter import FOOTNOTE_MARKER, TABLE_NOTE_KEYS, note_key
from .sentences import TERMINAL

SECTION_TITLE = re.compile(r"^item\s+\d{1,2}[a-c]?\b", re.IGNORECASE)
RISK_CATEGORY_START = re.compile(r"^risks?\s+(?:related|relating)\s+to\b", re.IGNORECASE)


# ---------- A. is this line a heading? ----------

def is_heading_like(text: str) -> bool:
    """A short line without sentence punctuation: a category or sub-heading.

    A line ending on "of" or "the" is a piece of a table header ("Number of"), not a heading.
    """
    words = text.split()
    return (len(text) <= 150 and "|" not in text and not is_bullet(text)
            and not TERMINAL.search(text) and not text.endswith(":")
            and bool(words) and words[-1].lower() not in DANGLING_WORDS
            and note_key(text) not in TABLE_NOTE_KEYS and not FOOTNOTE_MARKER.match(text))


def is_colon_heading(text: str) -> bool:
    """A short title-case line ending in ":" ("Executive Summary:", "OPERATIONS:",
    "Sales and Marketing (in millions, except percentages):").

    At most 10 words outside parentheses, the first capitalised, and every word of 4 or more
    letters capitalised, so "Our cost of revenues consists of:" and "The breakdown of Revenues
    is as follows:" stay sentences. The caller also checks that no bulleted list follows:
    then the line is a list intro.
    """
    if not text.endswith(":") or "|" in text:
        return False
    core = re.sub(r"\([^()]*\)", " ", text[:-1])
    words = re.findall(r"[A-Za-z][A-Za-z’'&-]*", core)
    return (1 <= len(core.split()) <= 10 and bool(words) and words[0][0].isupper()
            and all(w[0].isupper() for w in words if len(w) >= 4))


def is_line_heading(text: str, list_follows: bool) -> bool:
    """A paragraph that is a heading by its text alone: heading-like, or a colon heading that
    no list follows (with a list after it, the line is the list's intro)."""
    return is_heading_like(text) or (is_colon_heading(text) and not list_follows)


def is_period_heading(text: str) -> bool:
    """A short title-case sentence ending in a period: "Flexible Work.", "Zynga.", "2K."

    At most 5 words, and every word of 4 or more letters capitalised, so
    "Develop Robust Player Relationships." counts but "Revenue increased." does not.
    """
    if not text.endswith(".") or not 1 <= len(text.rstrip(".").split()) <= 5:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z’'-]*", text)
    return bool(words) and words[0][0].isupper() and all(w[0].isupper() for w in words if len(w) >= 4)


def is_category(text: str) -> bool:
    """Category headings are in capitals or title case ("Macroeconomic and Industry Risks"),
    or start "Risks related to" in any case (TTWO "Risks relating to our business and industry")."""
    words = re.findall(r"[A-Za-z][A-Za-z’'-]*", text)
    if not words:
        return False
    if RISK_CATEGORY_START.match(text):
        return True
    if text.upper() == text:
        return True
    capitalised = sum(w[0].isupper() for w in words)
    return capitalised / len(words) >= 0.5


# ---------- B. levels from step 4b ----------

def heading_id(sec: dict, paragraph: dict, start: int) -> str:
    """The id of a heading for heading levels: accession|section_key|its start in `content`."""
    spans = raw_spans(paragraph, start, start + 1)
    return f"{sec['accession_number']}|{sec['section_key']}|{spans[0][0] if spans else start}"


class Levels:
    """The step 4b levels of one section's headings (heading id -> level); empty without step 4b."""

    def __init__(self, sec: dict, levels: dict[str, int] | None):
        self.sec = sec
        self.levels = levels or {}

    def of(self, paragraph: dict, start: int) -> int | None:
        """The level the file gives the heading starting at `start`, or None if not listed."""
        return self.levels.get(heading_id(self.sec, paragraph, start))

    def is_sentence(self, paragraph: dict, start: int) -> bool:
        """The file says this heading-like line is a sentence of the text (level -1)."""
        return self.of(paragraph, start) == SENTENCE_LEVEL

    def is_risk_category(self, name: str, paragraph: dict, start: int) -> bool:
        """C: a risk category heading: level 1 in the file, else by its look."""
        level = self.of(paragraph, start)
        return level == 1 if level is not None else is_category(name)

    def heading_level(self, paragraph: dict, start: int, sibling: int) -> int:
        """The level of a sub-heading: from the file; not listed there, the sibling's level;
        without the file, 1 (each heading replaces the one before it)."""
        level = self.of(paragraph, start)
        if level is not None:
            return level
        return sibling if self.levels else 1


# ---------- the heading path ----------

class HeadingPath:
    """The risk category and the open sub-headings above the current unit."""

    def __init__(self) -> None:
        self.category: str | None = None
        self.subheading: str | None = None
        self.stack: list[tuple[int, str]] = []      # (level, heading text), outermost first

    @property
    def sibling(self) -> int:
        """The level a heading the levels file does not list gets: that of the heading before it."""
        return self.stack[-1][0] if self.stack else 1

    def names(self) -> list[str]:
        return [h for _, h in self.stack]

    def open(self, name: str, level: int) -> int:
        """Open a sub-heading at `level`, closing those at the same level or deeper. Level 0 (not
        a heading) changes nothing. Returns the level."""
        if level == NOT_A_HEADING:
            return level
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, name))
        self.subheading = name
        return level

    def set_category(self, name: str) -> None:
        """A new risk category closes every sub-heading."""
        self.category, self.subheading = name, None
        self.stack.clear()

    def close_subheadings(self) -> None:
        """A rule-proposed lead in Item 1A (risk.rule_lead_strength) ends the sub-headings
        above it; the category stays."""
        self.subheading = None
        self.stack.clear()
