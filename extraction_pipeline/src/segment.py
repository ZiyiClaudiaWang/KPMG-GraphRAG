"""Step 4 - Segment: split kept sections into sentence units, in source order.

Input : data/interim/clean/*.jsonl, data/output/section_manifest.csv
Output: data/output/sentences.jsonl   one row per unit the LLM may label

A unit is one sentence, or one list group (an introductory clause ending in ":"
plus its whole bulleted list).

Lists (an intro ending in ":" followed by bullet paragraphs) are merged into one
list group only when the items cannot stand alone and the group stays short:
  - short names: every item is a fragment and items average under
    short_name_max_chars (product or factor names), or
  - at least one item is a fragment and the whole group (last intro sentence
    plus all items) is at most list_merge_max_chars.
Every other list is split: each bullet is labelled on its own, and its units carry
list_intro_id, the unit_id of the intro's last sentence. Bullets with no ":"
intro before them (after a heading or an unrelated paragraph) are ordinary paragraphs.
Split list items are never dropped as too_short: their meaning comes from the intro.

A paragraph that opens with a short title-case sentence ending in a period
("Flexible Work.", "Zynga.", a run-in heading in bold before the text) starts a
sub-heading, like a heading without a period.

Every unit keeps:
  unit_id               uuid5 of accession, section_key and raw_spans (common.unit_id, #45): the
                        only id of a unit; every later step and file points at a unit by it.
                        It stays the same when a cleaning change keeps the unit's spans.
                        Units are written in source order, so the file order is the order.
  char_start, char_end  position in the cleaned section text
  raw_spans             positions in the Data-team `content` (one span per fragment)
  prefilter             the rule that marked it not_a_claim before the LLM, or null
  rule_heading          in Item 1A: "high"/"medium" if the rules think this is a
                        risk-factor heading, else null

In Item 1A, the risk factor summary some filings open with (MU "Risk Factor Summary",
TTWO "Summary of Risk Factors") repeats the risks described below it in short form. The
summary ends where a category heading it listed appears for the second time. Units in it
are labelled as usual and carry risk_summary = true, so later steps can tell them apart.

Risk headings are only proposed here. Apple puts each heading in its own
paragraph, which the rules catch; Microsoft starts a paragraph with the
heading sentence, which plain text cannot show. The LLM therefore also flags
headings in step 5, and step 7 settles them (see risk_factors.py).
"""

from __future__ import annotations

import re

# v3: bullets, list rule, abbreviation splits, headings with a period.
# v4 (4 Nov 2026): unit_id (#45); page breaks before an amount or parenthesis; "U.S. Cultural";
#     headings ending in ":"; table cells and footnote markers; Microsoft item links (step 2).
# v5 (18 Nov 2026): any table row is table_residue, two-column rows ("Acquisitions | 47") too;
#     table rows are never joined across a page break (step 2).
# v6 (19 Nov 2026): lists after a heading or "the following", lettered items, "• |" and "\uf0b7"
#     bullets, items whose bullet was lost; heading levels (step 4b); new page-break joins (step 2).
SEGMENTATION_VERSION = "kelly_sentence_v6"

from .clean import DANGLING_WORDS, ITEM_MARKER, has_table_cells, raw_spans
from .common import Paths, filings, log, read_jsonl, settings, unit_id, write_jsonl
from .filter_sections import kept_section_keys

STEP = "segment"

# ---------- sentence splitting ----------

ABBREVIATIONS = {
    "u.s", "u.k", "e.u", "inc", "corp", "co", "ltd", "llc", "l.p", "n.a", "s.a", "plc",
    "no", "nos", "e.g", "i.e", "etc", "vs", "approx", "fig", "mr", "ms", "mrs", "dr", "jr", "sr", "st",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "a.m", "p.m",
}
# After these, a period ends the sentence only if a typical sentence opener follows.
AMBIGUOUS = {"u.s", "u.k", "e.u", "inc", "corp", "co", "ltd", "llc", "etc", "plc"}
OPENERS = {
    "The", "In", "As", "This", "These", "That", "Those", "Such", "However", "Additionally", "Also",
    "We", "Our", "It", "Its", "If", "Any", "Many", "Some", "A", "An", "Accordingly", "Further",
    "Furthermore", "Moreover", "For", "There", "While", "When", "Although", "Because", "Each",
    "Restrictions",
    # Seen after "U.S." / "Inc." / "plc" at a real sentence end in the ten-company filings.
    "During", "Refer", "On", "To", "Like", "Prior", "He", "She", "They", "Mr", "Ms",
    "Cultural",   # TTWO: "...as well as they do in the U.S. Cultural differences may also require..."
}
ITEM_NUMBER = re.compile(r"\d{1,2}[a-c]?")   # "1a" in "see Item 1A. Risk Factors", "9" in "Note 9. Debt"
REFERENCE_WORDS = {"item", "note"}            # "Item 1A. Risk Factors", "Note 9. Debt": the title follows the number
BOUNDARY = re.compile(r"[.?!][\"”’)\]]*(?=\s+[\"“‘(\[]?[A-Z0-9•])")
# A bullet, with the "|" of a bullet exported as its own table cell after it ("• | Revenue grew").
# "\uf0b7" is the Symbol-font bullet (NetApp).
BULLET = re.compile(r"^(?:[•●◦▪‣\uf0b7]\s*(?:\|\s*)?|[–-]\s+(?:\|\s*)?|o\s+)")
# The last sentence of an intro that announces a list without a ":"
# (McKesson: "The following summary provides highlights ... for the year ended March 31, 2021.").
ANNOUNCES_LIST = re.compile(r"\bthe following\b|\bas follows\b", re.IGNORECASE)
# A list item that ends so that another item must follow: "...;", "...; and", "...,".
ITEM_GOES_ON = re.compile(r"(?:[;,]|;\s+(?:and|or)|,\s+(?:and|or)|\band/or)$")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of sentences in text, trimmed of whitespace."""
    spans, start = [], 0
    for m in BOUNDARY.finditer(text):
        end = m.end()
        before = text[start:m.start() + 1]
        token = before.split()[-1].rstrip(".?!").lower() if before.split() else ""
        token = token.lstrip("(\"“")
        tail = re.split(r"[-/–—]", token)[-1]
        if tail in ABBREVIATIONS:    # "non-U.S.", "Euro/U.S.", "patents—U.S." are read as "U.S.", not "10-K." as "K."
            token = tail
        nxt = text[end:].split(maxsplit=1)[0] if text[end:].strip() else ""
        opener = nxt.strip("\"“(").rstrip(".,;:")               # "Additionally," -> "Additionally"
        dotted = bool(re.fullmatch(r"(?:[a-z]\.)+[a-z]", token))   # "d.c", "u.s", "e.g"
        words = before.split()
        if (len(words) >= 2 and words[-2].lower().lstrip("(\"“") in REFERENCE_WORDS and ITEM_NUMBER.fullmatch(token)
                and opener not in OPENERS):
            continue                                # "see Item 1A. Risk Factors", "Note 9. Debt": one sentence
        if token in ABBREVIATIONS or dotted:
            ambiguous = token in AMBIGUOUS or (dotted and token not in ABBREVIATIONS)
            if not ambiguous or opener not in OPENERS:
                continue
        if re.fullmatch(r"[a-z]", token):          # an initial such as "J."
            continue
        spans.append((start, end))
        start = end
    spans.append((start, len(text)))
    out = []
    for s, e in spans:
        chunk = text[s:e]
        left = len(chunk) - len(chunk.lstrip())
        right = len(chunk.rstrip())
        if right > left:
            out.append((s + left, s + right))
    return out


# ---------- paragraph roles ----------

TERMINAL = re.compile(r"[.?!][\"”’)]*$")
SECTION_TITLE = re.compile(r"^item\s+\d{1,2}[a-c]?\b", re.IGNORECASE)
CROSS_REFERENCE = re.compile(
    r"^(see|refer to)\b|incorporated (herein )?by reference|^\(?(none|not applicable)\.?\)?$",
    re.IGNORECASE,
)


def is_bullet(text: str) -> bool:
    return bool(BULLET.match(text))


def item_marker(text: str) -> re.Match | None:
    """The bullet or letter/number that opens a list item, or None."""
    return BULLET.match(text) or ITEM_MARKER.match(text)


def list_items_end(paragraphs: list[dict], i: int, lettered: bool) -> int:
    """Index after the last list item of the run starting at paragraph i.

    An item is a bullet paragraph, or, in a lettered run ("a.", "(1)"), a lettered paragraph.
    A paragraph without a bullet also counts as an item when it is not a table row and either
    the item before it ends so that another must follow ("...;", "...; and", "...,") and it starts
    in lower case, or it is a short name between two short-name items (Parker-Hannifin's market
    lists: "• Power generation" / "Renewable energy" / "• Water purification"): the Data team
    export drops the bullet sign of some items (S12_bullet_lost).
    """
    j = i
    while j < len(paragraphs):
        text = paragraphs[j]["text"]
        if is_bullet(text) or (lettered and ITEM_MARKER.match(text)):
            j += 1
        elif j > i and not has_table_cells(text) and (
                (text[:1].islower() and ITEM_GOES_ON.search(paragraphs[j - 1]["text"].rstrip()))
                or (short_item(text) and short_item(paragraphs[j - 1]["text"])
                    and j + 1 < len(paragraphs) and is_bullet(paragraphs[j + 1]["text"]))):
            j += 1
        else:
            break
    return j


def short_item(text: str) -> bool:
    """A name in a list of names: at most 5 words, no end punctuation ("• Power generation",
    "Renewable energy"). Between two bullets, such a paragraph is an item whose bullet was lost."""
    body = BULLET.sub("", text)
    return 0 < len(body.split()) <= 5 and not TERMINAL.search(body) and not body.endswith(":")


def starts_list(paragraphs: list[dict], i: int, colon_intro: bool) -> bool:
    """True when paragraph i opens a list: a bullet, or after a ":" intro a lettered item."""
    if i >= len(paragraphs):
        return False
    text = paragraphs[i]["text"]
    return is_bullet(text) or (colon_intro and bool(ITEM_MARKER.match(text)))


def is_fragment(text: str) -> bool:
    """A bullet item that cannot stand alone ("Driving customer adoption...", "tariffs;", "iPad Air")."""
    item = ITEM_MARKER.sub("", BULLET.sub("", text))
    first = item.split()[0] if item.split() else ""
    return (item[:1].islower() or first.endswith("ing") or item.rstrip().endswith((";", ",", "; and", "; or"))
            or not TERMINAL.search(item))


def merge_list(intro_sentence: str, bullets: list[dict], cfg: dict) -> bool:
    """True when an intro and its bullets should be one list_group unit (see the module docstring).

    Bullets that are all complete sentences ("Microsoft Cloud revenue increased 29%.") are never
    merged; nor is a list too long to read as one statement (Micron's 17 international risks).
    """
    texts = [b["text"] for b in bullets]
    fragments = sum(is_fragment(t) for t in texts)
    if fragments == len(texts) and sum(map(len, texts)) / len(texts) < cfg.get("short_name_max_chars", 60):
        return True
    group_chars = len(" ".join([intro_sentence] + texts))
    return fragments > 0 and group_chars <= cfg.get("list_merge_max_chars", 600)


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


def is_period_heading(text: str) -> bool:
    """A short title-case sentence ending in a period: "Flexible Work.", "Zynga.", "2K."

    At most 5 words, and every word of 4 or more letters capitalised, so
    "Develop Robust Player Relationships." counts but "Revenue increased." does not.
    """
    if not text.endswith(".") or not 1 <= len(text.rstrip(".").split()) <= 5:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z’'-]*", text)
    return bool(words) and words[0][0].isupper() and all(w[0].isupper() for w in words if len(w) >= 4)


SENTENCE_LEVEL = -1        # heading_levels.jsonl: "a sentence, not a heading"

RISK_CATEGORY_START = re.compile(r"^risks?\s+(?:related|relating)\s+to\b", re.IGNORECASE)
RISK_SUMMARY = re.compile(r"^(?:risk factors? summary|summary of risk factors?)$", re.IGNORECASE)


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


# Body sentences often open by pointing back; headings never do.
CONTINUATION = re.compile(
    r"^(such|this|these|that|those|although|while|if|in addition|additionally|also|however|"
    r"for all|any of|as a result|accordingly|further|moreover)\b",
    re.IGNORECASE,
)


def sentence_count(text: str) -> int:
    return len(split_sentences(text))


def risk_heading_strength(paragraphs: list[dict], i: int, max_chars: int) -> str | None:
    """Return 'high' or 'medium' if paragraph i looks like a risk-factor heading, else None.

    Risk headings are single sentences (Apple's end with a period) followed by
    their body: a paragraph of two or more sentences (high), or a sub-heading as
    in Microsoft's 1A (medium). Single-sentence body paragraphs are followed by
    another single sentence or by a category heading, so they are not matched.
    """
    text = paragraphs[i]["text"]
    if (len(text) > max_chars or is_bullet(text) or text.endswith(":") or CONTINUATION.match(text)
            or not TERMINAL.search(text) or sentence_count(text) != 1):
        return None
    if i + 1 >= len(paragraphs):
        return None
    nxt = paragraphs[i + 1]["text"]
    if is_bullet(nxt):
        return None
    if is_heading_like(nxt):
        return None if is_category(nxt) else "medium"
    return "high" if sentence_count(nxt) >= 2 else None


def section_label(sec: dict) -> str:
    """'Part I Item 1A' - always with the Part prefix, for 10-K and 10-Q alike."""
    part = f"Part {sec['part']} " if sec.get("part") else ""
    return f"{part}Item {sec['item']}".strip()


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


# ---------- segmentation ----------

def heading_id(sec: dict, paragraph: dict, start: int) -> str:
    """The id of a heading for heading levels: accession|section_key|its start in `content`."""
    spans = raw_spans(paragraph, start, start + 1)
    return f"{sec['accession_number']}|{sec['section_key']}|{spans[0][0] if spans else start}"


def segment_section(sec: dict, cfg: dict, levels: dict[str, int] | None = None) -> list[dict]:
    """Units of one cleaned section. levels: heading id -> level (step 4b, src/headings/, from
    the filing's HTML), 1 = top. With levels, a heading keeps the headings above it in
    section_path ("Part II Item 7 > Results of Operations > Segment Results > Intelligent Cloud");
    level 0 means "not a heading" (a table title or column header) and changes no path; level -1
    means the line is a sentence of the text (CASY "We may be adversely impacted by increases in
    the cost of food ingredients and other related costs", a risk heading without its period):
    it is split and labelled like any paragraph. Without levels every heading is level 1, so a
    heading replaces the one before it, as before.
    """
    paragraphs = sec["paragraphs"]
    is_risk = sec["section_type"] == "risk_factors"
    base = section_label(sec)
    units: list[dict] = []
    category = subheading = None
    stack: list[tuple[int, str]] = []          # (level, heading text) above the current unit

    def is_sentence(p_index: int, start: int) -> bool:
        """The levels file says this heading-like line is a sentence of the text (level -1)."""
        return (levels or {}).get(heading_id(sec, paragraphs[p_index], start)) == SENTENCE_LEVEL

    def open_heading(name: str, p_index: int, start: int) -> int:
        """Put a sub-heading on the stack by its level; return the level (0: not a heading)."""
        nonlocal subheading
        level = (levels or {}).get(heading_id(sec, paragraphs[p_index], start), 1 if not levels else None)
        if level is None:                       # levels given but not for this heading: a sibling
            level = stack[-1][0] if stack else 1
        if level == 0:
            return 0
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, name))
        subheading = name
        return level

    def add(role: str, p_index: int, start: int, end: int, rule: str | None,
            spans: list | None = None, text: str | None = None, rule_heading: str | None = None) -> dict:
        unit = {
            "accession_number": sec["accession_number"], "ticker": sec["ticker"], "form": sec["form"],
            "section_id": sec["section_id"], "section_key": sec["section_key"],
            "section_type": sec["section_type"],
            "section_path": " > ".join(x for x in (base, category, *(h for _, h in stack)) if x),
            "section_label": base, "category": category, "subheading": subheading,
            "heading_path": [h for _, h in stack],
            "role": role,
            "text": text if text is not None else sec["text"][start:end],
            "char_start": start, "char_end": end,
            "raw_spans": spans if spans is not None else raw_spans(paragraphs[p_index], start, end),
            "paragraph_index": p_index, "prefilter": rule, "rule_heading": rule_heading,
            "risk_summary": False,
            "processing_set_id": sec["processing_set_id"], "source_locator": sec["source_locator"],
        }
        unit["segmentation_version"] = SEGMENTATION_VERSION
        unit["unit_id"] = unit_id(sec["accession_number"], sec["section_key"], unit["raw_spans"])
        units.append(unit)
        return unit

    def add_items(k0: int, k1: int, intro_unit: dict) -> None:
        """Split list items k0..k1-1: each sentence is a unit pointing back to intro_unit."""
        for k in range(k0, k1):
            item, q0 = paragraphs[k]["text"], paragraphs[k]["start"]
            marker = item_marker(item)
            body_start = q0 + (marker.end() if marker else 0)
            body = sec["text"][body_start:q0 + len(item)]
            for s, e in split_sentences(body):
                rule = prefilter(body[s:e], cfg["min_words"])
                if rule == "too_short":           # meaning comes from the intro, so keep it
                    rule = None
                unit = add("body", k, body_start + s, body_start + e, rule)
                unit["list_intro_id"] = intro_unit["unit_id"]

    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        text, p0 = para["text"], para["start"]

        if i == 0 and SECTION_TITLE.match(text):
            add("section_title", i, p0, p0 + len(text), "section_title")
            i += 1
            continue

        colon = text.endswith(":")
        list_follows = starts_list(paragraphs, i + 1, colon)
        if (is_heading_like(text) or (is_colon_heading(text) and not list_follows)) and not is_sentence(i, p0):
            name = text.rstrip(":").rstrip()      # "Executive Summary:" is shown as "Executive Summary"
            if is_risk and is_category(name):
                category, subheading = name, None
                stack.clear()
                heading = add("category_heading", i, p0, p0 + len(text), "heading")
            else:
                level = open_heading(name, i, p0)
                heading = add("heading", i, p0, p0 + len(text), "heading")
                heading["heading_level"] = level
            i += 1
            # Bullets right under a heading ("Risks Related to Laws and Regulations" / "•tax
            # expense and tax laws in key jurisdictions; and"): the heading is their intro.
            if starts_list(paragraphs, i, False):
                j = list_items_end(paragraphs, i, False)
                add_items(i, j, heading)
                i = j
            continue

        # A list: an intro ending in ":" (or a sentence announcing "the following") plus the
        # list items right after it.
        intro = split_sentences(text) if list_follows else []
        # The sentence that opens the list: the last one for a ":" intro, else the last sentence
        # announcing "the following" (Medtronic: "...included the following. Unless otherwise
        # stated, amounts apply to both periods.").
        opener = len(intro) - 1
        if intro and not colon:
            hits = [k for k, (a, b) in enumerate(intro) if ANNOUNCES_LIST.search(text[a:b])]
            opener = hits[-1] if hits and not is_bullet(text) and is_bullet(paragraphs[i + 1]["text"]) else None
        if list_follows and opener is not None:
            lettered = not is_bullet(paragraphs[i + 1]["text"])
            j = list_items_end(paragraphs, i + 1, lettered)
            bullets = paragraphs[i + 1:j]
            # Earlier sentences of the intro paragraph stay ordinary units;
            # only its last sentence opens the list.
            for s, e in intro[:opener]:
                add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
            start = p0 + intro[-1][0]
            if colon and merge_list(sec["text"][start:p0 + len(text)], bullets, cfg):
                # Merge: the intro sentence and every item become one list_group unit.
                end = bullets[-1]["start"] + len(bullets[-1]["text"])
                spans = raw_spans(para, start, p0 + len(text))
                for m in bullets:
                    spans += raw_spans(m, m["start"], m["start"] + len(m["text"]))
                pieces = [sec["text"][start:p0 + len(text)]] + [m["text"] for m in bullets]
                unit = add("list_group", i, start, end, None, spans=spans,
                           text=" ".join(pieces))          # fragments joined by one space
                unit["list_paragraphs"] = list(range(i, j))
            else:
                # Split: the intro sentence is an ordinary unit, and each item is labelled
                # on its own, pointing back to that intro sentence.
                intro_unit = None
                for s, e in intro[opener:]:
                    unit = add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
                    intro_unit = intro_unit or unit
                add_items(i + 1, j, intro_unit)
            i = j
            continue

        # A paragraph opening with a heading that ends in a period: the heading becomes the
        # sub-heading, and any text after it in the paragraph is split as usual.
        if not is_bullet(text):
            sentences = split_sentences(text)
            s, e = sentences[0]
            if is_period_heading(text[s:e]) and not is_sentence(i, p0 + s):
                level = open_heading(text[s:e], i, p0 + s)
                add("heading", i, p0 + s, p0 + e, "heading")["heading_level"] = level
                for s, e in sentences[1:]:
                    add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
                i += 1
                continue

        if is_risk:
            strength = risk_heading_strength(paragraphs, i, cfg["max_heading_chars"])
            if strength:
                subheading = None
                stack.clear()
                add("body", i, p0, p0 + len(text), None, rule_heading=strength)
                i += 1
                continue

        # Ordinary paragraph (a stray bullet is treated as one): split into sentences.
        body_start = p0 + (BULLET.match(text).end() if is_bullet(text) else 0)
        body = sec["text"][body_start:p0 + len(text)]
        for s, e in split_sentences(body):
            add("body", i, body_start + s, body_start + e, prefilter(body[s:e], cfg["min_words"]))
        i += 1

    mark_risk_summary(units)
    return units


def mark_risk_summary(units: list[dict]) -> None:
    """Flag the risk factor summary (risk_summary = true): it repeats the risks described after it.

    The summary runs from its heading to the first category heading that appears for the
    second time (where the full text starts again). Without such a repeat nothing is marked.
    """
    start = next((k for k, u in enumerate(units)
                  if u["role"] == "category_heading" and RISK_SUMMARY.match(u["text"])), None)
    if start is None:
        return
    seen, end = set(), None
    for k in range(start + 1, len(units)):
        if units[k]["role"] != "category_heading":
            continue
        key = units[k]["text"].lower()
        if key in seen:
            end = k
            break
        seen.add(key)
    if end is None:
        return
    for u in units[start:end]:
        u["risk_summary"] = True


def run() -> None:
    from .headings import load_levels            # heading levels, when step 4b has run
    cfg = settings()["segment"]
    kept = kept_section_keys()
    levels = load_levels()
    if levels:
        log(STEP, f"{len(levels)} heading levels from step 4b")
    all_units = []
    for f in filings():
        for sec in read_jsonl(Paths.cleaned(f)):
            if (sec["accession_number"], sec["section_key"]) not in kept:
                continue
            units = segment_section(sec, cfg, levels)
            all_units += units
            to_label = sum(u["prefilter"] is None for u in units)
            proposed = sum(bool(u["rule_heading"]) for u in units)
            log(STEP, f"{sec['ticker']} {sec['form']} {sec['section_key']}: {len(units)} units, "
                      f"{to_label} to label, {proposed} proposed risk headings")
    write_jsonl(Paths.sentences(), all_units)
    log(STEP, f"{len(all_units)} units -> {Paths.sentences()}")


if __name__ == "__main__":
    run()
