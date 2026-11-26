"""Lead sentences from a filing's EDGAR HTML: one sentence that sums up the sentences under it.

A lead sentence is not a heading: it is labelled like any sentence and changes no section_path.
Step 4 (segment.py) links each unit to the lead above it (lead_unit_id), in every section. Two
looks count as a lead (source):

  html         a block of its own whose whole text is bold, italic or underlined, a full sentence
               (lead_min_words..lead_max_words, ends in . ? !), followed by a plain block of text
               (MU "We are subject to counterparty default risks." / "We have numerous
               arrangements ..."). The Item 1A headings step 4b reads as sentences (level -1,
               reason lead_sentence: AMZN "Our International Operations Expose Us to a Number of
               Risks") are leads too.
  html_runin   the first sentence of a block is bold, italic or underlined and the rest is plain
               (MSFT "Competition in the technology sector may harm our business." then the text
               about it in the same paragraph); at least runin_min_words words.

Never a lead: a block in a table or a table of contents, a block step 4b reads as a heading
(any level but -1), a short title-case line ending in a period ("Flexible Work.", a run-in
heading: segment.is_period_heading), and a styled sentence next to another styled sentence or
block (a run of them is emphasis, not a lead).

Each lead is a span in the section's Data-team `content` (start, end), so step 4 matches it to
its unit by position: the first unit that starts inside the span.
"""

from __future__ import annotations

import re

from .html import Block, norm, style_over

LEAD_MIN_WORDS = 4
LEAD_MAX_WORDS = 100
RUNIN_MIN_WORDS = 6
SENTENCE_LEVEL = -1                           # heading_levels.jsonl: a sentence, not a heading
SENTENCE_END = re.compile(r"[.?!][\"'”’)]*$")
HAS_LETTER = re.compile(r"[A-Za-z]")


def emphasised(block: Block, n_chars: int | None = None) -> bool:
    st = style_over(block, n_chars if n_chars is not None else len(norm(block.text)))
    return st["bold"] or st["italic"] or st["underline"]


def is_full_sentence(text: str, min_words: int, max_words: int) -> bool:
    from ..segment import is_period_heading
    n = len(text.split())
    return (bool(SENTENCE_END.search(text)) and min_words <= n <= max_words
            and not is_period_heading(text))


def runin_prefix(block: Block) -> str | None:
    """The styled text a block opens with, when the rest of the block is plain text; else None."""
    prefix, rest = [], None
    for k, r in enumerate(block.runs):
        if not norm(r.text):
            prefix.append(r.text)
            continue
        if r.bold or r.italic or r.underline:
            prefix.append(r.text)
            continue
        rest = "".join(x.text for x in block.runs[k:])
        break
    if rest is None or not HAS_LETTER.search(rest) or not HAS_LETTER.search("".join(prefix)):
        return None
    return " ".join("".join(prefix).split())


def raw_end(text: str, start: int, n_chars: int) -> int:
    """The position in text just after the n_chars-th character counted from start the way
    html.norm counts them (whitespace and bullets not counted)."""
    seen, i = 0, start
    while i < len(text) and seen < n_chars:
        if norm(text[i]):
            seen += 1
        i += 1
    return i


def section_leads(section: dict, blocks: list[Block], text: str, rows: list[dict],
                  cfg: dict | None = None) -> list[dict]:
    """Lead sentences of one section: [{start, end, source, text}] in `content` positions, in order.

    rows: this section's heading rows (levels.infer_section) with heading_id, text and level.
    text: the export text the blocks are aligned to (html.export_text)."""
    cfg = cfg or {}
    min_words = cfg.get("lead_min_words", LEAD_MIN_WORDS)
    max_words = cfg.get("lead_max_words", LEAD_MAX_WORDS)
    runin_min = cfg.get("runin_min_words", RUNIN_MIN_WORDS)
    lo = section["offset_start"]
    hi = lo + len(section["content"])
    out: list[dict] = []
    headings = []                                   # spans step 4b reads as headings (any level but -1)
    for r in rows:
        start = int(r["heading_id"].rsplit("|", 1)[1])
        span = (start, start + len(r["text"]))
        if r["level"] == SENTENCE_LEVEL:
            out.append({"start": span[0], "end": span[1], "source": "html", "text": r["text"]})
        else:
            headings.append(span)

    def is_heading(s: int, e: int) -> bool:
        return any(a < e and b > s for a, b in headings)

    def taken(s: int) -> bool:
        return any(x["start"] - 2 <= s < x["end"] for x in out)

    for b in blocks:
        if b.start < lo or b.end > hi or b.in_table or b.is_link:
            continue
        s, e = b.start - lo, b.end - lo
        if is_heading(s, e) or taken(s):
            continue
        line = " ".join(b.text.split())
        if not HAS_LETTER.search(line):
            continue
        if emphasised(b):
            nxt = blocks[b.idx + 1] if b.idx + 1 < len(blocks) else None
            prv = blocks[b.idx - 1] if b.idx > 0 else None
            plain_next = (nxt is not None and not nxt.in_table and HAS_LETTER.search(nxt.text) is not None
                          and not emphasised(nxt))
            styled_before = (prv is not None and not prv.in_table and emphasised(prv)
                             and is_full_sentence(" ".join(prv.text.split()), min_words, max_words))
            if plain_next and not styled_before and is_full_sentence(line, min_words, max_words):
                out.append({"start": s, "end": e, "source": "html", "text": line})
            continue
        prefix = runin_prefix(b)
        if prefix and is_full_sentence(prefix, runin_min, max_words):
            end = raw_end(text, b.start, len(norm(prefix))) - lo
            out.append({"start": s, "end": end, "source": "html_runin", "text": prefix})
    return sorted(out, key=lambda x: x["start"])


def in_lead(start: int, leads: list[dict]) -> bool:
    """A block or unit that starts at start (section positions) begins inside a lead."""
    return any(x["start"] - 2 <= start < x["end"] for x in leads)
