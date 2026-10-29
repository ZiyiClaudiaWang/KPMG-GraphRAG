"""Step 4 - Segment: split kept sections into numbered sentences.

Input : data/interim/clean/*.jsonl, data/output/section_manifest.csv
Output: data/output/sentences.jsonl   one row per unit the LLM may label

A unit is one sentence, or one list group (an introductory clause ending in ":"
plus its whole bulleted list), following Alina's interim list convention.

Every unit keeps:
  char_start, char_end  position in the cleaned section text
  raw_spans             positions in the Data-team `content` (one span per fragment)
  prefilter             the rule that marked it not_a_claim before the LLM, or null
  rule_heading          in Item 1A: "high"/"medium" if the rules think this is a
                        risk-factor heading, else null

Risk headings are only proposed here. Apple puts each heading in its own
paragraph, which the rules catch; Microsoft starts a paragraph with the
heading sentence, which plain text cannot show. The LLM therefore also flags
headings in step 5, and step 7 settles them (see risk_factors.py).
"""

from __future__ import annotations

import re
import hashlib
import json

SEGMENTATION_VERSION = "kelly_sentence_v2_restrictions_boundary"

from .clean import raw_spans
from .common import Paths, filings, log, read_jsonl, settings, write_jsonl
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
}
BOUNDARY = re.compile(r"[.?!][\"”’)\]]*(?=\s+[\"“‘(\[]?[A-Z0-9•])")
BULLET = re.compile(r"^([•●◦▪‣–-]|o)\s+")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of sentences in text, trimmed of whitespace."""
    spans, start = [], 0
    for m in BOUNDARY.finditer(text):
        end = m.end()
        before = text[start:m.start() + 1]
        token = before.split()[-1].rstrip(".?!").lower() if before.split() else ""
        token = token.lstrip("(\"“")
        nxt = text[end:].split(maxsplit=1)[0] if text[end:].strip() else ""
        dotted = bool(re.fullmatch(r"(?:[a-z]\.)+[a-z]", token))   # "d.c", "u.s", "e.g"
        if token in ABBREVIATIONS or dotted:
            ambiguous = token in AMBIGUOUS or (dotted and token not in ABBREVIATIONS)
            if not ambiguous or nxt.strip("\"“(") not in OPENERS:
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


def is_fragment_list(bullets: list[dict]) -> bool:
    """True when most bullet items cannot stand alone ("Driving customer adoption...", "tariffs;").

    Such lists only make sense together with their intro, so they form one unit.
    Bullets that are complete sentences ("Microsoft Cloud revenue increased 29%.")
    are labelled one by one.
    """
    def fragment(text: str) -> bool:
        item = BULLET.sub("", text)
        first = item.split()[0] if item.split() else ""
        return (item[:1].islower() or first.endswith("ing") or item.rstrip().endswith((";", ",", "; and", "; or"))
                or not TERMINAL.search(item))
    return sum(fragment(b["text"]) for b in bullets) * 2 >= len(bullets)


def is_heading_like(text: str) -> bool:
    """A short line without sentence punctuation: a category or sub-heading."""
    return (len(text) <= 150 and "|" not in text and not is_bullet(text)
            and not TERMINAL.search(text) and not text.endswith(":"))


def is_category(text: str) -> bool:
    """Category headings are in capitals or title case ("Macroeconomic and Industry Risks")."""
    words = re.findall(r"[A-Za-z][A-Za-z’'-]*", text)
    if not words:
        return False
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
    if text.count("|") >= 2:
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
            "sentence_id": f"{sec['accession_number']}|{sec['section_key']}|s{len(units) + 1:04d}",
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
            "processing_set_id": sec["processing_set_id"], "source_locator": sec["source_locator"],
        }
        unit["segmentation_version"] = SEGMENTATION_VERSION
        unit["source_unit_key"] = hashlib.sha256(json.dumps(
            [sec["accession_number"], sec["section_key"], unit["raw_spans"], unit["text"]],
            ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
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

        if is_heading_like(text):
            if is_risk and is_category(text):
                category, subheading = text, None
                add("category_heading", i, p0, p0 + len(text), "heading")
            else:
                subheading = text
                add("heading", i, p0, p0 + len(text), "heading")
            i += 1
            continue

        # List group: an intro ending in ":" plus bullets that are fragments, kept as one unit.
        if text.endswith(":") and i + 1 < len(paragraphs) and is_bullet(paragraphs[i + 1]["text"]):
            j = i + 1
            while j < len(paragraphs) and is_bullet(paragraphs[j]["text"]):
                j += 1
            bullets = paragraphs[i + 1:j]
            if is_fragment_list(bullets):
                # Earlier sentences of the intro paragraph stay ordinary units;
                # only its last sentence opens the list.
                intro = split_sentences(text)
                for s, e in intro[:-1]:
                    add("body", i, p0 + s, p0 + e, prefilter(text[s:e], cfg["min_words"]))
                start = p0 + intro[-1][0]
                end = bullets[-1]["start"] + len(bullets[-1]["text"])
                spans = raw_spans(para, start, p0 + len(text))
                for m in bullets:
                    spans += raw_spans(m, m["start"], m["start"] + len(m["text"]))
                pieces = [sec["text"][start:p0 + len(text)]] + [m["text"] for m in bullets]
                unit = add("list_group", i, start, end, None, spans=spans,
                           text=" ".join(pieces))          # fragments joined by one space
                unit["list_paragraphs"] = list(range(i, j))
                i = j
                continue
            # Bullets that are full sentences: the intro and each bullet are ordinary paragraphs.

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

    return units


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
