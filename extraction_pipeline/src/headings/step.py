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
  data/output/heading_summary.csv      one row per company: match rate, levels, reasons, flags,
                                       and how many HTML headings step 4 reads as headings
  data/output/heading_html_diff.csv    HTML headings step 4 missed, and step-4 headings the HTML
                                       shows as plain text (compare.py); changes nothing
  data/interim/lead_sentences.jsonl    one row per filing: whether its HTML was read, and its lead
                                       sentences (leads.py), which step 4 reads for lead_unit_id
All three CSV files open with "# column: meaning" lines. Read them with read_output(path)
(pandas): not read_csv(comment="#"), which also cuts the open_in_edgar links at their "#".
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

from ..common import Paths, data_dir, filings, load_env, log, read_jsonl, settings
from ..ex21 import edgar
from .candidates import levels_path, section_headings
from .compare import body_size, html_only, step4_plain
from .leads import in_lead, section_leads
from .edgar_primary import Urls, atomic_write, cached_path, filing_html, text_link
from .html import align, export_text, html_blocks
from .levels import infer_section
from .tables import section_tables

STEP = "headings"
SOURCE = "html"
REASONS = ["typography", "table", "item_title_continuation", "toc", "units_line", "no_letters",
           "inside_paragraph", "plain_style", "unmatched", "missing_html", "lead_sentence"]
REASON_HELP = ("typography = level from its style; table = in a table (0); item_title_continuation = "
               "centred lines going on with the Item title (0); toc = table of contents (0); units_line = "
               "only a unit such as (In millions) (0); no_letters = no letter or digit, e.g. a rule "
               "before footnotes (0); inside_paragraph / plain_style / unmatched / "
               "missing_html = read as a sibling of the heading before it; lead_sentence = Item 1A, a "
               "risk factor's lead sentence styled as a heading (-1: step 4 labels it as a sentence)")

# How to read each file: written as "# column: meaning" lines above the header row.
REVIEW_HELP = {
    "ticker": "company", "form": "10-K or 10-Q", "accession": "filing",
    "section_key": "section of the filing (part-ii-item-7 = MD&A)",
    "heading_id": "accession|section_key|start of the heading in the Data-team text (the key step 4 reads)",
    "text": "the heading as step 4 found it",
    "level": "1 = top heading of the section, 2 = under the nearest 1 above, ...; 0 = not a heading (changes no path); "
             "-1 = a sentence (lead_sentence): labelled like the text, changes no path",
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
    "ticker": "company", "filings": "filings with kept sections",
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
    "html_headings": "HTML blocks in kept sections that look like headings (standalone, styled, short; "
                     "settings.yaml headings.compare.max_words) and are in a step-4 unit",
    "html_covered_pct": "% of those that step 4 reads as a heading, category heading or section title "
                        "(lead sentences left out: html_only_lead)",
    "html_only": "of those, the ones step 4 did not read as headings (heading_html_diff.csv kind html_only, "
                 "step4_role not none)",
    "html_only_sentence": "of html_only, full sentences (text_check sentence: risk lead sentences or body)",
    "html_only_lead": "heading-like HTML blocks that are lead sentences (leads.py): not in html_headings "
                      "or html_only",
    "leads_html": "lead sentences of their own block (lead_sentences.jsonl source html)",
    "leads_html_runin": "lead sentences opening a paragraph (source html_runin)",
    "html_only_removed": "heading-like HTML blocks in no step-4 unit (step4_role none): removed in clean, "
                         "mostly running page headers and footers; not in html_headings",
    "step4_plain": "step-4 headings the HTML shows as plain text (heading_html_diff.csv kind step4_plain)",
}
DIFF_HELP = {
    "kind": "html_only = the HTML styles it as a heading, step 4 did not read it as one; "
            "step4_plain = step 4 reads it as a heading, the HTML shows plain text",
    "ticker": "company", "form": "10-K or 10-Q", "accession": "filing", "section_key": "section of the filing",
    "text": "the line (html_only: the HTML block; step4_plain: the heading as step 4 found it)",
    "html_style": "html_only: what makes it stand out (bold / underline / italic / caps / size N = larger "
                  "than the body text); step4_plain: plain or inside paragraph",
    "step4_role": "what step 4 made of it: heading, or for html_only the role of the units "
                  "it overlaps (body, list_group; none = in no unit: removed in clean, mostly running page headers / footers)",
    "prefilter": "html_only: step 4's prefilter of those units (table_residue, too_short, ...)",
    "rule_heading": "html_only, Item 1A: high / medium = step 4 proposes it as a risk heading (lead sentence)",
    "text_check": "sentence = ends in . ! ? with headings.compare.sentence_min_words+ words (a risk lead "
                  "sentence or body, not a heading); short_line = otherwise",
    "level": "step4_plain: its level in heading_levels.jsonl", "reason": "step4_plain: why it got that level",
    "position": "accession|section_key|start in the Data-team text (step4_plain: the heading_id)",
    "next_text": "first words of what follows",
    "lead": "html_only: yes = the block is a lead sentence (lead_sentences.jsonl), left out of html_covered_pct",
    "open_in_edgar": "link to the filing on sec.gov that scrolls to the line and highlights it",
}


def help_lines(columns: dict[str, str]) -> str:
    return "".join(f"# {name}: {meaning}\n" for name, meaning in columns.items())


def read_output(path):
    """One of this step's CSV files as a pandas DataFrame, without its "# column: meaning" lines.
    (read_csv(comment="#") would also cut each open_in_edgar link at its "#".)"""
    import pandas as pd
    with open(path, encoding="utf-8", newline="") as stream:
        body = "".join(line for line in stream if not line.startswith("# "))
    return pd.read_csv(io.StringIO(body))


def htm_dir() -> Path:
    return data_dir() / "edgar" / "primary"


def review_path() -> Path:
    return data_dir() / "output" / "heading_review.csv"


def summary_path() -> Path:
    return data_dir() / "output" / "heading_summary.csv"


def diff_path() -> Path:
    return data_dir() / "output" / "heading_html_diff.csv"


def filing_rows(groups: list[dict], sections: dict[str, dict], html_path: Path | None,
                use_indent: bool, compare: dict | None = None,
                lead_cfg: dict | None = None, table_cfg: dict | None = None) -> tuple[list[dict], dict, list[dict]]:
    """Rows for one filing's candidate headings, facts about the filing for the summary, and the
    rows comparing its HTML headings with step 4's (compare.py)."""
    info = {"html": "missing", "sha_differs": None, "html_headings": 0, "html_only": 0, "html_removed": 0,
            "html_only_lead": 0, "leads": {}, "tables": {}}
    blocks, text = [], ""
    if html_path is not None and html_path.is_file() and sections:
        text = export_text(list(sections.values()))
        blocks = html_blocks(html_path)
        align(blocks, text)
        sha = hashlib.sha256(html_path.read_bytes()).hexdigest()
        export_sha = next(iter(sections.values())).get("source_sha256")
        info.update(html="ok", sha_differs=bool(export_sha) and export_sha != sha)
    rows = []
    for group in groups:
        section = sections.get(group["section_key"]) if blocks else None
        got = infer_section(group, section, blocks, text, use_indent)
        if section is not None:
            info["leads"][group["section_key"]] = section_leads(section, blocks, text, got, lead_cfg)
            info["tables"][group["section_key"]] = section_tables(section, blocks, table_cfg)
        for row in got:
            rows.append({"ticker": group["ticker"], "form": group["form"], "accession": group["accession"],
                         "section_key": group["section_key"], **row})
    nexts = {h["heading_id"]: h["next_text"] for g in groups for h in g["headings"]}
    roles = {h["heading_id"]: h.get("role") for g in groups for h in g["headings"]}
    by_look = {h["heading_id"] for g in groups for h in g["headings"] if h.get("category_by_look")}
    risk = {h["heading_id"] for g in groups for h in g["headings"]
            if (sections.get(g["section_key"]) or {}).get("section_type") == "risk_factors"
            or g["section_key"].endswith("item-1a")}
    for row in rows:
        row["next_text"] = nexts.get(row["heading_id"], "")
        in_risk = row["heading_id"] in risk
        row["risk_category"] = ("yes" if row["level"] == 1 else "no") if in_risk else ""
        row["category_by_look"] = ("yes" if row["heading_id"] in by_look else "no") if in_risk else ""
        row["role"] = roles.get(row["heading_id"])
    diff: list[dict] = []
    if blocks:
        body = body_size(blocks)
        for group in groups:
            section = sections.get(group["section_key"])
            if section is None:
                continue
            got, found = html_only(group, section, blocks, body, compare)
            leads = info["leads"].get(group["section_key"], [])
            for r in got:
                r["lead"] = "yes" if in_lead(int(r["position"].rsplit("|", 1)[1]), leads) else ""
            removed = sum(r["step4_role"] == "none" for r in got)
            lead = sum(r["step4_role"] != "none" and r["lead"] == "yes" for r in got)
            info["html_headings"] += found - lead
            info["html_only"] += len(got) - removed - lead
            info["html_only_lead"] += lead
            info["html_removed"] += removed
            diff += [dict(r, ticker=group["ticker"], form=group["form"], accession=group["accession"],
                          section_key=group["section_key"]) for r in got]
        for r in step4_plain(rows, body, compare):
            source = next(x for x in rows if x["heading_id"] == r["position"])
            diff.append(dict(r, **{k: source[k] for k in ("ticker", "form", "accession", "section_key")}))
    return rows, info, diff


def leads_path() -> Path:
    from .candidates import leads_path as path
    return path()


def write_leads(info: dict[str, dict]) -> None:
    """One row per filing: {"accession", "html": true when its HTML was read, "leads": [...]}.
    A filing without HTML has no leads here; step 4 then uses its risk heading rule instead."""
    def write(stream):
        for accession, facts in info.items():
            leads = [dict(x, section_key=k) for k, ls in facts["leads"].items() for x in ls]
            stream.write(json.dumps({"accession": accession, "html": facts["html"] == "ok", "leads": leads},
                                    ensure_ascii=False) + "\n")
    atomic_write(leads_path(), write)


def tables_path() -> Path:
    from .candidates import tables_path as path
    return path()


def write_tables(info: dict[str, dict]) -> None:
    """One row per filing: {"accession", "html": true when its HTML was read, "rows": [...]}: the
    HTML table rows in its kept sections, with their table's kind (tables.py)."""
    def write(stream):
        for accession, facts in info.items():
            rows = [dict(x, section_key=k) for k, xs in facts["tables"].items() for x in xs]
            stream.write(json.dumps({"accession": accession, "html": facts["html"] == "ok", "rows": rows},
                                    ensure_ascii=False) + "\n")
    atomic_write(tables_path(), write)


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


def write_diff(diff: list[dict]) -> None:
    def write(stream):
        stream.write(help_lines(DIFF_HELP))
        w = csv.DictWriter(stream, fieldnames=list(DIFF_HELP), extrasaction="ignore")
        w.writeheader()
        w.writerows(diff)
    atomic_write(diff_path(), write)


def summarise(rows: list[dict], info: dict[str, dict], diff: list[dict] | None = None) -> list[dict]:
    """info: accession -> facts about the filing, with its "ticker"."""
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    diff_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in diff or []:
        diff_by_ticker[r["ticker"]].append(r)
    accessions_of: dict[str, set] = defaultdict(set)
    for accession, facts in info.items():
        accessions_of[facts.get("ticker")].add(accession)
    out = []
    for ticker in sorted(set(by_ticker) | {t for t in accessions_of if t}):
        rs = by_ticker[ticker]
        ds = diff_by_ticker[ticker]
        accessions = accessions_of[ticker] | {r["accession"] for r in rs}
        html_headings = sum(info[a]["html_headings"] for a in accessions_of[ticker])
        html_only_n = sum(info[a]["html_only"] for a in accessions_of[ticker])
        removed_n = sum(info[a]["html_removed"] for a in accessions_of[ticker])
        reasons = Counter(r["reason"] for r in rs)
        levels = Counter(min(r["level"], 4) for r in rs)
        flags = Counter(f for r in rs for f in r["flags"])
        found = sum(r["match"] in ("standalone", "run_in", "inside") for r in rs)
        row = {"ticker": ticker, "filings": len(accessions),
               "filings_no_html": sum(info[a]["html"] != "ok" for a in accessions),
               "filings_sha_differs": sum(bool(info[a]["sha_differs"]) for a in accessions),
               "headings": len(rs), "found_in_html_pct": round(100 * found / len(rs), 1) if rs else "",
               **{f"level_{k}": levels.get(k, 0) for k in (0, 1, 2, 3)}, "level_4plus": levels.get(4, 0),
               **{f"reason_{k}": reasons.get(k, 0) for k in REASONS},
               "risk_categories": sum(r["risk_category"] == "yes" for r in rs),
               "risk_categories_by_look": sum(r["category_by_look"] == "yes" for r in rs),
               "flag_risk_sentence": flags.get("risk_sentence", 0),
               "flag_before_table": flags.get("before_table", 0),
               "html_headings": html_headings,
               "html_covered_pct": round(100 * (html_headings - html_only_n) / html_headings, 1) if html_headings else "",
               "html_only": html_only_n,
               "html_only_sentence": sum(d["kind"] == "html_only" and d["step4_role"] != "none"
                                         and d["text_check"] == "sentence" and d.get("lead") != "yes" for d in ds),
               "html_only_lead": sum(info[a]["html_only_lead"] for a in accessions_of[ticker]),
               "leads_html": sum(x["source"] == "html" for a in accessions_of[ticker]
                                 for ls in info[a]["leads"].values() for x in ls),
               "leads_html_runin": sum(x["source"] == "html_runin" for a in accessions_of[ticker]
                                       for ls in info[a]["leads"].values() for x in ls),
               "html_only_removed": removed_n,
               "step4_plain": sum(d["kind"] == "step4_plain" for d in ds)}
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
    compare = cfg.get("compare") or {}
    lead_cfg = cfg.get("leads") or {}
    table_cfg = cfg.get("tables") or {}
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
    log(STEP, f"{len(todo)} filings with kept sections; {len(missing)} HTML files to download, "
              f"{len(no_url)} URLs to look up" + ("" if fetch else " (--no-fetch: skipped)"))

    rows, info, diff = [], {}, []
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
        got, info[accession], got_diff = filing_rows(groups[accession], sections, path,
                                                     filing["ticker"] in indent_tickers, compare, lead_cfg,
                                                     table_cfg)
        info[accession]["ticker"] = filing["ticker"]
        for row in got:
            row["open_in_edgar"] = text_link(url, row["text"], row["link_suffix"], row["joined_blocks"] > 1)
        for row in got_diff:
            row["open_in_edgar"] = text_link(url, row["text"], row["link_suffix"])
        rows += got
        diff += got_diff
        if n % 25 == 0:
            log(STEP, f"{n}/{len(todo)} filings")

    write_levels(rows)
    write_leads(info)
    write_tables(info)
    write_review(rows)
    write_diff(diff)
    summary = summarise(rows, info, diff)
    write_summary(summary)
    reasons = Counter(r["reason"] for r in rows)
    log(STEP, f"{len(rows)} headings -> {levels_path()}")
    tables = {(a, x["table"]): x["kind"] for a, i in info.items() for xs in i["tables"].values() for x in xs}
    kinds_t = Counter(tables.values())
    log(STEP, f"{len(tables)} HTML tables in kept sections ({', '.join(f'{k} {kinds_t[k]}' for k in sorted(kinds_t))})"
              f" -> {tables_path()}")
    log(STEP, ", ".join(f"{k} {reasons[k]}" for k in REASONS if reasons[k]))
    kinds = Counter(r["kind"] for r in diff)
    html_headings = sum(i["html_headings"] for i in info.values())
    log(STEP, f"HTML vs step 4: {html_headings} HTML headings, {kinds['html_only']} not read as headings by "
              f"step 4; {kinds['step4_plain']} step-4 headings plain in the HTML")
    sources = Counter(x["source"] for i in info.values() for ls in i["leads"].values() for x in ls)
    log(STEP, f"lead sentences: {sources['html']} html, {sources['html_runin']} html_runin -> {leads_path()}")
    log(STEP, f"review: {review_path()}  summary: {summary_path()}  diff: {diff_path()}")
    no_html = sum(i["html"] != "ok" for i in info.values())
    if no_html:
        log(STEP, f"{no_html} filings without HTML: their headings are siblings (reason missing_html)")
