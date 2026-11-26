"""The unit record: one row of sentences.jsonl. Every field is listed in README Appendix A."""

from __future__ import annotations

from ..clean import raw_spans
from ..common import unit_id
from .headings import HeadingPath

# v3: bullets, list rule, abbreviation splits, headings with a period.
# v4 (4 Nov 2026): unit_id (#45); page breaks before an amount or parenthesis; "U.S. Cultural";
#     headings ending in ":"; table cells and footnote markers; Microsoft item links (step 2).
# v5 (18 Nov 2026): any table row is table_residue, two-column rows ("Acquisitions | 47") too;
#     table rows are never joined across a page break (step 2).
# v6 (19 Nov 2026): lists after a heading or "the following", lettered items, "• |" and ""
#     bullets, items whose bullet was lost; heading levels (step 4b); new page-break joins (step 2).
# v7 (26 Nov 2026): lead_source and lead_unit_id (lead sentences from the HTML, step 4b).
# A code change that leaves every unit the same (such as splitting segment.py into this package)
# keeps the version.
SEGMENTATION_VERSION = "kelly_sentence_v7"


def section_label(sec: dict) -> str:
    """'Part I Item 1A' - always with the Part prefix, for 10-K and 10-Q alike."""
    part = f"Part {sec['part']} " if sec.get("part") else ""
    return f"{part}Item {sec['item']}".strip()


def new_unit(sec: dict, path: HeadingPath, role: str, paragraph_index: int, start: int, end: int,
             rule: str | None, spans: list | None = None, text: str | None = None,
             rule_heading: str | None = None) -> dict:
    """A unit at sec["text"][start:end] (or `text`), under the headings open in `path`.
    unit_id comes from the accession, the section key and raw_spans, never the text (#45)."""
    base = section_label(sec)
    paragraph = sec["paragraphs"][paragraph_index]
    unit = {
        "accession_number": sec["accession_number"], "ticker": sec["ticker"], "form": sec["form"],
        "section_id": sec["section_id"], "section_key": sec["section_key"],
        "section_type": sec["section_type"],
        "section_path": " > ".join(x for x in (base, path.category, *path.names()) if x),
        "section_label": base, "category": path.category, "subheading": path.subheading,
        "heading_path": path.names(),
        "role": role,
        "text": text if text is not None else sec["text"][start:end],
        "char_start": start, "char_end": end,
        "raw_spans": spans if spans is not None else raw_spans(paragraph, start, end),
        "paragraph_index": paragraph_index, "prefilter": rule, "rule_heading": rule_heading,
        "risk_summary": False,
        "processing_set_id": sec["processing_set_id"], "source_locator": sec["source_locator"],
    }
    unit["segmentation_version"] = SEGMENTATION_VERSION
    unit["unit_id"] = unit_id(sec["accession_number"], sec["section_key"], unit["raw_spans"])
    return unit
