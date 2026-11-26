"""Step 4b - Heading levels from each filing's EDGAR HTML. No LLM.

  python run_pipeline.py --steps headings,segment
  python -m src.headings                              the same step on its own (then rerun segment)
  python -m src.headings --no-fetch                   only filings whose HTML is already cached

Reads the candidate headings of the kept sections (steps 2-3 must have run), the Data-team
sections (data/sections/) and the primary document of each filing, downloaded once from EDGAR to
data/edgar/primary/<TICKER>_<accession>.htm (needs SEC_USER_AGENT in .env). The filing URLs
are kept in data/edgar/primary/urls.json for the links in the review file.

Writes:
  data/interim/heading_levels.jsonl    the levels step 4 reads (candidates.py)
  data/output/heading_review.csv       one row per heading: level, parent, path, reason, flags, style
  data/output/heading_summary.csv      one row per company: match rate, levels, reasons, flags
Both CSV files open with "# column: meaning" lines (pandas: read_csv(path, comment="#")).
"""

from __future__ import annotations

import csv
import hashlib
import json
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

from ..common import Paths, data_dir, filings, load_env, log, read_jsonl, settings
from ..ex21 import edgar
from .candidates import levels_path, section_headings
from .edgar_primary import Urls, atomic_write, cached_path, filing_html, text_link
from .html import align, export_text, html_blocks
from .levels import infer_section

STEP = "headings"
SOURCE = "html"
REASONS = ["typography", "table", "item_title_continuation", "toc", "units_line", "no_letters",
           "inside_paragraph", "plain_style", "unmatched", "missing_html"]
REASON_HELP = ("typography = level from its style; table = in a table (0); item_title_continuation = "
               "centred line after the Item title (0); toc = table of contents (0); units_line = "
               "only a unit such as (In millions) (0); no_letters = no letter or digit, e.g. a rule "
               "before footnotes (0); inside_paragraph / plain_style / unmatched / "
               "missing_html = read as a sibling of the heading before it")

# How to read each file: written as "# column: meaning" lines above the header row.
REVIEW_HELP = {
    "ticker": "company", "form": "10-K or 10-Q", "accession": "filing",
    "section_key": "section of the filing (part-ii-item-7 = MD&A)",
    "heading_id": "accession|section_key|start of the heading in the Data-team text (the key step 4 reads)",
    "text": "the heading as step 4 found it",
    "level": "1 = top heading of the section, 2 = under the nearest 1 above, ...; 0 = not a heading (changes no path)",
    "parent_text": "the heading this one sits under (empty = top of the section)",
    "heading_path": "the headings above the text that follows, top first: what goes into section_path",
    "reason": "why it got this level: " + REASON_HELP,
    "flags": "for review, never change the level: risk_sentence = in Item 1A, 10+ words (maybe a risk "
             "factor's lead sentence read as a heading, so it never becomes a Claim); before_table = the next "
             "HTML block is a table (maybe a table title)",
    "match": "where it is in the HTML: standalone = a block of its own; run_in = start of a paragraph; "
             "inside = middle of a paragraph; unmatched / missing_html = not found",
    "next_text": "first words of the text below it",
    "risk_category": "Item 1A only: yes = a risk category (level 1), no = a sub-heading under one",
    "category_by_look": "Item 1A only: yes = step 4's text rule alone would call it a category (capitals or title "
                        "case, \"Risks related to ...\"); where it differs from risk_category, the level decided",
    "open_in_edgar": "link to the filing on sec.gov that scrolls to this heading and highlights it "
                     "(Chrome / Edge / Safari; if nothing is highlighted, search for the text)",
    "joined_blocks": "number of HTML blocks the heading spans (2+ = the HTML splits it over lines; 0 = not found)",
    "size": "font size in pt", "bold": "bold (True/False)", "underline": "underlined", "caps": "all capitals",
    "italic": "italic", "center": "centred",
    "indent": "left indent in 9pt steps (only for companies in settings.yaml headings.indent_tickers, else 0)",
    "run_in": "a lead-in at the start of a paragraph", "in_table": "inside a table",
    "block_text": "the HTML block it was found in (first 160 characters)",
}
REVIEW_COLS = list(REVIEW_HELP)
SUMMARY_HELP = {
    "ticker": "company", "filings": "filings with candidate headings",
    "filings_no_html": "filings whose HTML could not be read (their headings are siblings); should be 0",
    "filings_sha_differs": "filings whose EDGAR HTML hash differs from the export's source_sha256 (known: all; ask the Data team)",
    "headings": "candidate headings", "found_in_html_pct": "% of headings found in the HTML (standalone, run_in or inside)",
    "level_0": "not headings (table headers, tables of contents, unit lines)",
    "level_1": "top headings of a section", "level_2": "headings at level 2", "level_3": "at level 3",
    "level_4plus": "at level 4 or deeper (many = check the nesting)",
    **{f"reason_{k}": f"headings with reason {k}" for k in REASONS},
    "risk_categories": "Item 1A headings that are risk categories (level 1)",
    "risk_categories_by_look": "Item 1A headings step 4's text rule alone would call categories",
    "flag_risk_sentence": "headings flagged risk_sentence (check: a risk factor's lead sentence or a heading?)",
    "flag_before_table": "headings flagged before_table (check: table title or heading?)",
}


def help_lines(columns: dict[str, str]) -> str:
    return "".join(f"# {name}: {meaning}\n" for name, meaning in columns.items())


def htm_dir() -> Path:
    return data_dir() / "edgar" / "primary"


def review_path() -> Path:
    return data_dir() / "output" / "heading_review.csv"


def summary_path() -> Path:
    return data_dir() / "output" / "heading_summary.csv"


def filing_rows(groups: list[dict], sections: dict[str, dict], html_path: Path | None,
                use_indent: bool) -> tuple[list[dict], dict]:
    """Rows for one filing's candidate headings, and facts about the filing for the summary."""
    info = {"html": "missing", "sha_differs": None}
    blocks, text = [], ""
    if html_path is not None and html_path.is_file() and sections:
        text = export_text(list(sections.values()))
        blocks = html_blocks(html_path)
        align(blocks, text)
        sha = hashlib.sha256(html_path.read_bytes()).hexdigest()
        export_sha = next(iter(sections.values())).get("source_sha256")
        info = {"html": "ok", "sha_differs": bool(export_sha) and export_sha != sha}
    rows = []
    for group in groups:
        section = sections.get(group["section_key"]) if blocks else None
        for row in infer_section(group, section, blocks, text, use_indent):
            rows.append({"ticker": group["ticker"], "form": group["form"], "accession": group["accession"],
                         "section_key": group["section_key"], **row})
    nexts = {h["heading_id"]: h["next_text"] for g in groups for h in g["headings"]}
    roles = {h["heading_id"]: h.get("role") for g in groups for h in g["headings"]}
    risk = {h["heading_id"] for g in groups for h in g["headings"]
            if (sections.get(g["section_key"]) or {}).get("section_type") == "risk_factors"
            or g["section_key"].endswith("item-1a")}
    for row in rows:
        row["next_text"] = nexts.get(row["heading_id"], "")
        in_risk = row["heading_id"] in risk
        row["risk_category"] = ("yes" if row["level"] == 1 else "no") if in_risk else ""
        row["category_by_look"] = ("yes" if roles.get(row["heading_id"]) == "category_heading" else "no") if in_risk else ""
    return rows, info


def write_levels(rows: list[dict]) -> None:
    def write(stream):
        for r in rows:
            stream.write(json.dumps({"heading_id": r["heading_id"], "text": r["text"], "level": r["level"],
                                     "source": SOURCE, "reason": r["reason"]}, ensure_ascii=False) + "\n")
    atomic_write(levels_path(), write)


def write_review(rows: list[dict]) -> None:
    def write(stream):
        stream.write(help_lines(REVIEW_HELP))
        w = csv.DictWriter(stream, fieldnames=REVIEW_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(dict(r, flags=";".join(r["flags"]), heading_path=" > ".join(r["heading_path"]),
                            **(r["style"] or {})))
    atomic_write(review_path(), write)


def summarise(rows: list[dict], info: dict[str, dict]) -> list[dict]:
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    out = []
    for ticker in sorted(by_ticker):
        rs = by_ticker[ticker]
        accessions = {r["accession"] for r in rs}
        reasons = Counter(r["reason"] for r in rs)
        levels = Counter(min(r["level"], 4) for r in rs)
        flags = Counter(f for r in rs for f in r["flags"])
        found = sum(r["match"] in ("standalone", "run_in", "inside") for r in rs)
        row = {"ticker": ticker, "filings": len(accessions),
               "filings_no_html": sum(info[a]["html"] != "ok" for a in accessions),
               "filings_sha_differs": sum(bool(info[a]["sha_differs"]) for a in accessions),
               "headings": len(rs), "found_in_html_pct": round(100 * found / len(rs), 1),
               **{f"level_{k}": levels.get(k, 0) for k in (0, 1, 2, 3)}, "level_4plus": levels.get(4, 0),
               **{f"reason_{k}": reasons.get(k, 0) for k in REASONS},
               "risk_categories": sum(r["risk_category"] == "yes" for r in rs),
               "risk_categories_by_look": sum(r["category_by_look"] == "yes" for r in rs),
               "flag_risk_sentence": flags.get("risk_sentence", 0),
               "flag_before_table": flags.get("before_table", 0)}
        out.append(row)
    return out


def write_summary(summary: list[dict]) -> None:
    def write(stream):
        stream.write(help_lines(SUMMARY_HELP))
        w = csv.DictWriter(stream, fieldnames=list(SUMMARY_HELP))
        w.writeheader()
        w.writerows(summary)
    atomic_write(summary_path(), write)


def run(fetch: bool = True) -> None:
    load_env()
    cfg = settings().get("headings") or {}
    indent_tickers = set(cfg.get("indent_tickers") or [])
    groups: dict[str, list[dict]] = defaultdict(list)
    for group in section_headings():
        groups[group["accession"]].append(group)
    if not groups:
        raise SystemExit("No candidate headings. Run clean and filter first.")
    todo = [f for f in filings() if f["accession"] in groups]
    not_cleaned = [f for f in filings() if not Paths.cleaned(f).is_file()]
    if not_cleaned:
        log(STEP, f"{len(not_cleaned)} filings have no cleaned sections and are skipped "
                  f"(run fetch, clean and filter first), e.g. {not_cleaned[0]['ticker']} {not_cleaned[0]['accession']}")
    urls = Urls(htm_dir())
    missing = [f for f in todo if not cached_path(f, htm_dir()).is_file()]
    no_url = [f for f in todo if f["accession"] not in urls.urls]
    if fetch and (missing or no_url) and not edgar.user_agent():
        raise SystemExit(f"{len(missing)} filings need their HTML and {len(no_url)} their URL from EDGAR: "
                         "set SEC_USER_AGENT='Name email' in extraction_pipeline/.env (or run with --no-fetch)")
    log(STEP, f"{len(todo)} filings with candidate headings; {len(missing)} HTML files to download, "
              f"{len(no_url)} URLs to look up" + ("" if fetch else " (--no-fetch: skipped)"))

    rows, info = [], {}
    for n, filing in enumerate(todo, 1):
        accession = filing["accession"]
        path = url = None
        try:
            path = filing_html(filing, htm_dir(), fetch, urls)
            url = urls.get(filing, fetch)
        except (OSError, ValueError, urllib.error.URLError, etree.LxmlError) as exc:
            log(STEP, f"{filing['ticker']} {accession}: no HTML or URL ({exc})")
        sections = {}
        if Paths.sections(filing).is_file():
            sections = {s["section_key"]: s for s in read_jsonl(Paths.sections(filing))}
        else:
            log(STEP, f"{filing['ticker']} {accession}: no section export in data/sections/")
        got, info[accession] = filing_rows(groups[accession], sections, path,
                                           filing["ticker"] in indent_tickers)
        for row in got:
            row["open_in_edgar"] = text_link(url, row["text"], row["link_suffix"], row["joined_blocks"] > 1)
        rows += got
        if n % 25 == 0:
            log(STEP, f"{n}/{len(todo)} filings")

    write_levels(rows)
    write_review(rows)
    summary = summarise(rows, info)
    write_summary(summary)
    reasons = Counter(r["reason"] for r in rows)
    log(STEP, f"{len(rows)} headings -> {levels_path()}")
    log(STEP, ", ".join(f"{k} {reasons[k]}" for k in REASONS if reasons[k]))
    log(STEP, f"review: {review_path()}  summary: {summary_path()}")
    no_html = sum(i["html"] != "ok" for i in info.values())
    if no_html:
        log(STEP, f"{no_html} filings without HTML: their headings are siblings (reason missing_html)")
