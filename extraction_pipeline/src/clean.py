"""Step 2 - Clean: remove page furniture and repair paragraphs, keeping exact offsets.

Input : data/sections/<TICKER>_<accession>.jsonl   (one Data-team section per line)
Output: data/interim/clean/<TICKER>_<accession>.jsonl

Each output section keeps its provenance fields and adds:
  text        cleaned paragraphs joined by "\\n\\n"
  paragraphs  [{text, start, pieces}] where pieces = [[raw_start, raw_end, clean_start], ...]
              maps cleaned characters back to the section's original `content`
  removed     [{raw_start, raw_end, rule, text}]  everything dropped, and why
  joined      number of paragraphs merged across page breaks

Cleaning never rewrites words. Characters are only replaced one-for-one
(newline or non-breaking space -> space), so a quote can always be found in
the original content through the piece map.
"""

from __future__ import annotations

import re
from collections import Counter

from .common import Paths, filings, log, read_jsonl, settings, write_jsonl

STEP = "clean"

KEEP_FIELDS = [
    "accession_number", "ticker", "cik", "company_name", "form", "filing_date",
    "reporting_period_end", "processing_set_id", "report_id", "section_id", "section_key",
    "section_order", "part", "item", "section_type", "original_title", "source_locator",
    "source_url", "source_sha256", "offset_start", "offset_end",
]

PAGE_NUMBER = re.compile(r"^\d{1,3}$")
PAGE_LABEL = re.compile(r"^(part\s+[ivx]+|item\s+\d{1,2}[a-c]?)\.?$", re.IGNORECASE)
TOC_LINK = re.compile(r"^table of contents$", re.IGNORECASE)
BULLET_ONLY = re.compile(r"^([•●◦▪‣–-]|o)$")  # "o" is a second-level bullet
TERMINAL = re.compile(r"[.?!:;\"”’)]$")
MIN_WORDS_TO_JOIN = 8
ONE_FOR_ONE = str.maketrans({"\n": " ", "\r": " ", "\t": " ", " ": " ", " ": " ", " ": " "})


def split_paragraphs(raw: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of non-empty paragraphs, trimmed of surrounding whitespace."""
    spans, start = [], 0
    for m in re.finditer(r"\n\s*\n", raw):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(raw)))
    trimmed = []
    for s, e in spans:
        chunk = raw[s:e]
        left = len(chunk) - len(chunk.lstrip())
        right = len(chunk.rstrip())
        if right > left:
            trimmed.append((s + left, s + right))
    return trimmed


def header_key(text: str) -> str:
    """Normalise digits so 'Apple Inc. | 2025 Form 10-K | 5' and '... | 6' count as one header."""
    return re.sub(r"\d+", "#", text)


def is_table_row(text: str) -> bool:
    return text.startswith("|") or text.endswith("|")


def repeated_headers(sections: list[dict], cfg: dict) -> set[str]:
    """Short paragraphs that repeat across many sections of a filing are running page headers.

    Requiring several sections keeps repeated sub-headings inside one section
    (e.g. "Fiscal Year 2026 Compared with Fiscal Year 2025" in MD&A).
    """
    counts: Counter[str] = Counter()
    where: dict[str, set[int]] = {}
    for i, sec in enumerate(sections):
        raw = sec["content"] or ""
        for s, e in split_paragraphs(raw):
            text = raw[s:e]
            if len(text) <= cfg["repeated_paragraph_max_chars"]:
                key = header_key(text)
                counts[key] += 1
                where.setdefault(key, set()).add(i)
    return {
        key for key, n in counts.items()
        if n >= cfg["repeated_paragraph_min_count"]
        and len(where[key]) >= cfg["repeated_paragraph_min_sections"]
        and not BULLET_ONLY.match(key)
        and not is_table_row(key)
        and not TERMINAL.search(key)       # real sentences ("None.") are not headers
    }


def drop_rule(text: str, headers: set[str]) -> str | None:
    """Name of the rule that removes this paragraph, or None to keep it."""
    if PAGE_NUMBER.match(text):
        return "page_number"
    if PAGE_LABEL.match(text):
        return "page_label"
    if TOC_LINK.match(text):
        return "toc_link"
    if header_key(text) in headers:
        return "page_header"
    return None


def clean_section(sec: dict, headers: set[str]) -> dict:
    raw = sec["content"] or ""
    paragraphs: list[dict] = []    # each: {"pieces": [[raw_start, raw_end]], "after_removal": bool}
    removed: list[dict] = []
    pending_bullet: tuple[int, int] | None = None
    joined = 0
    for s, e in split_paragraphs(raw):
        text = raw[s:e]
        rule = drop_rule(text, headers)
        if rule:
            removed.append({"raw_start": s, "raw_end": e, "rule": rule, "text": text})
            continue
        if BULLET_ONLY.match(text):          # a lone "•": attach it to the next paragraph
            pending_bullet = (s, e)
            continue

        pieces = [[s, e]]
        if pending_bullet:
            pieces.insert(0, list(pending_bullet))
            pending_bullet = None

        prev = paragraphs[-1] if paragraphs else None
        if prev and should_join(raw, prev["pieces"], text):
            prev["pieces"].extend(pieces)     # paragraph split by a page break
            joined += 1
        else:
            paragraphs.append({"pieces": pieces})

    if pending_bullet:                        # trailing bullet with nothing after it
        s, e = pending_bullet
        removed.append({"raw_start": s, "raw_end": e, "rule": "orphan_bullet", "text": raw[s:e]})

    # Build cleaned text: pieces inside a paragraph are joined by one space.
    out_paragraphs, parts, offset = [], [], 0
    for p in paragraphs:
        texts, mapped, cursor = [], [], offset
        for i, (s, e) in enumerate(p["pieces"]):
            if i:
                cursor += 1                   # the joining space
            mapped.append([s, e, cursor])
            texts.append(raw[s:e].translate(ONE_FOR_ONE))
            cursor += e - s
        text = " ".join(texts)
        out_paragraphs.append({"text": text, "start": offset, "pieces": mapped})
        parts.append(text)
        offset += len(text) + 2               # the "\n\n" separator

    out = {k: sec.get(k) for k in KEEP_FIELDS}
    out.update(text="\n\n".join(parts), paragraphs=out_paragraphs, removed=removed, joined=joined)
    return out


def should_join(raw: str, prev_pieces: list[list[int]], next_text: str) -> bool:
    """Join when the previous paragraph stops mid-sentence and the next starts in lower case.

    The previous paragraph must be a run of text, not a short heading: Apple's
    product headings ("iPhone") are followed by paragraphs that also start in
    lower case.
    """
    s, e = prev_pieces[-1][:2]
    prev_text = raw[s:e]
    if is_table_row(prev_text) or is_table_row(next_text):
        return False
    if len(prev_text.split()) < MIN_WORDS_TO_JOIN:
        return False
    return not TERMINAL.search(prev_text) and next_text[:1].islower()


def raw_spans(paragraph: dict, start: int, end: int) -> list[list[int]]:
    """Map a [start, end) range of cleaned section text to spans of the original content."""
    spans = []
    for raw_s, raw_e, clean_s in paragraph["pieces"]:
        clean_e = clean_s + (raw_e - raw_s)
        lo, hi = max(start, clean_s), min(end, clean_e)
        if lo < hi:
            spans.append([raw_s + lo - clean_s, raw_s + hi - clean_s])
    return spans


def run() -> None:
    cfg = settings()["clean"]
    for f in filings():
        sections = list(read_jsonl(Paths.sections(f)))
        headers = repeated_headers(sections, cfg)
        cleaned = [clean_section(sec, headers) for sec in sections]
        write_jsonl(Paths.cleaned(f), cleaned)
        n_removed = sum(len(c["removed"]) for c in cleaned)
        n_joined = sum(c["joined"] for c in cleaned)
        log(STEP, f"{f['ticker']} {f['form']} {f['accession']}: removed {n_removed} paragraphs, "
                  f"joined {n_joined} split paragraphs, {len(headers)} header patterns")


if __name__ == "__main__":
    run()
