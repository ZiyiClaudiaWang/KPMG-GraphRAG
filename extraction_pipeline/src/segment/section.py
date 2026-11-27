"""Split one cleaned section into units, paragraph by paragraph, in source order.

Each paragraph goes to the first reader in READERS that takes it; the order is the priority:

  1. section_title    the Item title opening the section
  2. heading_line     a heading by its text (headings.is_line_heading), not a sentence by its level
                      (in Item 1A, level 1 is a risk category); bullets right under it are a list
                      with the heading as intro
  3. group_list       a label row and the short names under it (PH Item 1 market tables:
                      "Engineered Materials Group: | • Aerospace", "•Agriculture", ...,
                      "Life sciences | • Microelectronics"): one list_group per label
  3b. list_block      an intro (ending in ":" or announcing "the following") and its list items;
                      a run-in heading opening the intro paragraph is a heading too (PH "Aerospace
                      Systems Segment. Our ... products include:")
  4. run_in_heading   a paragraph opening with a period heading ("Flexible Work. ..."): title case
                      by its text, or, from the filing's HTML, a short styled first sentence in any
                      case (TTWO "Player acquisition costs.", step 3b headings/leads.py)
  5. rule_lead        Item 1A: a paragraph the rules propose as a risk lead (rule_heading)
  6. paragraph        anything else: split into sentences; a stray bullet paragraph opening
                      with a title-case period heading is a run-in heading too (WMT
                      "•Opportunity. Retail can be ..."); a sentence the HTML styles as a
                      run-in heading opens one there too (WMT "...ethics and integrity.
                      Opportunity. Retail can ...", one paragraph in the export). A stray bullet
                      is never too_short (GOOGL "•seasonality."): an item takes its meaning from
                      the list
"""

from __future__ import annotations

from typing import Callable

from .headings import NOT_A_HEADING, SECTION_TITLE, HeadingPath, Levels, is_line_heading, is_period_heading
from .lists import (ANNOUNCES_LIST, BULLET, group_items_end, group_label, is_bullet, item_marker, list_items_end,
                    merge_list, starts_list)
from .html_tables import TableRows
from .prefilter import prefilter
from .risk import mark_risk_summary, rule_lead_strength
from .sentences import split_sentences
from .units import new_unit
from ..clean import raw_spans
from ..headings.leads import RUNIN_HEADING_MAX_WORDS


AUTO = "auto"       # rule for Section.add: prefilter the unit from its text and its HTML table


class Section:
    """One section being split: its paragraphs, the units made so far and the open headings."""

    def __init__(self, sec: dict, cfg: dict, levels: dict[str, int] | None, tables: list[dict] | None,
                 runin_headings: list[dict] | None = None, guessed: set[str] | None = None):
        self.sec, self.cfg = sec, cfg
        self.runin_spans = [(x["start"], x["end"]) for x in runin_headings or []]
        self.paragraphs: list[dict] = sec["paragraphs"]
        self.is_risk = sec["section_type"] == "risk_factors"
        self.levels = Levels(sec, levels, guessed)
        self.tables = TableRows(tables)
        self.path = HeadingPath(self.is_risk)
        self.units: list[dict] = []

    def add(self, role: str, p_index: int, start: int, end: int, rule: str | None,
            spans: list | None = None, text: str | None = None, rule_heading: str | None = None) -> dict:
        """A unit; rule AUTO = prefilter it (prefilter.py), else the rule given (None = to the LLM)."""
        unit = new_unit(self.sec, self.path, role, p_index, start, end, rule, spans, text, rule_heading)
        where = self.tables.where(unit)
        unit["html_table"] = where if where not in (None, "outside") else None
        if rule == AUTO:
            unit["prefilter"] = prefilter(unit["text"], self.cfg["min_words"], where)
        self.units.append(unit)
        return unit

    def styled_runin(self, paragraph: dict, start: int, text: str) -> bool:
        """The HTML styles this short first sentence as a run-in heading (step 3b)."""
        if not self.runin_spans or not text.endswith(".") or len(text.split()) > RUNIN_HEADING_MAX_WORDS:
            return False
        spans = raw_spans(paragraph, start, start + 1)
        pos = spans[0][0] if spans else start
        return any(a - 2 <= pos < b for a, b in self.runin_spans)

    def open_run_in(self, i: int, a: int, b: int, by_text: bool = True) -> bool:
        """A run-in heading at text[a:b] of paragraph i (positions in the paragraph): open it as
        a sub-heading and add its heading unit. False when it is not one. by_text: a title-case
        sentence counts without the HTML (only where it opens the paragraph)."""
        para = self.paragraphs[i]
        text, p0 = para["text"], para["start"]
        head = text[a:b]
        if not ((by_text and is_period_heading(head)) or self.styled_runin(para, p0 + a, head)) \
                or self.levels.is_sentence(para, p0 + a):
            return False
        level = self.levels.heading_level(para, p0 + a, self.path.sibling)
        if level != NOT_A_HEADING:
            if self.levels.is_guess(para, p0 + a):
                level = max(level, self.path.run_in_floor())   # below the line heading above it
            if self.is_risk:
                level = max(level, 2)                           # never a risk category
        self.path.open(head, level, run_in=True)
        self.add("heading", i, p0 + a, p0 + b, "heading")["heading_level"] = level
        return True

    def add_items(self, k0: int, k1: int, intro_unit: dict) -> None:
        """Split list items k0..k1-1: each sentence is a unit pointing back to intro_unit."""
        for k in range(k0, k1):
            item, q0 = self.paragraphs[k]["text"], self.paragraphs[k]["start"]
            marker = item_marker(item)
            body_start = q0 + (marker.end() if marker else 0)
            body = self.sec["text"][body_start:q0 + len(item)]
            for s, e in split_sentences(body):
                unit = self.add("body", k, body_start + s, body_start + e, AUTO)
                if unit["prefilter"] == "too_short":      # meaning comes from the intro, so keep it
                    unit["prefilter"] = None
                unit["list_intro_id"] = intro_unit["unit_id"]


# A reader takes paragraph i and returns the index of the next paragraph to read, or returns
# None to leave it to the next reader.
Reader = Callable[[Section, int], "int | None"]


def section_title(s: Section, i: int) -> int | None:
    text, p0 = s.paragraphs[i]["text"], s.paragraphs[i]["start"]
    if i == 0 and SECTION_TITLE.match(text):
        s.add("section_title", i, p0, p0 + len(text), "section_title")
        return i + 1
    return None


def heading_line(s: Section, i: int) -> int | None:
    para = s.paragraphs[i]
    text, p0 = para["text"], para["start"]
    list_follows = starts_list(s.paragraphs, i + 1, text.endswith(":"))
    if not is_line_heading(text, list_follows) or s.levels.is_sentence(para, p0):
        return None
    name = text.rstrip(":").rstrip()          # "Executive Summary:" is shown as "Executive Summary"
    if s.is_risk:
        level = s.levels.risk_heading_level(name, para, p0, s.path.sibling)
    else:
        level = s.levels.heading_level(para, p0, s.path.sibling)
    s.path.open(name, level)
    heading = s.add("heading", i, p0, p0 + len(text), "heading")
    heading["heading_level"] = level
    i += 1
    # Bullets right under a heading ("Risks Related to Laws and Regulations" / "•tax
    # expense and tax laws in key jurisdictions; and"): the heading is their intro.
    if starts_list(s.paragraphs, i, False):
        j = list_items_end(s.paragraphs, i, False)
        s.add_items(i, j, heading)
        i = j
    return i


def group_list(s: Section, i: int) -> int | None:
    """A label row ("Engineered Materials Group: | • Aerospace") and the short names under it,
    one per paragraph or two in a row ("Life sciences | • Microelectronics"), as one list_group:
    the names only mean something together with the label (lists.group_label)."""
    para = s.paragraphs[i]
    if not group_label(para["text"]):
        return None
    j = group_items_end(s.paragraphs, i + 1)
    if j - i < 3:                                   # the label row and at least two more rows
        return None
    members = s.paragraphs[i:j]
    spans = []
    for m in members:
        spans += raw_spans(m, m["start"], m["start"] + len(m["text"]))
    end = members[-1]["start"] + len(members[-1]["text"])
    unit = s.add("list_group", i, para["start"], end, None, spans=spans,
                 text=" ".join(m["text"] for m in members))
    unit["list_paragraphs"] = list(range(i, j))
    return j


def list_block(s: Section, i: int) -> int | None:
    """An intro ending in ":" (or a sentence announcing "the following") plus the list items
    right after it: one list_group unit, or split items pointing back to the intro sentence."""
    para = s.paragraphs[i]
    text, p0 = para["text"], para["start"]
    colon = text.endswith(":")
    list_follows = starts_list(s.paragraphs, i + 1, colon)
    intro = split_sentences(text) if list_follows else []
    # The sentence that opens the list: the last one for a ":" intro, else the last sentence
    # announcing "the following" (Medtronic: "...included the following. Unless otherwise
    # stated, amounts apply to both periods.").
    opener = len(intro) - 1
    if intro and not colon:
        hits = [k for k, (a, b) in enumerate(intro) if ANNOUNCES_LIST.search(text[a:b])]
        opener = hits[-1] if hits and not is_bullet(text) and is_bullet(s.paragraphs[i + 1]["text"]) else None
    if not list_follows or opener is None:
        return None
    lettered = not is_bullet(s.paragraphs[i + 1]["text"])
    j = list_items_end(s.paragraphs, i + 1, lettered)
    bullets = s.paragraphs[i + 1:j]
    # Earlier sentences of the intro paragraph stay ordinary units (the first may be a run-in
    # heading); only its last sentence opens the list.
    for k, (a, b) in enumerate(intro[:opener]):
        if k == 0 and not is_bullet(text) and s.open_run_in(i, a, b):
            continue
        s.add("body", i, p0 + a, p0 + b, AUTO)
    start = p0 + intro[-1][0]
    if colon and merge_list(s.sec["text"][start:p0 + len(text)], bullets, s.cfg):
        # Merge: the intro sentence and every item become one list_group unit.
        end = bullets[-1]["start"] + len(bullets[-1]["text"])
        spans = raw_spans(para, start, p0 + len(text))
        for m in bullets:
            spans += raw_spans(m, m["start"], m["start"] + len(m["text"]))
        pieces = [s.sec["text"][start:p0 + len(text)]] + [m["text"] for m in bullets]
        unit = s.add("list_group", i, start, end, None, spans=spans,
                     text=" ".join(pieces))          # fragments joined by one space
        unit["list_paragraphs"] = list(range(i, j))
    else:
        # Split: the intro sentence is an ordinary unit, and each item is labelled
        # on its own, pointing back to that intro sentence.
        intro_unit = None
        for a, b in intro[opener:]:
            unit = s.add("body", i, p0 + a, p0 + b, AUTO)
            intro_unit = intro_unit or unit
        s.add_items(i + 1, j, intro_unit)
    return j


def run_in_heading(s: Section, i: int) -> int | None:
    """A paragraph opening with a heading that ends in a period: the heading becomes the
    sub-heading, and any text after it in the paragraph is split as usual."""
    para = s.paragraphs[i]
    text, p0 = para["text"], para["start"]
    if is_bullet(text):
        return None
    sentences = split_sentences(text)
    a, b = sentences[0]
    if not s.open_run_in(i, a, b):
        return None
    for a, b in sentences[1:]:
        if not s.open_run_in(i, a, b, by_text=False):
            s.add("body", i, p0 + a, p0 + b, AUTO)
    return i + 1


def rule_lead(s: Section, i: int) -> int | None:
    """Item 1A: a single-sentence paragraph the rules propose as a risk lead (rule_heading)."""
    if not s.is_risk:
        return None
    strength = rule_lead_strength(s.paragraphs, i, s.cfg["max_heading_chars"])
    if not strength:
        return None
    s.path.close_subheadings()
    text, p0 = s.paragraphs[i]["text"], s.paragraphs[i]["start"]
    s.add("body", i, p0, p0 + len(text), None, rule_heading=strength)
    return i + 1


def paragraph(s: Section, i: int) -> int:
    """An ordinary paragraph (a stray bullet is treated as one): split into sentences."""
    text, p0 = s.paragraphs[i]["text"], s.paragraphs[i]["start"]
    bullet = is_bullet(text)
    body_start = p0 + (BULLET.match(text).end() if bullet else 0)
    body = s.sec["text"][body_start:p0 + len(text)]
    sentences = split_sentences(body)
    at = body_start - p0
    for k, (a, b) in enumerate(sentences):
        if k == 0 and bullet and len(sentences) > 1 and s.open_run_in(i, at + a, at + b):
            continue                                 # WMT "•Opportunity. Retail can be ..."
        if k and not bullet and s.open_run_in(i, at + a, at + b, by_text=False):
            continue
        unit = s.add("body", i, body_start + a, body_start + b, AUTO)
        if bullet and unit["prefilter"] == "too_short":
            unit["prefilter"] = None
    return i + 1


READERS: tuple[Reader, ...] = (section_title, heading_line, group_list, list_block, run_in_heading, rule_lead, paragraph)


def segment_section(sec: dict, cfg: dict, levels: dict[str, int] | None = None,
                    tables: list[dict] | None = None, runin_headings: list[dict] | None = None,
                    guessed: set[str] | None = None) -> list[dict]:
    """Units of one cleaned section. levels: heading id -> level from step 3b (headings.Levels);
    without it every heading is level 1, so a heading replaces the one before it. tables: the
    section's HTML table rows from step 3b (html_tables.TableRows); None = the filing has no HTML,
    so tables are found from the text ("|"). runin_headings: the section's styled short run-ins
    from step 3b (headings/leads.py runin_headings), [{start, end}] in `content` positions.
    guessed: heading ids whose 3b level only repeats the heading before (load_guessed_levels)."""
    s = Section(sec, cfg, levels, tables, runin_headings, guessed)
    i = 0
    while i < len(s.paragraphs):
        for read in READERS:
            nxt = read(s, i)
            if nxt is not None:
                break
        i = nxt
    mark_risk_summary(s.units)
    return s.units
