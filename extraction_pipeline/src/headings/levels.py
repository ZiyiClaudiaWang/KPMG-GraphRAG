"""Heading levels of one section from the typography of its HTML blocks.

Each candidate heading (step 4's rules) is found in the HTML by its position in the export text,
then:

  reason                    level
  table                     0     inside a table: a column or row header
  item_title_continuation   0     a centred line right after the Item title ("For the fiscal year ended ...")
  toc                       0     a table of contents inside the section ("INDEX TO ...", and the entries
                                  after it, or a line that is only an internal link)
  units_line                0     only a unit line such as "(In millions)" or "(in thousands, except per share)"
  typography                1..   by style, per section:
                                    - the first style seen is level 1;
                                    - a style seen before closes the levels under it (a sibling);
                                    - a new style first closes the open styles less prominent than it
                                      (size, then bold / underline / capitals / centred; italic and
                                      run-in rank lower), then opens one level deeper.
  inside_paragraph          sibling  the line is in the middle of an HTML paragraph
  plain_style               sibling  a run-in lead-in without bold or italic
  unmatched                 sibling  not found in the HTML
  missing_html              sibling  no HTML or no section export for the filing

"sibling" = the level of the heading before it (1 at the start of a section), as step 4 does for
a heading the file does not list.

`flags` mark rows to review; they never change the level:
  maybe_sentence   10+ words, starts like a sentence ("We ...", "Our ..."), or inside a paragraph:
                   possibly a sentence the rules read as a heading (CASY risk factors whose period
                   was lost). Once checked, a rule can give these level -1 (step 4 then labels the
                   line like any sentence).
  before_table     the next HTML block is a table: possibly a table title, not a heading.
"""

from __future__ import annotations

import bisect
import re

from .html import Block, norm, style_over

SIG_COLS = ["size", "bold", "underline", "caps", "italic", "center", "indent", "run_in", "in_table"]

ITEM_TITLE = re.compile(r"^item\s*\d+[a-z]?\b", re.I)
CONTINUATION = re.compile(r"^(?:for the |ended |as of )", re.I)
TOC_TITLE = re.compile(r"^(?:index to\b|table of contents\b|contents$)", re.I)
UNITS_LINE = re.compile(
    r"^\(?\s*(?:\$|dollars|amounts|shares)?\s*in\s+(?:millions|thousands|billions)\b[^)]*\)?:?$", re.I)
SENTENCE_START = re.compile(r"^(?:we|our|us|if|any|there|it|this|these)\b", re.I)
SENTENCE_WORDS = 10


def signature(h_text: str, block: Block, how: str, use_indent: bool) -> tuple:
    st = style_over(block, len(norm(h_text)))
    letters = [ch for ch in h_text if ch.isalpha()]
    caps = st["caps_css"] or (bool(letters) and all(ch.isupper() for ch in letters))
    return (st["size"], st["bold"], st["underline"], caps, st["italic"],
            block.align == "center", indent_bucket(block, how, use_indent), how == "run_in", block.in_table)


def indent_bucket(block: Block, how: str, use_indent: bool) -> int:
    """Left indent in 9pt steps; 0 for run-in lead-ins (that indent is the paragraph's first
    line) and for companies where indent is not a level (headings.indent_tickers)."""
    if how == "run_in" or not use_indent:
        return 0
    return round(block.indent / 9) * 9


def score(sig: tuple) -> float:
    """How prominent a heading style looks: size first, then bold / underline / capitals /
    centred; italic and run-in lower it; indent breaks ties."""
    size, bold, under, caps, ital, center, indent, runin, tbl = sig
    return size * 10 + bold + under + caps + center - 0.5 * ital - 2 * runin - indent / 100


def match_heading(heading: dict, section: dict, aligned: list[Block], starts: list[int],
                  text: str, use_indent: bool) -> dict:
    """Where the heading is in the HTML: standalone (a block of its own), run_in (the start of a
    block), inside (in the middle of a block) or unmatched."""
    position = section["offset_start"] + int(heading["heading_id"].rsplit("|", 1)[1])
    index = bisect.bisect_right(starts, position) - 1
    if index < 0 or position >= aligned[index].end:
        return {"match": "unmatched"}
    block = aligned[index]
    title = norm(heading["text"])
    prefix = norm(text[block.start:position])
    block_text = norm(block.text)
    if not title or not block_text[len(prefix):].startswith(title):
        return {"match": "unmatched"}
    how = "inside" if prefix else "standalone" if block_text == title else "run_in"
    style = signature(heading["text"], block, how, use_indent) if not prefix else None
    return {"match": how, "block": block, "style": dict(zip(SIG_COLS, style)) if style else None}


def flags_for(text: str, match: str, before_table: bool) -> list[str]:
    out = []
    if (len(text.split()) >= SENTENCE_WORDS or SENTENCE_START.match(text) or match == "inside"):
        out.append("maybe_sentence")
    if before_table:
        out.append("before_table")
    return out


def infer_section(group: dict, section: dict | None, blocks: list[Block], text: str,
                  use_indent: bool) -> list[dict]:
    """One row per candidate heading of the section, in order. blocks: every block of the
    filing's HTML (html_blocks), aligned to text (align)."""
    aligned = [b for b in blocks if b.start >= 0]
    starts = [b.start for b in aligned]
    stack: list[tuple[int, str, tuple | None, str]] = []      # (level, heading_id, style, text)
    first = next((b for b in aligned if section and b.start >= section["offset_start"]), None)
    title_run_block = first if first and ITEM_TITLE.match(first.text.strip()) else None
    toc_block: Block | None = None             # last block of a table of contents still open
    toc_score = 0.0                            # how prominent its title is
    rows = []
    for candidate in group["headings"]:
        key, words = candidate["heading_id"], candidate["text"]
        if section is None or not aligned:
            evidence = {"match": "missing_html"}
        else:
            evidence = match_heading(candidate, section, aligned, starts, text, use_indent)
        block: Block | None = evidence.get("block")
        style = evidence.get("style")
        sig = tuple(style.values()) if style else None
        after = blocks[block.idx + 1] if block is not None and block.idx + 1 < len(blocks) else None
        before_table = bool(after is not None and after.in_table and block is not None and not block.in_table)

        continuation = bool(
            title_run_block is not None and block is not None and block.idx == title_run_block.idx + 1
            and style and style["center"] and CONTINUATION.match(words))
        title_run_block = block if continuation else None
        # A table of contents: its title, then the lines right after it that are links or less
        # prominent than the title (a heading as prominent as the title ends it).
        in_toc = False
        if block is not None and TOC_TITLE.match(words):
            in_toc, toc_block, toc_score = True, block, score(sig) if sig else 0.0
        elif block is not None and block.is_link:
            in_toc, toc_block = True, block
        elif (block is not None and toc_block is not None and block.idx == toc_block.idx + 1
              and sig and score(sig) < toc_score):
            in_toc, toc_block = True, block
        if not in_toc:
            toc_block = None

        sibling = stack[-1][0] if stack else 1
        if evidence["match"] in ("missing_html", "unmatched"):
            level, reason = sibling, evidence["match"]
        elif block.in_table:
            level, reason = 0, "table"
        elif continuation:
            level, reason = 0, "item_title_continuation"
        elif in_toc:
            level, reason = 0, "toc"
        elif UNITS_LINE.match(words.strip()):
            level, reason = 0, "units_line"
        elif evidence["match"] == "inside":
            level, reason = sibling, "inside_paragraph"
        elif evidence["match"] == "standalone" or style["bold"] or style["italic"]:
            if sig[3] and len(words.split()) == 1 and stack:
                plain = sig[:3] + (False,) + sig[4:]       # a one-word capitals heading ("Products")
                if stack[-1][2] == plain:                   # in a run of the same style
                    sig = plain
            for index, entry in enumerate(stack):
                if entry[2] == sig:
                    del stack[index:]
                    break
            else:
                while stack and stack[-1][2] is not None and score(stack[-1][2]) < score(sig):
                    stack.pop()
            level, reason = (stack[-1][0] + 1 if stack else 1), "typography"
        else:
            level, reason = sibling, "plain_style"

        if level > 0:
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1] if stack else None
            stack.append((level, key, sig if reason == "typography" else None, words))
            path = [entry[3] for entry in stack]
        else:
            parent, path = (stack[-1] if stack else None), [entry[3] for entry in stack]
        rows.append({
            "heading_id": key, "text": words, "level": level, "reason": reason,
            "flags": flags_for(words, evidence["match"], before_table) if level != 0 else [],
            "match": evidence["match"],
            "parent_text": parent[3] if parent else None,
            "heading_path": path,
            "style": style,
            "block_text": block.text[:160] if block is not None else None,
        })
    return rows


def paths_from_levels(headings: list[dict], levels: dict[str, int]) -> dict[str, str | None]:
    """heading_id -> parent heading text, as step 4 would build it from these levels (to compare
    with another levels file)."""
    stack: list[tuple[int, str]] = []
    out = {}
    for h in headings:
        level = levels.get(h["heading_id"], stack[-1][0] if stack else 1)
        if level <= 0:
            out[h["heading_id"]] = stack[-1][1] if stack else None
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        out[h["heading_id"]] = stack[-1][1] if stack else None
        stack.append((level, h["text"]))
    return out
