"""Step 4b - Heading levels from each filing's EDGAR HTML. No LLM.

  python run_pipeline.py --steps headings,segment
  python -m src.headings                              the same step on its own (then rerun segment)
  python -m src.headings --no-fetch                   only filings whose HTML is already cached
  python -m src.headings --compare-with old.jsonl     add the old levels to the review file

Reads the candidate headings of the kept sections (steps 2-3 must have run), the Data-team
sections (data/sections/) and the primary document of each filing, downloaded once from EDGAR to
data/edgar/primary/<TICKER>_<accession>.htm (needs SEC_USER_AGENT in .env).

Writes:
  data/interim/heading_levels.jsonl    the levels step 4 reads (candidates.py)
  data/output/heading_review.csv       one row per heading: level, parent, path, reason, flags, style
  data/output/heading_summary.csv      one row per company: match rate, levels, reasons, flags
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
from .edgar_primary import atomic_write, cached_path, filing_html
from .html import align, export_text, html_blocks
from .levels import infer_section, paths_from_levels

STEP = "headings"
SOURCE = "html"
REVIEW_COLS = ["ticker", "form", "accession", "section_key", "heading_id", "text", "level",
               "parent_text", "heading_path", "reason", "flags", "match", "next_text",
               "size", "bold", "underline", "caps", "italic", "center", "indent", "run_in",
               "block_text"]
REASONS = ["typography", "table", "item_title_continuation", "toc", "units_line",
           "inside_paragraph", "plain_style", "unmatched", "missing_html"]


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
    for row in rows:
        row["next_text"] = nexts.get(row["heading_id"], "")
    return rows, info


def write_levels(rows: list[dict]) -> None:
    def write(stream):
        for r in rows:
            stream.write(json.dumps({"heading_id": r["heading_id"], "text": r["text"], "level": r["level"],
                                     "source": SOURCE, "reason": r["reason"]}, ensure_ascii=False) + "\n")
    atomic_write(levels_path(), write)


def write_review(rows: list[dict], old: dict[str, int] | None, old_parents: dict[str, str | None]) -> None:
    cols = REVIEW_COLS + (["level_old", "parent_old"] if old is not None else [])

    def write(stream):
        w = csv.DictWriter(stream, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = dict(r, flags=";".join(r["flags"]), heading_path=" > ".join(r["heading_path"]),
                       **(r["style"] or {}))
            if old is not None:
                out["level_old"] = old.get(r["heading_id"])
                out["parent_old"] = old_parents.get(r["heading_id"])
            w.writerow(out)
    atomic_write(review_path(), write)


def summarise(rows: list[dict], info: dict[str, dict], old: dict[str, int] | None,
              old_parents: dict[str, str | None]) -> list[dict]:
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
               "flag_maybe_sentence": flags.get("maybe_sentence", 0),
               "flag_before_table": flags.get("before_table", 0)}
        if old is not None:
            both = [r for r in rs if r["heading_id"] in old]
            row["same_level_as_old"] = f"{sum(old[r['heading_id']] == r['level'] for r in both)}/{len(both)}"
            real = [r for r in both if r["level"] > 0 and old[r["heading_id"]] > 0]
            row["same_parent_as_old"] = (f"{sum(old_parents.get(r['heading_id']) == r['parent_text'] for r in real)}"
                                         f"/{len(real)}")
        out.append(row)
    return out


def write_summary(summary: list[dict]) -> None:
    def write(stream):
        if not summary:
            return
        w = csv.DictWriter(stream, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    atomic_write(summary_path(), write)


def run(fetch: bool = True, compare_with: Path | None = None) -> None:
    load_env()
    cfg = settings().get("headings") or {}
    indent_tickers = set(cfg.get("indent_tickers") or [])
    groups: dict[str, list[dict]] = defaultdict(list)
    for group in section_headings():
        groups[group["accession"]].append(group)
    if not groups:
        raise SystemExit("No candidate headings. Run clean and filter first.")
    todo = [f for f in filings() if f["accession"] in groups]
    missing = [f for f in todo if not cached_path(f, htm_dir()).is_file()]
    if fetch and missing and not edgar.user_agent():
        raise SystemExit(f"{len(missing)} filings need their HTML from EDGAR: set SEC_USER_AGENT='Name email' "
                         "in extraction_pipeline/.env (or run with --no-fetch)")
    log(STEP, f"{len(todo)} filings with candidate headings; {len(missing)} HTML files to download"
              + ("" if fetch else " (--no-fetch: skipped)"))

    rows, info = [], {}
    for n, filing in enumerate(todo, 1):
        accession = filing["accession"]
        path = None
        try:
            path = filing_html(filing, htm_dir(), fetch)
        except (OSError, ValueError, urllib.error.URLError, etree.LxmlError) as exc:
            log(STEP, f"{filing['ticker']} {accession}: no HTML ({exc})")
        sections = {}
        if Paths.sections(filing).is_file():
            sections = {s["section_key"]: s for s in read_jsonl(Paths.sections(filing))}
        else:
            log(STEP, f"{filing['ticker']} {accession}: no section export in data/sections/")
        got, info[accession] = filing_rows(groups[accession], sections, path,
                                           filing["ticker"] in indent_tickers)
        rows += got
        if n % 25 == 0:
            log(STEP, f"{n}/{len(todo)} filings")

    backup = levels_path().with_name("heading_levels_llm_backup.jsonl")
    if levels_path().is_file() and any(r.get("source") != SOURCE for r in read_jsonl(levels_path())):
        if not backup.exists():               # the old step 4b (LLM) file: keep it, never overwrite
            backup.write_bytes(levels_path().read_bytes())
            log(STEP, f"kept the old LLM levels as {backup}")
        compare_with = compare_with or levels_path()
    if compare_with is None and backup.exists():
        compare_with = backup
    old, old_parents = None, {}
    if compare_with:
        log(STEP, f"comparing with {compare_with}")
        old = {r["heading_id"]: int(r["level"]) for r in read_jsonl(compare_with)}
        for g in (g for gs in groups.values() for g in gs):
            old_parents.update(paths_from_levels(g["headings"], old))

    write_levels(rows)
    write_review(rows, old, old_parents)
    summary = summarise(rows, info, old, old_parents)
    write_summary(summary)
    reasons = Counter(r["reason"] for r in rows)
    log(STEP, f"{len(rows)} headings -> {levels_path()}")
    log(STEP, ", ".join(f"{k} {reasons[k]}" for k in REASONS if reasons[k]))
    log(STEP, f"review: {review_path()}  summary: {summary_path()}")
    no_html = sum(i["html"] != "ok" for i in info.values())
    if no_html:
        log(STEP, f"{no_html} filings without HTML: their headings are siblings (reason missing_html)")
