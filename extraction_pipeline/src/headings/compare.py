"""Headings the HTML shows vs the headings step 4 reads, for review. Changes no level and no role.

Two kinds of rows (heading_html_diff.csv):

  html_only     an HTML block in a kept section that looks like a heading (see html_heading)
                but no step-4 heading, category heading or section title overlaps it. step4_role
                says what step 4 made of it (body, list_group, ...; none = not in any unit, so
                removed by clean or filter). text_check: sentence = ends in . ! ? with
                sentence_min_words+ words (a risk lead sentence or body, not a heading), else
                short_line.
  step4_plain   a step-4 heading (level > 0, found in the HTML) that the HTML shows as plain
                text: in the middle of a paragraph, or no bold / italic / underline / capitals /
                centring and no larger size than the body text.

If step 4 already covers nearly all the HTML headings (heading_summary.csv html_covered_pct),
"step 4 candidates + HTML levels" stays; otherwise HTML-first candidates are worth trying.

Thresholds: settings.yaml headings.compare (max_words, sentence_min_words).
"""

from __future__ import annotations

from collections import Counter

from .html import Block, norm, style_over
from .levels import HAS_WORD, ITEM_TITLE, UNITS_LINE, link_words

MAX_WORDS = 20                 # longer HTML blocks are never heading-like
SENTENCE_MIN_WORDS = 8         # text_check: "sentence" from this many words, with . ! ? at the end
SIZE_STEP = 0.5                # pt above the body size that counts as "larger"
HEADING_ROLES = {"heading", "category_heading", "section_title"}


def body_size(blocks: list[Block]) -> float:
    """The body text size of a filing: the most common size by characters, tables left out."""
    sizes = Counter()
    for b in blocks:
        if b.in_table:
            continue
        for r in b.runs:
            sizes[r.size] += len(norm(r.text))
    return sizes.most_common(1)[0][0] if sizes else 10.0


def emphasis(block: Block, body: float) -> list[str]:
    """What makes the whole block stand out from body text (empty = plain)."""
    text = block.text
    st = style_over(block, len(norm(text)))
    letters = [ch for ch in text if ch.isalpha()]
    out = [name for name in ("bold", "underline", "italic") if st[name]]
    if st["caps_css"] or (len(letters) >= 2 and all(ch.isupper() for ch in letters)):
        out.append("caps")
    if st["size"] > body + SIZE_STEP:
        out.append(f"size {st['size']:g}")
    return out


def html_heading(block: Block, body: float, max_words: int = MAX_WORDS) -> list[str] | None:
    """The emphasis of a block that looks like a heading, or None."""
    text = " ".join(block.text.split())
    if (block.start < 0 or block.in_table or block.is_link or not HAS_WORD.search(text)
            or len(text.split()) > max_words or UNITS_LINE.match(text)):
        return None
    return emphasis(block, body) or None


def text_check(text: str, min_words: int = SENTENCE_MIN_WORDS) -> str:
    text = text.strip().rstrip("\"')]")
    return "sentence" if text[-1:] in ".!?" and len(text.split()) >= min_words else "short_line"


def html_only(group: dict, section: dict, blocks: list[Block], body: float,
              cfg: dict | None = None) -> tuple[list[dict], int]:
    """Rows for the heading-like blocks of one section that step 4 did not read as headings,
    and the number of heading-like blocks in the section."""
    cfg = cfg or {}
    max_words = cfg.get("max_words", MAX_WORDS)
    min_words = cfg.get("sentence_min_words", SENTENCE_MIN_WORDS)
    lo = section["offset_start"]
    hi = lo + len(section["content"])
    units = [dict(u, start=u["start"] + lo, end=u["end"] + lo) for u in group.get("units", [])]
    inside = [b for b in blocks if b.start >= lo and b.end <= hi]
    rows, found = [], 0
    for k, b in enumerate(inside):
        if k == 0 and ITEM_TITLE.match(b.text.strip()):
            continue
        style = html_heading(b, body, max_words)
        if style is None:
            continue
        found += 1
        over = [u for u in units if u["start"] < b.end and u["end"] > b.start]
        if any(u["role"] in HEADING_ROLES for u in over):
            continue
        after = blocks[b.idx + 1] if b.idx + 1 < len(blocks) else None
        text = " ".join(b.text.split())
        rows.append({
            "kind": "html_only", "text": text, "html_style": ";".join(style),
            "step4_role": ";".join(dict.fromkeys(u["role"] for u in over)) or "none",
            "prefilter": ";".join(dict.fromkeys(u["prefilter"] for u in over if u["prefilter"])),
            "rule_heading": ";".join(dict.fromkeys(u["rule_heading"] for u in over if u["rule_heading"])),
            "text_check": text_check(text, min_words),
            "position": f"{group['accession']}|{group['section_key']}|{b.start - lo}",
            "next_text": link_words(after) if after is not None else "",
            "link_suffix": link_words(after) if after is not None and not after.in_table else "",
            "level": "", "reason": "",
        })
    return rows, found


def is_plain(row: dict, body: float) -> bool:
    if row["match"] == "inside":
        return True
    st = row["style"]
    return bool(st) and not any(st[k] for k in ("bold", "italic", "underline", "caps", "center")) \
        and st["size"] <= body + SIZE_STEP


def step4_plain(rows: list[dict], body: float, cfg: dict | None = None) -> list[dict]:
    """Rows for the step-4 headings of a filing (infer_section rows) the HTML shows as plain text."""
    min_words = (cfg or {}).get("sentence_min_words", SENTENCE_MIN_WORDS)
    out = []
    for r in rows:
        if r["level"] <= 0 or r["match"] in ("unmatched", "missing_html") or not is_plain(r, body):
            continue
        out.append({
            "kind": "step4_plain", "text": r["text"],
            "html_style": "inside paragraph" if r["match"] == "inside" else "plain",
            "step4_role": r.get("role") or "heading", "prefilter": "", "rule_heading": "",
            "text_check": text_check(r["text"], min_words), "position": r["heading_id"],
            "next_text": r.get("next_text", ""), "link_suffix": r.get("link_suffix", ""),
            "level": r["level"], "reason": r["reason"],
        })
    return out
