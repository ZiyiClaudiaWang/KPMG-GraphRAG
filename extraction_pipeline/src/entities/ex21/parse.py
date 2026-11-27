"""Parse one Exhibit 21 document into subsidiary rows.

Exhibit 21 has no required format. Companies use:
  * an HTML table: name | (spacer) | jurisdiction   (Apple, Microsoft)
  * an HTML table with extra columns, e.g. ownership % or business segment; with a header row
    the jurisdiction and % columns are read by header (Amazon: "100" | "%" in two cells;
    Walmart: a last column naming the business, not the jurisdiction)
  * indentation to show which subsidiary owns which (multi-level groups)
  * plain text lines: "Name (Jurisdiction)" or "Name .... Jurisdiction"
  * described lines: "Name, an Iowa corporation" or
    "1. Name, LLC, a Texas limited liability company"   (Casey's)
This module turns all of these into the same row shape:
  {row_order, subsidiary_name, jurisdiction_raw, ownership_pct, level, parse_method, flags}
"""
from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

HEADER_WORDS = re.compile(
    r"^(name|legal\s+name|(subsidiary|entity|company)\s+name|names?\s+of\s+(subsidiar|entit|compan)|subsidiar(y|ies)|entity|company|"
    r"jurisdiction|where\s+incorporated|state\s+or\s+(other\s+)?(country|jurisdiction)|"
    r"(country|state|place)\s+of\s+(incorporation|organization|formation)|"
    r"incorporated\s+in|organized\s+under|percent|%\s*owned|ownership)",
    re.I,
)
LIST_NUMBER = re.compile(r"\(?\d{1,3}[.)]")       # a numbering cell: "1." / "(2)" / "3)"
# Header cells, read in this order ("Percent of equity owned" is the % column, not a name).
COLUMN_HEADERS = (
    ("pct", re.compile(r"percent|%|owner", re.I)),
    ("juris", re.compile(r"jurisdiction|incorporat|organi[sz]|domicile|country|\bstate\b|place\s+of|"
                         r"where|laws\s+of|formed\s+in", re.I)),
    ("name", re.compile(r"name|subsidiar|entit|compan", re.I)),
)
PCT = re.compile(r"^\(?\s*(\d{1,3}(?:\.\d+)?)\s*%\s*\)?$")
FOOTNOTE_MARK = re.compile(r"(\s*(\*+|\(\d{1,2}\)|\[\d{1,2}\]|†|‡))+$")
PAREN_JURIS = re.compile(r"^(?P<name>.+?)\s*\((?P<j>[^()]+)\)\s*$")
LEADER = re.compile(r"^(?P<name>.+?)\s*(?:\.{3,}|\t+|\s{3,}|\s+[-–—]\s+)\s*(?P<j>[^.\t]+)$")
# "Name, an Iowa corporation" / "Name, LLC, a Delaware limited liability company":
# the jurisdiction sits between the article and the kind of entity.
ENTITY_KIND = (r"(?:(?:private|public)\s+)?(?:(?:limited|unlimited)\s+(?:liability\s+)?)?"
               r"(?:corporation|company|partnership|trust|association|cooperative|bank)")
DESCRIBED = re.compile(
    r"^(?:\(?\d{1,3}[.)]\s*)?(?P<name>.+?),\s+(?:an?|the)\s+(?P<j>[A-Z][\w.’'&\- ]*?)\s+"
    + ENTITY_KIND + r"\b[^,]*\.?$"
)
NOISE = re.compile(
    r"^(exhibit\s+\d|subsidiaries\s+of|list\s+of\s+subsidiaries|the\s+following|pursuant\s+to|"
    r"\*|#|note|omit|page\s+\d|\d+$)",
    re.I,
)
# A whole row that is a title, date or group heading, not a subsidiary. Only used on rows with
# no jurisdiction, and the whole name must match, so "United States Steel" is never dropped.
#   Medtronic: "Medtronic plc and Subsidiaries", "At April 24, 2026"
#   Parker-Hannifin: "UNITED STATES", "INTERNATIONAL" over the two halves of its table
TITLE_ROW = re.compile(
    r"(?:united\s+states|u\.?\s?s\.?|domestic|international|foreign|non[-\s]u\.?\s?s\.?|"
    r"north\s+america|europe|asia(?:\s+pacific)?|latin\s+america|americas|emea)"
    r"(?:\s+subsidiar(?:y|ies))?:?"
    r"|(?:at|as\s+of)\s+[a-z]+\.?\s+\d{1,2},?\s+\d{4}"
    r"|.+\s+and\s+(?:its\s+)?(?:consolidated\s+)?subsidiaries",
    re.I,
)


def _not_a_subsidiary(name: str, juris: str) -> bool:
    return not juris and bool(NOISE.match(name) or TITLE_ROW.fullmatch(name))


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


def _spans(cells) -> list[tuple[int, int]]:
    """Grid columns [start, end) of each cell, from colspan."""
    out, col = [], 0
    for c in cells:
        try:
            n = max(1, int(c.get("colspan") or 1))
        except ValueError:
            n = 1
        out.append((col, col + n))
        col += n
    return out


def _header_columns(cells, texts) -> dict | None:
    """{"name"/"juris"/"pct"/"other_<i>": (start, end)} when the row is a header row naming a
    jurisdiction or % column; None otherwise. Columns it cannot name (Walmart's segment) are
    "other_<i>", so their cells are never read as the jurisdiction."""
    filled = [(span, t) for span, t in zip(_spans(cells), texts) if t]
    if len(filled) < 2 or not HEADER_WORDS.match(filled[0][1]) or any(len(t) >= 60 for _, t in filled):
        return None
    cols = {}
    for i, (span, t) in enumerate(filled):
        kind = next((k for k, rx in COLUMN_HEADERS if rx.search(t)), None)
        cols[kind if kind and kind not in cols else f"other_{i}"] = span   # other: read by nobody
    return cols if ("juris" in cols or "pct" in cols) else None


def _column_of(span, header: dict) -> str | None:
    """The header column a data cell sits under: the one it overlaps, else the nearest header
    column starting at or before it (spacer cells shift data cells by a column or two)."""
    over = [k for k, (a, b) in header.items() if span[0] < b and a < span[1]]
    if over:
        return over[0]
    before = [(a, k) for k, (a, _) in header.items() if a <= span[0]]
    return max(before)[1] if before else None


def _join_pct(texts: list[str]) -> list[str]:
    """ "100" | "%" in two cells -> "100%" (Amazon)."""
    out = []
    for t in texts:
        if t == "%" and out and re.fullmatch(r"\(?\d{1,3}(?:\.\d+)?", out[-1]):
            out[-1] += "%"
        else:
            out.append(t)
    return out


def _pct_value(text: str) -> float | None:
    m = PCT.match(text) or re.fullmatch(r"(\d{1,3}(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    header, header_width = None, 0          # kept across tables of the same width (page breaks)
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [_clean(c.get_text(" ")) for c in cells]
            filled = [(c, t) for c, t in zip(cells, texts) if t]
            if not filled:
                continue
            found = _header_columns(cells, texts)
            if found:
                header, header_width = found, _spans(cells)[-1][1]
                continue
            if HEADER_WORDS.match(filled[0][1]) and len(filled[0][1]) < 60:
                continue
            width = _spans(cells)[-1][1]
            if header and width == header_width and len(filled) > 1 \
                    and not LIST_NUMBER.fullmatch(filled[0][1]) and not _split_stacked_row(filled):
                by_col: dict[str, list[str]] = {}
                for (c, t), span in zip(filled[1:], [sp for sp, t in zip(_spans(cells), texts) if t][1:]):
                    by_col.setdefault(_column_of(span, header) or "", []).append(t)
                pct_text = "".join(by_col.get("pct", []))
                pct = _pct_value(pct_text) if pct_text else None
                juris = " ".join(t for t in _join_pct(by_col.get("juris", [])) if not PCT.match(t))
                first_cell, name = filled[0]
                if not juris:
                    m = DESCRIBED.match(name) or PAREN_JURIS.match(name)
                    if m:
                        name, juris = m.group("name"), m.group("j")
                if not _not_a_subsidiary(name, juris):
                    rows.append(_row(len(rows) + 1, name, juris, pct, _indent_of(first_cell),
                                     "html_table:header"))
                continue
            if len(filled) > 1 and LIST_NUMBER.fullmatch(filled[0][1]):
                filled = filled[1:]                 # Casey's 2021: "1." | "Name, an Iowa corporation"
            split = _split_stacked_row(filled)
            if split:
                for name, juris in split:
                    if not _not_a_subsidiary(name, juris):
                        rows.append(_row(len(rows) + 1, name, juris, None, _indent_of(filled[0][0]),
                                         "html_table:stacked"))
                continue
            first_cell, name = filled[0]
            rest = _join_pct([t for _, t in filled[1:]])
            pct = next((float(PCT.match(t).group(1)) for t in rest if PCT.match(t)), None)
            juris_cells = [t for t in rest if not PCT.match(t)]
            juris = juris_cells[-1] if juris_cells else ""
            if not juris:
                m = DESCRIBED.match(name) or PAREN_JURIS.match(name)
                if m:
                    name, juris = m.group("name"), m.group("j")
            if _not_a_subsidiary(name, juris):
                continue
            rows.append(_row(len(rows) + 1, name, juris, pct, _indent_of(first_cell), "html_table"))
    if rows:
        return rows
    # No usable table: fall back to text lines. Inline tags (a <span> around "Casey’s", a
    # <font>) cut an entry over several lines when every tag starts a line: the entry is
    # lost, or its name loses the words before the tag. Reading one line per block element
    # keeps each entry whole, so it wins unless the tag reading finds more entries.
    by_tag = parse_text(soup.get_text("\n"), method="html_text_lines")
    by_block = parse_text(_block_text(soup), method="html_block_lines")
    return by_block if by_block and len(by_block) >= len(by_tag) else by_tag


BLOCKS = {"p", "div", "li", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6", "table"}


def _paragraphs(cell) -> list[str]:
    """The non-empty <p>/<div> blocks of a cell, or its <br>-separated lines."""
    blocks = [_clean(b.get_text(" ")) for b in cell.find_all(["p", "div"]) if not b.find(["p", "div"])]
    blocks = [b for b in blocks if b]
    if len(blocks) > 1:
        return blocks
    if cell.find("br"):
        lines = [_clean(t) for t in cell.get_text("\n").split("\n")]
        return [t for t in lines if t]
    return blocks


def _split_stacked_row(filled) -> list[tuple[str, str]]:
    """One table row holding several subsidiaries stacked in the same cells (NetApp:
    "Network Appliance Saudi Arabia LLC" / "NetApp Regional Headquarter Company" over
    "Saudi Arabia" / "Saudi Arabia"). Split only when the name cell and the jurisdiction
    cell have the same number (2 or more) of paragraphs; otherwise return []."""
    if len(filled) < 2:
        return []
    names, jurs = _paragraphs(filled[0][0]), _paragraphs(filled[-1][0])
    if len(names) < 2 or len(names) != len(jurs) or any(PCT.match(j) for j in jurs):
        return []
    return list(zip(names, jurs))


def _block_text(soup) -> str:
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for el in soup.find_all(BLOCKS):
        el.insert_before("\n")
        el.insert_after("\n")
    return soup.get_text("")


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
            if _not_a_subsidiary(parts[0], jur[-1] if jur else ""):
                continue
            rows.append(_row(len(rows) + 1, parts[0], jur[-1] if jur else "", pct, indent // 2,
                             method + ":pipe"))
            continue
        if NOISE.match(line) or HEADER_WORDS.match(line) or len(line) > 160:
            continue
        m = DESCRIBED.match(line) or PAREN_JURIS.match(line) or LEADER.match(raw.replace("\xa0", " ").strip())
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
