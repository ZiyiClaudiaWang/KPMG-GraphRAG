"""Parse one Exhibit 21 document into subsidiary rows.

Exhibit 21 has no required format. Companies use:
  * an HTML table: name | (spacer) | jurisdiction   (Apple, Microsoft)
  * an HTML table with extra columns, e.g. ownership %
  * indentation to show which subsidiary owns which (multi-level groups)
  * plain text lines: "Name (Jurisdiction)" or "Name .... Jurisdiction"
This module turns all of these into the same row shape:
  {row_order, subsidiary_name, jurisdiction_raw, ownership_pct, level, parse_method, flags}
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

HEADER_WORDS = re.compile(
    r"^(name|names?\s+of\s+(subsidiar|entit|compan)|subsidiar(y|ies)|entity|company|"
    r"jurisdiction|where\s+incorporated|state\s+or\s+(other\s+)?(country|jurisdiction)|"
    r"(country|state|place)\s+of\s+(incorporation|organization|formation)|"
    r"incorporated\s+in|organized\s+under|percent|%\s*owned|ownership)",
    re.I,
)
PCT = re.compile(r"^\(?\s*(\d{1,3}(?:\.\d+)?)\s*%\s*\)?$")
FOOTNOTE_MARK = re.compile(r"(\s*(\*+|\(\d{1,2}\)|\[\d{1,2}\]|†|‡))+$")
PAREN_JURIS = re.compile(r"^(?P<name>.+?)\s*\((?P<j>[^()]+)\)\s*$")
LEADER = re.compile(r"^(?P<name>.+?)\s*(?:\.{3,}|\t+|\s{3,}|\s+[-–—]\s+)\s*(?P<j>[^.\t]+)$")
NOISE = re.compile(
    r"^(exhibit\s+\d|subsidiaries\s+of|list\s+of\s+subsidiaries|the\s+following|pursuant\s+to|"
    r"\*|#|note|omit|page\s+\d|\d+$)",
    re.I,
)


def _clean(text: str) -> str:
    text = (text or "").replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", text).strip()


def _strip_marks(name: str) -> tuple[str, bool]:
    new = FOOTNOTE_MARK.sub("", name).strip()
    return new, new != name


def _row(order, name, juris, pct, level, method):
    flags = []
    name, had_mark = _strip_marks(_clean(name))
    juris, _ = _strip_marks(_clean(juris or ""))
    if had_mark:
        flags.append("footnote_marker_removed")
    if not juris:
        flags.append("jurisdiction_missing")
    return {
        "row_order": order,
        "subsidiary_name": name,
        "jurisdiction_raw": juris,
        "ownership_pct": pct,
        "level": level,
        "parse_method": method,
        "flags": ";".join(flags),
    }


def _indent_of(cell) -> int:
    """Indentation (in rough 'levels') from CSS padding/margin/text-indent or leading nbsp."""
    px = 0.0
    for el in [cell] + cell.find_all(True):
        style = (el.get("style") or "").lower()
        for prop in ("padding-left", "margin-left", "text-indent"):
            m = re.search(prop + r"\s*:\s*([\d.]+)\s*(pt|px|em|in)?", style)
            if m:
                v = float(m.group(1))
                unit = m.group(2) or "px"
                v = v * {"pt": 1.33, "px": 1, "em": 16, "in": 96}[unit]
                px = max(px, v)
    raw = cell.get_text()
    lead = len(raw) - len(raw.lstrip("\xa0 "))
    px = max(px, lead * 4)
    return int(round(px / 12)) if px >= 9 else 0


def parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [_clean(c.get_text(" ")) for c in cells]
            filled = [(c, t) for c, t in zip(cells, texts) if t]
            if not filled:
                continue
            if HEADER_WORDS.match(filled[0][1]) and len(filled[0][1]) < 60:
                continue
            first_cell, name = filled[0]
            rest = [t for _, t in filled[1:]]
            pct = next((float(PCT.match(t).group(1)) for t in rest if PCT.match(t)), None)
            juris_cells = [t for t in rest if not PCT.match(t)]
            juris = juris_cells[-1] if juris_cells else ""
            if not juris:
                m = PAREN_JURIS.match(name)
                if m:
                    name, juris = m.group("name"), m.group("j")
            if NOISE.match(name) and not juris:
                continue
            rows.append(_row(len(rows) + 1, name, juris, pct, _indent_of(first_cell), "html_table"))
    if rows:
        return rows
    # No usable table: fall back to text lines.
    return parse_text(soup.get_text("\n"), method="html_text_lines")


def parse_text(text: str, method: str = "text_lines") -> list[dict]:
    rows: list[dict] = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue  # provenance comments in transcribed files
        indent = len(raw) - len(raw.lstrip(" \t\xa0"))
        line = _clean(raw)
        if not line:
            continue
        if "|" in line:
            parts = [p.strip() for p in line.strip("|").split("|")]
            parts = [p for p in parts if p and not set(p) <= set("-: ")]
            if not parts:
                continue
            if HEADER_WORDS.match(parts[0]):
                continue
            pct = next((float(PCT.match(p).group(1)) for p in parts[1:] if PCT.match(p)), None)
            jur = [p for p in parts[1:] if not PCT.match(p)]
            if NOISE.match(parts[0]) and not jur:
                continue
            rows.append(_row(len(rows) + 1, parts[0], jur[-1] if jur else "", pct, indent // 2,
                             method + ":pipe"))
            continue
        if NOISE.match(line) or HEADER_WORDS.match(line) or len(line) > 160:
            continue
        m = PAREN_JURIS.match(line) or LEADER.match(line)
        if m:
            rows.append(_row(len(rows) + 1, m.group("name"), m.group("j"), None, indent // 2, method))
    return rows


def parse_file(path: str | Path) -> list[dict]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".htm", ".html", ".xml"} or "<table" in text.lower():
        return parse_html(text)
    return parse_text(text)


def assign_parents(rows: list[dict], registrant: str) -> list[dict]:
    """Turn indentation levels into immediate-parent links.

    Level 0 rows are owned by the registrant. A row indented under another row is
    owned by the nearest earlier row with a smaller level. Flat lists (Apple,
    Microsoft) therefore map every subsidiary straight to the registrant.
    """
    levels = sorted({r["level"] for r in rows})
    remap = {lv: i for i, lv in enumerate(levels)}  # 0, 3, 6 -> 0, 1, 2
    stack: list[tuple[int, str]] = []
    for r in rows:
        r["level"] = remap[r["level"]]
        while stack and stack[-1][0] >= r["level"]:
            stack.pop()
        r["immediate_parent"] = stack[-1][1] if stack else registrant
        # A flat list does not say whether a subsidiary is held directly or through another
        # subsidiary, so we do not claim "direct" unless the filing shows nesting.
        r["relationship"] = ("subsidiary of registrant (direct/indirect not stated)" if not stack
                             else "indirect subsidiary (nested in filing)")
        stack.append((r["level"], r["subsidiary_name"]))
    return rows
