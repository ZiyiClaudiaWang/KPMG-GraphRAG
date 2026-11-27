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

from .common import ONE_FOR_ONE, Paths, filings, log, read_jsonl, settings, write_jsonl

STEP = "clean"

KEEP_FIELDS = [
    "accession_number", "ticker", "cik", "company_name", "form", "filing_date",
    "reporting_period_end", "processing_set_id", "report_id", "section_id", "section_key",
    "section_order", "part", "item", "section_type", "original_title", "source_locator",
    "source_url", "source_sha256", "offset_start", "offset_end",
]

PAGE_NUMBER = re.compile(r"^\d{1,3}$")
# Alphabet's page footer is "17." followed by its page header "Table of Contents | Alphabet Inc.":
# with a period it is a page number only when page furniture (or the section end) follows,
# so a numbered item "1." on its own line stays.
PAGE_NUMBER_DOT = re.compile(r"^\d{1,3}\.$")
PAGE_LABEL = re.compile(r"^(part\s+[ivx]+|item\s+\d{1,2}[a-c]?)\.?$", re.IGNORECASE)
TOC_LINK = re.compile(r"^table of contents$", re.IGNORECASE)
# McKesson's page top: "Table of Contents | MD&A Index |" (a link row, not a table); Alphabet's:
# "Table of Contents | Alphabet Inc." (one period, at the end: a company name, not a sentence).
NAV_LINKS = re.compile(r"^table of contents\s*\|[^.]{0,60}\.?$", re.IGNORECASE)
# Microsoft's page footer links to the next items: "Item 1B, 1C", "Item 9B, 9C, 10, 11".
ITEM_LINKS = re.compile(r"^item\s+\d{1,2}[a-c]?(?:,\s*\d{1,2}[a-c]?)+$", re.IGNORECASE)
# McKesson's running header in MD&A: "FINANCIAL REVIEW (Continued)", and "(Concluded)" on its last page; it repeats in one
# section only, so repeated_headers() (which needs several sections) misses it.
CONTINUED_HEADER = re.compile(r"\((?:continued|concluded)\)$", re.IGNORECASE)
CONTINUED_HEADER_MAX_CHARS = 90
# McKesson's 10-Q repeats "(UNAUDITED)" under that header on every page.
UNAUDITED_LABEL = re.compile(r"^\(unaudited\)$", re.IGNORECASE)
# A paragraph never ends on these words; when it does, a page break cut the sentence,
# even if the next page starts with a capital ("...blocked by the" / "Sixth Circuit ...").
DANGLING_WORDS = {"a", "an", "the", "of", "to", "by", "in", "for", "with", "on", "at", "from",
                  "and", "or", "as", "that", "our", "its", "their"}
# A bullet on its own line. "o" is a second-level bullet; "\uf0b7" is the Symbol-font bullet
# (NetApp); "• |" is a bullet exported as its own table cell (Microsoft, NetApp, Nike).
BULLET_ONLY = re.compile(r"^(?:[•●◦▪‣\uf0b7–-]|o)(?:\s*\|)?$")
# The bullet that opens an item, with the "|" of a bullet cell after it: "• | Revenue grew 6%."
LEADING_BULLET = re.compile(r"^(?:[•●◦▪‣\uf0b7]|[–-](?=\s))\s*(?:\|\s*)?")
TERMINAL = re.compile(r"[.?!:;\"”’)]$")
MIN_WORDS_TO_JOIN = 8
# A page that starts with an amount, or with a parenthesis followed by more of the sentence.
CONTINUES_AFTER_BREAK = re.compile(r"[$€£]\s?\d|\([^()]{1,80}\)[,;]?\s+[a-z]")
# A paragraph that ends on a closing parenthesis or quote with no sentence end before it
# ("...(including annual changes in compensation)", "...in other companies’", "...could (i)"):
# not a sentence end when the next paragraph goes on in lower case.
CLOSES_WITHOUT_END = re.compile(r"(?<![.?!:;\"”’)])[\"”’)]+$")


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
    """A row with a "|" at one end. Used for page headers ("Apple Inc. | 2025 Form 10-K | 5" is
    one); for tables in the text use has_table_cells()."""
    return text.startswith("|") or text.endswith("|")


# A sentence ends in . ? or !, perhaps followed by a closing quote or parenthesis.
SENTENCE_END = re.compile(r"[.?!][\"”’)]*$")


def has_table_cells(text: str) -> bool:
    """A table row or cell as the Data team exports tables: cells joined by " | "
    ("Foreign Operations | 45", "Three Months Ended | Six Months Ended"), or one cell with a
    "|" at an end ("Chief Executive Officer |").

    A sentence with a stray "|" at one end is not a table: Microsoft's risk factors put one
    before the text ("| We derive substantial revenue ..."), and an executive bio can end
    "... Boulder. |". So a "|" at either end is ignored when what is left is a sentence.
    A bullet exported as its own cell ("• | Revenue grew 6%.") is a list item, not a table.
    """
    if "|" not in text:
        return False
    core = LEADING_BULLET.sub("", text.strip()).strip("|").strip()
    return "|" in core or not SENTENCE_END.search(core)


def repeated_headers(sections: list[dict], cfg: dict) -> set[str]:
    """Short paragraphs that repeat across many sections of a filing are running page headers.

    Requiring several sections keeps repeated sub-headings inside one section
    (e.g. "Fiscal Year 2026 Compared with Fiscal Year 2025" in MD&A).

    A line of words only (no digit, no "|") must also name the company ("McKESSON
    CORPORATION", "Medtronic plc", "PARKER-HANNIFIN CORPORATION") or be a "Table of Contents"
    link: Microsoft's segment headings "More Personal Computing", "Intelligent Cloud" repeat
    in Items 1, 7 and 8 too, and are not page headers. Running headers with a page or form
    number ("Apple Inc. | 2025 Form 10-K | 5", "- 12 -") are not affected.
    """
    name_words = company_words(sections[0].get("company_name") if sections else None)
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
        and (not WORDS_ONLY.match(key) or names_company(key, name_words) or TOC_WORDS.search(key))
    }


# A repeated line of words only: a heading candidate, not a page or form number.
WORDS_ONLY = re.compile(r"^[^\d|#]+$")
TOC_WORDS = re.compile(r"table of contents?", re.IGNORECASE)
COMPANY_SUFFIXES = {"inc", "corp", "corporation", "co", "company", "plc", "ltd", "llc", "the"}


def company_words(name: str | None) -> set[str]:
    """Distinctive words of the company name: "PARKER-HANNIFIN CORPORATION" -> {parker, hannifin}."""
    words = re.findall(r"[a-z]+", (name or "").lower().replace("’", "").replace("'", ""))
    return {w for w in words if len(w) >= 3 and w not in COMPANY_SUFFIXES}


def names_company(text: str, name_words: set[str]) -> bool:
    return bool(name_words & set(re.findall(r"[a-z]+", text.lower().replace("’", "").replace("'", ""))))


def drop_rule(text: str, headers: set[str]) -> str | None:
    """Name of the rule that removes this paragraph, or None to keep it."""
    if PAGE_NUMBER.match(text):
        return "page_number"
    if PAGE_LABEL.match(text):
        return "page_label"
    if TOC_LINK.match(text) or NAV_LINKS.match(text) or ITEM_LINKS.match(text):
        return "toc_link"
    if len(text) <= CONTINUED_HEADER_MAX_CHARS and CONTINUED_HEADER.search(text):
        return "continued_header"
    if UNAUDITED_LABEL.match(text):
        return "unaudited_label"
    if header_key(text) in headers:
        return "page_header"
    return None


def walk(sec: dict, headers: set[str], decisions: dict[str, bool] | None = None) -> tuple[list, list, list]:
    """Step 2's single pass over a section: drop furniture, attach lone bullets, join page breaks.

    Returns (paragraphs, removed, boundaries):
      paragraphs  [[raw_start, raw_end], ...] per kept paragraph (its pieces in `content`)
      removed     [{raw_start, raw_end, rule, text}]
      boundaries  one record per boundary between two kept paragraphs, in order:
                  {class, prev, next, next_start, joined, prev_is_item}
    checks/diagnose.py and page_breaks.py read the boundaries, so the three never disagree.
    """
    raw = sec["content"] or ""
    paragraphs: list[list[list[int]]] = []
    items: list[bool] = []                 # does each paragraph open a list item?
    removed: list[dict] = []
    boundaries: list[dict] = []
    pending_bullet: tuple[int, int] | None = None
    spans = split_paragraphs(raw)
    for k, (s, e) in enumerate(spans):
        text = raw[s:e]
        rule = drop_rule(text, headers)
        if rule is None and PAGE_NUMBER_DOT.match(text):
            after = raw[spans[k + 1][0]:spans[k + 1][1]] if k + 1 < len(spans) else None
            if after is None or drop_rule(after, headers):
                rule = "page_number"
        if rule:
            removed.append({"raw_start": s, "raw_end": e, "rule": rule, "text": text})
            continue
        if BULLET_ONLY.match(text):          # a lone "•": attach it to the next paragraph
            pending_bullet = (s, e)
            continue

        pieces = [[s, e]]
        starts_with_bullet = pending_bullet is not None
        if pending_bullet:
            pieces.insert(0, list(pending_bullet))
            pending_bullet = None

        if paragraphs and not starts_with_bullet:
            prev = paragraphs[-1]
            cls = boundary_class(raw, prev, text)
            joined = joins(cls, (decisions or {}).get(boundary_id(sec, s)))
            boundaries.append({"class": cls, "prev": raw[prev[-1][0]:prev[-1][1]], "next": text,
                               "next_start": s, "joined": joined, "prev_is_item": items[-1]})
            if joined:
                prev.extend(pieces)           # paragraph split by a page break
                continue
        paragraphs.append(pieces)
        items.append(starts_with_bullet or is_item_start(text))

    if pending_bullet:                        # trailing bullet with nothing after it
        s, e = pending_bullet
        removed.append({"raw_start": s, "raw_end": e, "rule": "orphan_bullet", "text": raw[s:e]})
    return paragraphs, removed, boundaries


def clean_section(sec: dict, headers: set[str], decisions: dict[str, bool] | None = None) -> dict:
    raw = sec["content"] or ""
    paragraphs, removed, boundaries = walk(sec, headers, decisions)

    # Build cleaned text: pieces inside a paragraph are joined by one space.
    out_paragraphs, parts, offset = [], [], 0
    for p in paragraphs:
        texts, mapped, cursor = [], [], offset
        for i, (s, e) in enumerate(p):
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
    out.update(text="\n\n".join(parts), paragraphs=out_paragraphs, removed=removed,
               joined=sum(b["joined"] for b in boundaries))
    return out


# ---------- page breaks (C9) ----------
#
# Every boundary between two kept paragraphs gets a class. The class decides the join:
# JOIN_CLASSES are joined, GRAY_CLASSES are joined only when step 2b (page_breaks.py, an LLM
# yes/no) said so, every other class is never joined. checks/diagnose.py reports the same classes.

JOIN_CLASSES = ("join_lowercase", "join_close_lowercase", "join_dangling_word", "join_dangling_quote", "join_dangling_digit",
                "join_amount_paren", "join_comma", "join_open_quote")
GRAY_CLASSES = ("C9_gray_no_punct_capital", "C9_gray_short_prev_lower")
BOUNDARY_CLASSES = ("sentence_end", "table_row", "short_prev", "heading_then_text", "text_then_heading",
                    "list_item_end", "bullet_item_end", "next_is_bullet", "checkbox") + JOIN_CLASSES + GRAY_CLASSES

# A lettered or numbered list item: "a.we maintain", "(b) the", "1.Product purchases", "(ii)pertain".
# "e.g." and "i.e." are not items; nor is "1.5 million". The one definition (segment, diagnose).
ITEM_MARKER = re.compile(r"^(?:\((?:[a-z]|[ivx]{2,4}|\d{1,2})\)|[a-z]\.(?![a-z]\.)|\d{1,2}\.(?!\d))\s?(?=[A-Za-z“\"(])")
CHECKBOX_END = re.compile(r"(?:[☐☒☑ýþ]|\bNo o)$")
# A word in capitals for heading purposes: "Results", "COVID-19", "iPhone" (a brand in lower case).
CAPITALISED = re.compile(r"^(?:[A-Z0-9]|[a-z]{1,3}[A-Z])")
OPENING_MARK = "“\"‘'("


def looks_like_heading(text: str, max_words: int = 25) -> bool:
    """A title rather than a sentence: no end punctuation, at most max_words words, and in
    capitals or title case (60% of the words of 4+ letters capitalised): "Critical Audit Matter
    Description", "Item 2. Management's Discussion and Analysis ...", "Three Months Ended
    December 31, 2021 Compared with Three Months Ended December 31, 2020"."""
    words = text.split()
    if not words or len(words) > max_words or SENTENCE_END.search(text) or text.endswith((",", ";", ":")):
        return False
    if text.upper() == text and any(c.isalpha() for c in text):
        return True
    long_words = [w.strip("(“\"‘") for w in words if len(w.strip("(“\"‘,")) >= 4]
    if not long_words:
        return CAPITALISED.match(words[0]) is not None and len(words) <= 3
    return sum(bool(CAPITALISED.match(w)) for w in long_words) / len(long_words) >= 0.6


def is_item_start(text: str) -> bool:
    """A paragraph that opens a list item: a bullet ("•", "• |", "o ") or "a." / "(1)"."""
    return bool(LEADING_BULLET.match(text) or text.startswith("o ") or ITEM_MARKER.match(text))


def boundary_class(raw: str, prev_pieces: list[list[int]], next_text: str) -> str:
    """Why a paragraph and the next one are, or are not, one paragraph split by a page break.

    In order: table rows are never joined; a paragraph that ends a sentence ends there; a short
    paragraph is a heading unless lower case follows (then gray: "Earnings (loss) per common share
    attributable" / "to McKesson"); "...; and" ends a list item; then the joins: lower case next,
    an amount or a parenthesis that the sentence goes on after, a word no sentence ends on ("the",
    "of", "and") before a word, a quote or a number, a comma, a quote or parenthesis left open
    and closed in the next paragraph; then what is never joined: a
    bullet next, a check box ("Yes ☒ No ☐"), a list item, a title before or after. What is left
    (no end punctuation, capital next) is gray and goes to the LLM (page_breaks.py).
    """
    s, e = prev_pieces[-1][:2]
    prev = raw[s:e]
    first = raw[prev_pieces[0][0]:prev_pieces[0][1]]          # where the previous paragraph starts
    if has_table_cells(prev) or has_table_cells(next_text):
        return "table_row"
    words = prev.split()
    if (CLOSES_WITHOUT_END.search(prev) and next_text[:1].islower() and len(words) >= MIN_WORDS_TO_JOIN
            and not is_item_start(next_text) and not looks_like_heading(prev)):
        return "join_close_lowercase"
    if TERMINAL.search(prev):
        return "sentence_end"
    if len(words) < MIN_WORDS_TO_JOIN:
        if next_text[:1].islower() and not looks_like_heading(prev):
            return "C9_gray_short_prev_lower"
        return "heading_then_text" if next_text[:1].islower() else "short_prev"
    last = words[-1].lower()
    if last in ("and", "or") and words[-2].endswith(";"):
        return "list_item_end"
    if next_text[:1].islower():
        return "join_lowercase"
    if CONTINUES_AFTER_BREAK.match(next_text):
        return "join_amount_paren"
    if is_item_start(next_text):
        return "next_is_bullet"
    if last in DANGLING_WORDS:
        if next_text[:1].isalpha():
            return "join_dangling_word"
        if next_text[:1] in OPENING_MARK:
            return "join_dangling_quote"
        if next_text[:1].isdigit():
            return "join_dangling_digit"
    if prev.endswith(",") and not looks_like_heading(next_text, 12):
        return "join_comma"
    if ((prev.count("“") > prev.count("”") and "”" in next_text[:200])
            or (prev.count("(") > prev.count(")") and ")" in next_text[:100] and next_text[:1] != "(")):
        return "join_open_quote"      # 'See “Item 1. ... Notes to Consolidated Financial' / 'Statements – ...”.'
    if CHECKBOX_END.search(prev):
        return "checkbox"
    if is_item_start(first):
        return "bullet_item_end"
    if looks_like_heading(prev):
        return "heading_then_text"
    if looks_like_heading(next_text, 12):
        return "text_then_heading"
    return "C9_gray_no_punct_capital"


def boundary_id(sec: dict, next_start: int) -> str:
    """The id of the page break before the paragraph that starts at next_start in `content`."""
    return f"{sec['accession_number']}|{sec['section_key']}|{next_start}"


def joins(cls: str, decision: bool | None = None) -> bool:
    """Whether a boundary of this class is joined. A gray one only when decision is True: the
    answer step 2b (page_breaks.py) saved for it; without one it stays two paragraphs.

    Examples. Joined: "...blocked by the" / "Sixth Circuit"; "...through February 27," / "2025, we
    had"; "...issuance of 2026 Notes and" / "2028 Notes". Not joined: "Mac | 8,675" / "iPad | 8,435"
    (table rows); "...; and" / "fluctuations in ..." (a list item, its bullet dropped); "iPhone" /
    "iPhone net sales ..." (a heading); "...the 2023 fiscal year" / "COVID-19 and Related Impacts".
    """
    return cls in JOIN_CLASSES or (cls in GRAY_CLASSES and decision is True)


def should_join(raw: str, prev_pieces: list[list[int]], next_text: str, decision: bool | None = None) -> bool:
    """joins() for one boundary, from the previous paragraph's pieces and the next paragraph."""
    return joins(boundary_class(raw, prev_pieces, next_text), decision)


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
    from .page_breaks import load_decisions     # step 2b's answers for gray page breaks, if any
    cfg = settings()["clean"]
    decisions = load_decisions()
    if decisions:
        log(STEP, f"{len(decisions)} page-break decisions from step 2b, {sum(decisions.values())} joined")
    for f in filings():
        sections = list(read_jsonl(Paths.sections(f)))
        headers = repeated_headers(sections, cfg)
        cleaned = [clean_section(sec, headers, decisions) for sec in sections]
        write_jsonl(Paths.cleaned(f), cleaned)
        n_removed = sum(len(c["removed"]) for c in cleaned)
        n_joined = sum(c["joined"] for c in cleaned)
        log(STEP, f"{f['ticker']} {f['form']} {f['accession']}: removed {n_removed} paragraphs, "
                  f"joined {n_joined} split paragraphs, {len(headers)} header patterns")


if __name__ == "__main__":
    run()