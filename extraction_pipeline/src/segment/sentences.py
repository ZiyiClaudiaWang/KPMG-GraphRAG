"""Sentence splitting: where one sentence ends and the next starts."""

from __future__ import annotations

import re

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
ROMAN = re.compile(r"x{0,3}(?:ix|iv|v?i{0,3})")   # "iii" of "III."; one letter ("I.", "V.") is an initial below
QUOTE_OPEN = "\"“‘(["                        # a numbered title in quotes: under the sub-caption "III. Environmental Matters."
# A sentence end before whitespace, or a closing quote straight before a capital (WMT 'Matters."The
# foregoing matters ...': no space in the export).
BOUNDARY = re.compile(r"[.?!][\"”’)\]]*(?=\s+[\"“‘(\[]?[A-Z0-9•])|[.?!][\"”](?=[A-Z][a-z])")
TERMINAL = re.compile(r"[.?!][\"”’)]*$")      # the text ends like a sentence


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
        last = words[-1] if words else ""
        if (len(token) >= 2 and ROMAN.fullmatch(token) and last[:1] in QUOTE_OPEN
                and last.lstrip(QUOTE_OPEN)[:1].isupper() and opener not in OPENERS):
            continue                                # WMT '"III. Environmental Matters."': the number opens a title
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


def sentence_count(text: str) -> int:
    return len(split_sentences(text))
