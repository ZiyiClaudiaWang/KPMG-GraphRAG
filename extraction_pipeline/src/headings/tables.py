"""HTML tables: which rows of a section are table rows, and what kind of table each one is.

Step 4 reads this (data/interim/html_tables.jsonl) to decide `table_residue` from the HTML
instead of from a "|" in the text. Filings use tables for more than numbers: MSFT lays out its
bullet lists as two-column tables ("• | text"), MU and NKE put executive bios in one-cell tables,
NTAP its critical accounting estimates. So a table is classified first, from all its rows:

  layout      at least `layout_long_share` of its non-empty cells are running text
              (`long_cell_words`+ words), or at least `layout_bullet_share` of its rows open with
              a bullet cell: text laid out in a table. Step 4 treats it as ordinary text.
  definition  a layout table of term | definition rows (MSFT "Microsoft Cloud revenue growth |
              Revenue from Azure, ..."): most rows have two cells, a short label (no end
              punctuation, not ending in ":") and a longer text without bullets. Step 4 marks its
              units `definition` (not sent to the LLM; kept for a later metric glossary).
  data        any other table: numbers, dates, names, ratings (on 313 filings no data table had
              long text or bullets). Step 4 marks its units table_residue, except full sentences
              (footnotes such as PH "(2) On February 9, 2025, a fire damaged ..."), which go to
              the LLM.

On the 313 filings (26 Nov 2026): 3,391 data tables, ~870 layout, ~30 definition (MSFT).
Thresholds: settings.yaml headings.tables.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .html import Block

LONG_CELL_WORDS = 12
LAYOUT_LONG_SHARE = 0.3
LAYOUT_BULLET_SHARE = 0.5
DEFINITION_ROW_SHARE = 0.6
LABEL_MAX_WORDS = 8
DEFINITION_MIN_WORDS = 6
KINDS = ("data", "layout", "definition")

BULLET = re.compile(r"^[•●◦▪‣]")
END_PUNCT = re.compile(r"[.?!:;][\"”’)]*$")


def cells(text: str) -> list[str]:
    return [c.strip() for c in text.split("|") if c.strip()]


def is_definition_row(row: list[str]) -> bool:
    """term | definition: a short label and a longer text, no bullets."""
    if len(row) != 2:
        return False
    label, text = row
    return (len(label.split()) <= LABEL_MAX_WORDS and not END_PUNCT.search(label)
            and not BULLET.match(label) and "•" not in text
            and len(text.split()) >= DEFINITION_MIN_WORDS)


def table_kind(rows: list[Block], cfg: dict | None = None) -> str:
    """data, layout or definition, from all the rows of one table (see the module docstring)."""
    cfg = cfg or {}
    cs = [cells(b.text) for b in rows]
    cs = [r for r in cs if r]
    flat = [c for r in cs for c in r]
    if not flat:
        return "data"
    long_share = sum(len(c.split()) >= cfg.get("long_cell_words", LONG_CELL_WORDS) for c in flat) / len(flat)
    bullet_share = sum(bool(BULLET.match(r[0])) for r in cs) / len(cs)
    if (long_share < cfg.get("layout_long_share", LAYOUT_LONG_SHARE)
            and bullet_share < cfg.get("layout_bullet_share", LAYOUT_BULLET_SHARE)):
        return "data"
    definitions = sum(is_definition_row(r) for r in cs)
    if definitions >= 2 and definitions / len(cs) >= cfg.get("definition_row_share", DEFINITION_ROW_SHARE):
        return "definition"
    return "layout"


def section_tables(section: dict, blocks: list[Block], cfg: dict | None = None) -> list[dict]:
    """The table rows inside one section: [{start, end, kind, table}] in `content` positions, in order.

    blocks: the filing's HTML blocks, aligned to the export text (html.align). A table is classified
    from all its placed rows, also those outside this section."""
    lo = section["offset_start"]
    hi = lo + len(section["content"])
    by_table: dict[int, list[Block]] = defaultdict(list)
    for b in blocks:
        if b.in_table and b.start >= 0:
            by_table[b.table_id].append(b)
    out = []
    for table_id, rows in by_table.items():
        inside = [b for b in rows if lo <= b.start and b.end <= hi]
        if not inside:
            continue
        kind = table_kind(rows, cfg)
        out += [{"start": b.start - lo, "end": b.end - lo, "kind": kind, "table": table_id} for b in inside]
    return sorted(out, key=lambda r: r["start"])
