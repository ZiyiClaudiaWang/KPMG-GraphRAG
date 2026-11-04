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

SEGMENTATION_VERSION = "kelly_sentence_v3"   # v3: bullets, list rule, abbreviation splits, headings with a period

from .clean import DANGLING_WORDS, raw_spans
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
BULLET = re.compile(r"^(?:[•●◦▪‣]\s*|[–-]\s+|o\s+)")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of sentences in text, trimmed of whitespace."""
    spans, start = [], 0
    for m in BOUNDARY.finditer(text):
        end = m.end()
        before = text[start:m.start() + 1]
        token = before.split()[-1].rstrip(".?!").lower() if before.split() else ""
        token = token.lstrip("(\"“")
        if token.rsplit("-", 1)[-1] in ABBREVIATIONS:    # "non-U.S." is read as "U.S.", not "10-K." as "K."
            token = token.rsplit("-", 1)[-1]
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


def is_fragment(text: str) -> bool:
    """A bullet item that cannot stand alone ("Driving customer adoption...", "tariffs;", "iPad Air")."""
    item = BULLET.sub("", text)
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
    if text.count("|") >= 2 or note_key(text) in TABLE_NOTE_KEYS or FOOTNOTE_MARKER.match(text):
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

def segment_section(sec: dict, cfg: dict) -> list[dict]:
    paragraphs = sec["paragraphs"]
    is_risk = sec["section_type"] == "risk_factors"
    base = section_label(sec)
    units: list[dict] = []
    category = subheading = None

    def add(role: str, p_index: int, start: int, end: int, rule: str | None,
            spans: list | None = None, text: str | None = None, rule_heading: str | None = None) -> dict:
        unit = {
            "accession_number": sec["accession_number"], "ticker": sec["ticker"], "form": sec["form"],
            "section_id": sec["section_id"], "section_key": sec["section_key"],
            "section_type": sec["section_type"],
            "section_path": " > ".join(x for x in (base, category, subheading) if x),
            "section_label": base, "category": category, "subheading": subheading,
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

    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        text, p0 = para["text"], para["start"]

        if i == 0 and SECTION_TITLE.match(text):
            add("section_title", i, p0, p0 + len(text), "section_title")
            i += 1
            continue

        list_follows = i + 1 < len(paragraphs) and is_bullet(paragraphs[i + 1]["text"])
        if is_heading_like(text) or (is_colon_heading(text) and not list_follows):
            name = text.rstrip(":").rstrip()      # "Executive Summary:" is shown as "Executive Summary"
            if is_risk and is_category(name):
                category, subheading = name, None
                add("category_heading", i, p0, p0 + len(text), "heading")
            else:
                subheading = name
                add("heading", i, p0, p0 + len(text), "heading")
            i += 1
            continue

        # A list: an intro ending in ":" plus the bullet paragraphs right after it.
        if text.endswith(":") and i + 1 < len(paragraphs) and is_bullet(paragraphs[i + 1]["text"]):
            j = i + 1
            while j < len(paragraphs) and is_bullet(paragraphs[j]["text"]):
                j += 1
            bullets = paragraphs[i + 1:j]
            # Earlier sentences of the intro paragraph stay ordinary units;
            # only its last sentence opens the list.
            intro = split_sentences(text)
            for s, e in intro[:-1]:
                add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
            start = p0 + intro[-1][0]
            if merge_list(sec["text"][start:p0 + len(text)], bullets, cfg):
                # Merge: the intro sentence and every bullet become one list_group unit.
                end = bullets[-1]["start"] + len(bullets[-1]["text"])
                spans = raw_spans(para, start, p0 + len(text))
                for m in bullets:
                    spans += raw_spans(m, m["start"], m["start"] + len(m["text"]))
                pieces = [sec["text"][start:p0 + len(text)]] + [m["text"] for m in bullets]
                unit = add("list_group", i, start, end, None, spans=spans,
                           text=" ".join(pieces))          # fragments joined by one space
                unit["list_paragraphs"] = list(range(i, j))
            else:
                # Split: the intro sentence is an ordinary unit, and each bullet is labelled
                # on its own, pointing back to that intro sentence.
                s, e = intro[-1]
                intro_unit = add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
                for k in range(i + 1, j):
                    item, q0 = paragraphs[k]["text"], paragraphs[k]["start"]
                    body_start = q0 + BULLET.match(item).end()
                    body = sec["text"][body_start:q0 + len(item)]
                    for s, e in split_sentences(body):
                        rule = prefilter(body[s:e], cfg["min_words"])
                        if rule == "too_short":           # meaning comes from the intro, so keep it
                            rule = None
                        unit = add("body", k, body_start + s, body_start + e, rule)
                        unit["list_intro_id"] = intro_unit["unit_id"]
            i = j
            continue

        # A paragraph opening with a heading that ends in a period: the heading becomes the
        # sub-heading, and any text after it in the paragraph is split as usual.
        if not is_bullet(text):
            sentences = split_sentences(text)
            s, e = sentences[0]
            if is_period_heading(text[s:e]):
                subheading = text[s:e]
                add("heading", i, p0 + s, p0 + e, "heading")
                for s, e in sentences[1:]:
                    add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
                i += 1
                continue

        if is_risk:
            strength = risk_heading_strength(paragraphs, i, cfg["max_heading_chars"])
            if strength:
                subheading = None
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
    cfg = settings()["segment"]
    kept = kept_section_keys()
    all_units = []
    for f in filings():
        for sec in read_jsonl(Paths.cleaned(f)):
            if (sec["accession_number"], sec["section_key"]) not in kept:
                continue
            units = segment_section(sec, cfg)
            all_units += units
            to_label = sum(u["prefilter"] is None for u in units)
            proposed = sum(bool(u["rule_heading"]) for u in units)
            log(STEP, f"{sec['ticker']} {sec['form']} {sec['section_key']}: {len(units)} units, "
                      f"{to_label} to label, {proposed} proposed risk headings")
    write_jsonl(Paths.sentences(), all_units)
    log(STEP, f"{len(all_units)} units -> {Paths.sentences()}")


if __name__ == "__main__":
    run()
