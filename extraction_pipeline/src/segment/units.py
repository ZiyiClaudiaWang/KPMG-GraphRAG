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
# v8 (26 Nov 2026): no role category_heading: an Item 1A risk category is a heading at level 1, in
#     heading_path and subheading like any heading; category and section_path are unchanged.
# v9 (26 Nov 2026): tables from the HTML (step 4b): html_table; table_residue only in data tables
#     (their full sentences go to the LLM); layout tables are text; definition tables; "|" outside a
#     table is no longer a table.
# v10 (26 Nov 2026): too_short only for fragments: short complete sentences go to the LLM; short
#     ":" intros are list_intro; run-in headings opening a list intro, and short styled run-ins
#     from the HTML in any case, are headings; '"III. ...' no longer ends a sentence.
# v11 (26 Nov 2026): step 2 drops "17." page numbers before page furniture (GOOGL); a short
#     styled run-in with a verb ("We may experience supply problems.") is not a heading; styled
#     run-ins inside a paragraph are headings (WMT "Opportunity."); a stray bullet is never
#     too_short; 'Matters."The' ends a sentence; "* Not meaningful." is a table note.
# v12 (26 Nov 2026): a label row and the short names under it are one list_group (PH market
#     tables).
# v13 (26 Nov 2026): a stray bullet paragraph opening with a title-case period heading is a
#     run-in heading (WMT "•Opportunity. Retail can be ...").
# A code change that leaves every unit the same (such as splitting segment.py into this package)
# keeps the version.
SEGMENTATION_VERSION = "kelly_sentence_v13"


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
        "section_path": " > ".join(x for x in (base, *path.names()) if x),
        "section_label": base, "category": path.category, "subheading": path.subheading,
        "heading_path": path.names(),
        "role": role,
        "text": text if text is not None else sec["text"][start:end],
        "char_start": start, "char_end": end,
        "raw_spans": spans if spans is not None else raw_spans(paragraph, start, end),
        "paragraph_index": paragraph_index, "prefilter": rule, "html_table": None, "rule_heading": rule_heading,
        "risk_summary": False,
        "processing_set_id": sec["processing_set_id"], "source_locator": sec["source_locator"],
    }
    unit["segmentation_version"] = SEGMENTATION_VERSION
    unit["unit_id"] = unit_id(sec["accession_number"], sec["section_key"], unit["raw_spans"])
    return unit
