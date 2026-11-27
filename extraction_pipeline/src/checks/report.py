"""Review report for the steps before labelling (1-6): which checks fail, what is stale, what
changed since the last report, and which result and check files to open.

Steps are numbered, named and ordered as in the pipeline README's step table (README.md
"2. Steps"; 2b runs before 2 and 4b before 4); each step's section links to its README entry
("4. Step details", anchors step-1 ... step-6b), its code and its files. Keep the two in step when
a step is added, renamed or renumbered.

  python -m src.checks.report           read what the steps wrote, write data/checks/report/
  python -m src.checks.report --against FILE   compare with a saved report.json instead of the last one

Reads files only: no step is re-run, nothing in output/ or interim/ is written, no API, no
database. run_pipeline.py runs it at the end of every run that includes one of these steps.

Checks (section 1 of the report). Every number the report gives can carry a limit in
config/settings.yaml `report.checks` (max or min, a level, and why it matters):
  error   must be fixed before the label run (a filing with no export, no HTML, ...)
  warn    look at it: a rate above what the 18 Nov diagnostics found normal, unmapped
          categories, renames not reviewed, ...
  info    worth knowing; not a problem in itself
Three checks are built in: every stale step is an error; a key number (`report.big_change`)
that moves more than `report.big_change_pct` from the last report is a warning; and the unit
checks list the filings far above the usual rate (outlier_filings). The `report:` block is
read by no step, so changing a limit never makes a step stale.

A step is *stale* when one of its inputs (an earlier step's result, its code under src/, its
config file, or its own block of settings.yaml) is newer than its result: run that step again
before reading its numbers. The changes section compares every number with the report before
(report_previous.json), so a before / after check of a rule change needs no throwaway script
for the counts; read the examples in the check files for the cases themselves.

Outputs (data/checks/report/):
  report.md             read this first; links to every check file (relative to this folder)
  report.json           the numbers, the checks and the stale steps (for comparing runs, and
                        later the dashboard)
  report_previous.json  the report.json before this one
  samples.csv           examples for each unit check (up to SAMPLES_PER_CHECK per company)

Unit checks (step 4, units the LLM would see) are those of the 18 Nov scale-up diagnostics
(src/checks/diagnose.py) that can be read from sentences.jsonl alone. diagnose.py still runs steps 2-4
in memory on every section, also the ones the filter drops; run it for a deeper look.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from ..risk import factors as risk_factors, taxonomy as risk_taxonomy, themes as risk_themes
from ..clean import ITEM_MARKER
from ..common import ROOT, Paths, checks_dir, data_dir, filings, log, read_csv, read_jsonl, settings, write_csv
from .diagnose import BRAND, TABLE_TITLE, md_table
from .entity_inputs import subsidiaries_by_year
from ..segment import is_category_unit
from ..segment.sentences import TERMINAL

STEP = "report"
SRC = ROOT / "src"
CONFIG = ROOT / "config"
SAMPLES_PER_CHECK = 5          # per check and company
OUTLIER_FACTOR, OUTLIER_MIN = 3, 5   # a filing at 3x the median rate of a check, with 5+ cases
LEVELS = ["error", "warn", "info"]
ICON = {"error": "❌", "warn": "⚠️", "info": "ℹ️", "pass": "✅", "not run": "–"}


def out_dir() -> Path:
    return checks_dir("report")


# ---------------------------------------------------------------- the steps ----

def per_filing(kind) -> list[Path]:
    """The per-filing files of one kind (Paths.sections or Paths.cleaned) that exist."""
    return [p for p in (kind(f) for f in filings()) if p.exists()]


# Each step, in the order of the README's step table (README.md "2. Steps"): its number and name
# there, its `--steps` name (the id), its code, whether it calls the LLM, and the anchor of its
# entry in README "4. Step details". Then what it writes (results), what it reads (inputs: earlier
# results, its code, its config files), the blocks of config/settings.yaml it reads (settings),
# and the check files to open with what to look at. Paths are functions, read late.
STEPS = [
    {"id": "fetch", "num": "1", "label": "Fetch", "code": ["fetch.py"], "llm": "no",
     "results": lambda: per_filing(Paths.sections),
     "inputs": lambda: [],
     "checks": []},
    {"id": "clean", "num": "2", "label": "Clean", "code": ["clean.py"], "llm": "no",
     "results": lambda: per_filing(Paths.cleaned),
     "inputs": lambda: per_filing(Paths.sections) + [Paths.page_break_decisions(), SRC / "clean.py"],
     "settings": ["clean"],
     "checks": []},
    {"id": "page_breaks", "num": "2b", "label": "Page breaks", "code": ["page_breaks.py"],
     "llm": "paid, run on purpose",
     "results": lambda: [Paths.page_break_decisions()],
     "inputs": lambda: [],            # never re-run by a default run: not reported stale
     "checks": [(Paths.page_break_candidates, "gray boundaries sent to the model, with both sides"),
                (Paths.page_break_decisions, "the model's answers (`same_sentence`)")]},
    {"id": "filter", "num": "3", "label": "Filter", "code": ["filter_sections.py"], "llm": "no",
     "results": lambda: [Paths.manifest()],
     "inputs": lambda: per_filing(Paths.cleaned) + [CONFIG / "sections.yaml", SRC / "filter_sections.py"],
     "checks": [(Paths.manifest, "`review` rows; add overrides in config/sections.yaml"),
                (lambda: CONFIG / "sections.yaml", "which section types are kept, dropped or reviewed")]},
    {"id": "headings", "num": "4b", "label": "Heading levels", "code": ["headings/"], "llm": "no",
     "results": lambda: [Paths.heading_levels(), Paths.lead_sentences(), Paths.html_tables()],
     "inputs": lambda: [Paths.manifest(), SRC / "headings"] + per_filing(Paths.cleaned),
     "settings": ["headings", "segment"],
     "checks": [(Paths.heading_summary, "companies with a low `found_in_html_pct`, `filings_no_html` > 0, "
                                        "`flag_risk_sentence`, `flag_before_table`, `level_4plus`"),
                (Paths.heading_review, "the headings behind those counts (filter on `flags`, `reason`; "
                                       "`open_in_edgar` opens the filing)"),
                (Paths.heading_html_diff, "`html_only`: HTML headings step 4 missed; `step4_plain`: step-4 "
                                          "headings the HTML shows as plain text")]},
    {"id": "segment", "num": "4", "label": "Segment", "code": ["segment/"], "llm": "no",
     "results": lambda: [Paths.sentences()],
     "inputs": lambda: [Paths.manifest(), Paths.heading_levels(), Paths.lead_sentences(), Paths.html_tables(),
                        SRC / "segment"] + per_filing(Paths.cleaned),
     "settings": ["segment", "headings"],
     "checks": [(lambda: out_dir() / "samples.csv", "examples of the unit checks (filter on `check`)"),
                (Paths.lead_summary, "per company: leads by source, units under a lead"),
                (Paths.lead_review, "one row per lead; `rule_heading` rows the HTML does not confirm"),
                (lambda: checks_dir("unit_repeats"), "**repeats** (a check, not a step): repeats across "
                                                     "filings before labelling, written after step 4 by "
                                                     "`src/checks/unit_repeats.py`")]},
    {"id": "risk_factors", "num": "5", "label": "Risk factors", "code": ["risk/factors.py"], "llm": "no",
     "results": lambda: [Paths.risk_categories(), Paths.risk_factors(), Paths.risk_assignments()],
     "inputs": lambda: [Paths.sentences(), risk_taxonomy.folder(), SRC / "risk" / "factors.py",
                        SRC / "risk" / "taxonomy.py"],
     "computed": "risk_categories.csv not written yet; the numbers here are computed from sentences.jsonl",
     "checks": [(risk_taxonomy.category_map_path, "add the unmapped category texts listed in this section"),
                (risk_factors.checks_path, "missing or doubtful categories: filter on `check` (year_gap, "
                                           "summary_mismatch, risk_heading_below_1, suspect_category)")]},
    {"id": "risk_themes", "num": "5b", "label": "Risk themes", "code": ["risk/themes.py"],
     "llm": "paid, sends only when named",
     "results": lambda: [Paths.risk_themes()],
     "inputs": lambda: [Paths.risk_factors(), risk_taxonomy.folder(), SRC / "risk" / "themes.py"],
     "checks": [(risk_themes.factors_path, "one row per risk factor; filter on `review` (not_classified, unknown, "
                                           "differs_from_reference, overrules_heading)"),
                (lambda: checks_dir("risk_themes") / "requests.csv",
                 "one row per risk factor: its request, `reply_saved`, `truncated`")]},
    {"id": "subsidiaries", "num": "6a", "label": "Subsidiaries", "code": ["entities/subsidiaries.py", "entities/ex21/"],
     "llm": "no",
     "results": lambda: [Paths.subsidiaries(), Paths.subsidiary_catalog()],
     "inputs": lambda: [CONFIG / "filings.yaml", CONFIG / "subsidiary_aliases.yaml",
                        SRC / "entities" / "subsidiaries.py", SRC / "entities" / "ex21"],
     "checks": [(Paths.subsidiary_qa, "CHECK rows (`status` not OK); `duplicate_names`, `unmapped_jurisdiction`"),
                (Paths.subsidiary_renames, "rows with an empty `review`: same entity renamed, or two entities? "
                                           "Reviewed pairs go to config/subsidiary_aliases.yaml"),
                (lambda: checks_dir("entity_inputs"), "`python -m src.checks.entity_inputs`: big year-to-year "
                                                      "changes, repeats, companies without segments")]},
    {"id": "entity_catalog", "num": "6b", "label": "Entity catalog", "code": ["entities/catalog.py"], "llm": "no",
     "results": lambda: [Paths.entity_catalog()],
     "inputs": lambda: [Paths.subsidiary_catalog(), CONFIG / "entity_linking.yaml", SRC / "entities" / "catalog.py"],
     "settings": ["entity_catalog"],
     "checks": [(Paths.entity_catalog, "entries by `entity_type`; segments come from the fact store")]},
]
for _s in STEPS:
    _s["name"] = f"{_s['num']} {_s['label']}"
STEP_IDS = [s["id"] for s in STEPS]
STEP_NAME = {s["id"]: s["name"] for s in STEPS}


# ---------------------------------------------------------------- freshness ----

def newest(paths: list[Path]) -> tuple[float, Path | None]:
    """Latest modification time among the files (and the files under the folders) given."""
    best, which = 0.0, None
    for p in paths:
        files = [p] if p.is_file() else ([q for q in p.rglob("*") if q.is_file() and q.suffix != ".pyc"]
                                         if p.is_dir() else [])
        for q in files:
            t = q.stat().st_mtime
            if t > best:
                best, which = t, q
    return best, which


def settings_hash(keys: list[str]) -> str:
    blocks = {k: settings().get(k) for k in keys}
    return hashlib.sha256(json.dumps(blocks, sort_keys=True, default=str).encode()).hexdigest()[:16]


def freshness(step: dict, last: dict | None) -> dict:
    """status: missing (no result), stale (an input is newer than the oldest result, or the
    step's blocks of settings.yaml changed since it ran), or ok.

    settings.yaml holds every step's settings, so its time alone would make every step stale.
    The report keeps, per step, a hash of the blocks the step reads, taken when the step's result
    is newer than settings.yaml; a different hash now means the step ran with other settings."""
    results = [p for p in step["results"]() if p.exists()]
    if not results:
        return {"status": "missing", "written": None, "reason": ""}
    written = min(p.stat().st_mtime for p in results)
    t, which = newest(step["inputs"]())
    out = {"status": "ok", "written": stamp(written), "reason": ""}
    if which is not None and t > written + 1:
        out.update(status="stale", reason=f"{rel(which)} is newer ({stamp(t)})")
    keys = step.get("settings")
    if keys:
        now = settings_hash(keys)
        seen = ((last or {}).get("fresh", {}).get(step["id"]) or {}).get("settings_hash")
        if written > (CONFIG / "settings.yaml").stat().st_mtime:
            seen = now                    # it ran after the last edit of settings.yaml
        out["settings_hash"] = seen
        blocks = ", ".join(f"`{k}:`" for k in keys)
        if out["status"] == "ok" and seen is None:
            out.update(status="stale", reason="config/settings.yaml was edited after this step ran "
                                              "(no earlier report to tell which block)")
        elif out["status"] == "ok" and seen != now:
            out.update(status="stale", reason=f"config/settings.yaml {blocks} changed since this step ran")
    return out


def stamp(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def rel(p: Path) -> str:
    for base, name in ((data_dir(), "data"), (ROOT, "")):
        try:
            r = p.resolve().relative_to(base)
            return f"{name}/{r}" if name else str(r)
        except ValueError:
            continue
    return str(p)


def link(p: Path) -> str:
    """A markdown link to a file or folder, relative to report.md, so it opens from the report."""
    href = quote(os.path.relpath(p.resolve(), out_dir().resolve()).replace(os.sep, "/"))
    return f"[`{rel(p)}`]({href}{'/' if p.is_dir() else ''})"


README = ROOT / "README.md"


def readme_link(anchor_id: str, text: str) -> str:
    """A link to a section of the pipeline README (README.md), relative to report.md."""
    href = quote(os.path.relpath(README.resolve(), out_dir().resolve()).replace(os.sep, "/"))
    return f"[{text}]({href}#{anchor_id})"


def readme_anchor(step: dict) -> str:
    """The anchor of the step's entry in README "4. Step details" (`<a id="step-4b">`)."""
    return f"step-{step['num']}"


def summarised(paths: list[Path]) -> list[Path]:
    """Results to list: a step with one file per filing is shown by its folder."""
    return sorted({p.parent for p in paths}) if len(paths) > 3 else paths


# ---------------------------------------------------------------- numbers per step ----
# Each returns (metrics, notes, lines): numbers to compare between runs and to check against
# the limits, details behind them, and extra markdown (tables) for the step's section.

def pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


def fetch_numbers() -> tuple[dict, list, list]:
    fs, all_fs = filings(), filings(include_on_hold=True)
    missing = [f for f in fs if not Paths.sections(f).exists()]
    m = {"filings_passed": len(fs), "filings_on_hold": len(all_fs) - len(fs),
         "10-K": sum(f["form"] == "10-K" for f in fs), "10-Q": sum(f["form"] == "10-Q" for f in fs),
         "exports": len(fs) - len(missing), "exports_missing": len(missing)}
    notes = [f"no export for {len(missing)} passed filings: "
             + ", ".join(f"{f['ticker']} {f['form']} {f['accession']}" for f in missing[:10])] if missing else []
    return m, notes, []


def subsidiaries_numbers() -> tuple[dict, list, list]:
    if not Paths.subsidiaries().exists():
        return {}, [], []
    rows = list(read_jsonl(Paths.subsidiaries()))
    qa = read_csv(Paths.subsidiary_qa()) if Paths.subsidiary_qa().exists() else []
    renames = read_csv(Paths.subsidiary_renames()) if Paths.subsidiary_renames().exists() else []
    catalog = json.loads(Paths.subsidiary_catalog().read_text(encoding="utf-8")) \
        if Paths.subsidiary_catalog().exists() else []
    ten_ks = {f["accession"]: f for f in filings() if f["form"] == "10-K"}
    not_ok = [q for q in qa if q["status"] != "OK"]
    unchecked = [f for a, f in ten_ks.items() if a not in {q["accession"] for q in qa}]
    big = [r for r in subsidiaries_by_year(rows) if r["flag"]]
    m = {"10k_in_filings": len(ten_ks), "10k_checked": len(qa), "10k_unchecked": len(unchecked),
         "10k_not_ok": len(not_ok), "rows": len(rows), "distinct_subsidiaries": len(catalog),
         "renames": len(renames), "renames_not_reviewed": sum(1 for r in renames if not r["review"]),
         "big_year_changes": len(big)}
    notes = []
    if not_ok:
        notes.append("10-Ks not OK in subsidiary_qa.csv: "
                     + ", ".join(f"{q['ticker']} {q['accession']} ({q['status']})" for q in not_ok[:8]))
    if unchecked:
        by_t = Counter(f["ticker"] for f in unchecked)
        notes.append("10-Ks in filings.yaml not in subsidiary_qa.csv (run step 6a): "
                     + ", ".join(f"{t} ({n})" for t, n in sorted(by_t.items())))
    lines = []
    if big:
        lines = ["Row count of a 10-K moved by more than half from the 10-K before:", "",
                 md_table(["ticker", "period", "rows", "10-K before", "change"],
                          [[r["ticker"], r["reporting_period_end"], r["rows"], r["rows_10k_before"], r["change"]]
                           for r in big]), ""]
    return m, notes, lines


def entity_catalog_numbers() -> tuple[dict, list, list]:
    if not Paths.entity_catalog().exists():
        return {}, [], []
    catalog = json.loads(Paths.entity_catalog().read_text(encoding="utf-8"))
    kinds = Counter(e["entity_type"] for e in catalog)
    ticker = {str(f["cik"]).zfill(10): f["ticker"] for f in filings()}
    with_segments = {e["issuer_cik"] for e in catalog if e["entity_type"] == "segment"}
    with_subs = {e["issuer_cik"] for e in catalog if e["entity_type"] == "subsidiary"}
    no_seg = sorted({t for c, t in ticker.items() if c not in with_segments})
    no_sub = sorted({t for c, t in ticker.items() if c not in with_subs})
    m = {f"entries_{k}": v for k, v in sorted(kinds.items())}
    m["companies_without_segments"], m["companies_without_subsidiaries"] = len(no_seg), len(no_sub)
    notes = []
    if no_seg:
        notes.append(f"companies with no segment in the catalog: {', '.join(no_seg)}"
                     + (" (no segment at all: was step 6b run without a fact store, `--facts` or "
                        "`PIPELINE_FACT_STORE`?)" if not with_segments else ""))
    if no_sub:
        notes.append(f"companies with no subsidiary in the catalog: {', '.join(no_sub)}")
    return m, notes, []


def page_breaks_numbers() -> tuple[dict, list, list]:
    m = {}
    if Paths.page_break_candidates().exists():
        m["gray_boundaries"] = sum(1 for _ in read_jsonl(Paths.page_break_candidates()))
    if Paths.page_break_decisions().exists():
        rows = list(read_jsonl(Paths.page_break_decisions()))
        m["decisions"] = len(rows)
        m["joined"] = sum(bool(r.get("same_sentence")) for r in rows)
        if "gray_boundaries" in m:
            m["not_answered"] = max(m["gray_boundaries"] - m["decisions"], 0)
    ledger = Paths.step_usage("page_breaks")
    if ledger.exists():
        m["cost_usd"] = round(sum(r.get("estimated_cost_usd") or 0 for r in read_jsonl(ledger)), 4)
    return m, [], []


def clean_numbers() -> tuple[dict, list, list]:
    removed, m = Counter(), Counter()
    exported = [f for f in filings() if Paths.sections(f).exists()]
    not_cleaned = [f for f in exported if not Paths.cleaned(f).exists()]
    empty = []
    for f in exported:
        if not Paths.cleaned(f).exists():
            continue
        m["filings"] += 1
        paragraphs = 0
        for sec in read_jsonl(Paths.cleaned(f)):
            m["sections"] += 1
            paragraphs += len(sec["paragraphs"])
            m["joined"] += sec.get("joined", 0)
            removed.update(r["rule"] for r in sec.get("removed", []))
        m["paragraphs"] += paragraphs
        if not paragraphs:
            empty.append(f)
    m["filings_not_cleaned"], m["filings_without_paragraphs"] = len(not_cleaned), len(empty)
    m["removed"] = sum(removed.values())
    m["removed_pct"] = pct(m["removed"], m["removed"] + m["paragraphs"])
    m.update({f"removed_{k}": v for k, v in removed.items()})
    notes = []
    if not_cleaned:
        notes.append("exported filings with no cleaned file (run step 2): "
                     + ", ".join(f"{f['ticker']} {f['accession']}" for f in not_cleaned[:8]))
    if empty:
        notes.append("cleaned filings with no paragraph: " + ", ".join(f"{f['ticker']} {f['accession']}" for f in empty[:8]))
    return dict(m), notes, []


def filter_numbers() -> tuple[dict, list, list]:
    if not Paths.manifest().exists():
        return {}, [], []
    rows = read_csv(Paths.manifest())
    m = {f"sections_{k}": v for k, v in Counter(r["decision"] for r in rows).items()}
    kept = defaultdict(set)
    for r in rows:
        if r["decision"] == "keep":
            kept[r["accession_number"]].add(r["section_type"])
    m.update({f"kept_{k}": v for k, v in Counter(r["section_type"] for r in rows if r["decision"] == "keep").items()})
    expected = {"10-K": {"risk_factors", "mda", "business"}, "10-Q": {"mda"}}
    gaps = []
    for f in filings():
        if not Paths.sections(f).exists():
            continue
        lacking = expected.get(f["form"], set()) - kept[f["accession"]]
        if lacking:
            gaps.append(f"{f['ticker']} {f['form']} {f['accession']} ({', '.join(sorted(lacking))})")
    m["filings_lacking_a_section"] = len(gaps)
    notes = ["filings without a section kept for their form: " + "; ".join(gaps[:8])] if gaps else []

    def by_type(decision: str) -> list[list]:
        """One row per section type: sections, filings, the titles the filings use (most common first)."""
        group = defaultdict(list)
        for r in rows:
            if r["decision"] == decision:
                group[r["section_type"]].append(r)
        out = []
        for t, rs in sorted(group.items(), key=lambda kv: -len(kv[1])):
            forms = Counter(r["form"] for r in rs)
            titles = Counter(" ".join((r["original_title"] or "").split()) for r in rs).most_common(4)
            more = len({r["original_title"] for r in rs}) - len(titles)
            out.append([f"`{t}`", len(rs), ", ".join(f"{k} {v}" for k, v in sorted(forms.items())),
                        "; ".join(f"{x} ({n})" for x, n in titles) + (f"; +{more} more" if more > 0 else ""),
                        rs[0]["reason"]])
        return out

    head = ["section_type", "sections", "by form", "titles in the filings (count)", "reason (sections.yaml)"]
    lines = ["**Kept** (sent on to step 4):", "", md_table(head, by_type("keep")), ""]
    review = by_type("review")
    if review:
        lines += ["**Review** (kept out until decided in `config/sections.yaml`):", "", md_table(head, review), ""]
    return m, notes, lines


def headings_numbers() -> tuple[dict, list, list]:
    if not Paths.heading_levels().exists():
        return {}, [], []
    levels = list(read_jsonl(Paths.heading_levels()))
    lv = Counter(min(int(r["level"]), 4) for r in levels)
    reasons = Counter(r["reason"] for r in levels)
    real = sum(v for k, v in lv.items() if k >= 1)
    m = {"headings": len(levels), "sentence_level": lv.get(-1, 0), "not_a_heading_level_0": lv.get(0, 0),
         "level_1": lv.get(1, 0), "level_2": lv.get(2, 0), "level_3": lv.get(3, 0), "level_4plus": lv.get(4, 0),
         "level_4plus_pct": pct(lv.get(4, 0), real)}
    m.update({f"reason_{k}": v for k, v in reasons.items()})
    html = no_html = 0
    if Paths.lead_sentences().exists():
        for r in read_jsonl(Paths.lead_sentences()):
            html += bool(r["html"])
            no_html += not r["html"]
            for x in r["leads"]:
                m[f"leads_{x['source']}"] = m.get(f"leads_{x['source']}", 0) + 1
            m["runin_headings"] = m.get("runin_headings", 0) + len(r.get("runin_headings", []))
    m["filings_with_html"], m["filings_without_html"] = html, no_html
    if Paths.html_tables().exists():
        kinds = Counter(x["kind"] for r in read_jsonl(Paths.html_tables()) for x in r["rows"])
        m.update({f"table_rows_{k}": v for k, v in kinds.items()})
    notes, lines = [], []
    from ..headings.step import read_output
    if Paths.heading_html_diff().exists():
        diff = read_output(Paths.heading_html_diff())
        if "kind" in diff:
            m.update({f"html_diff_{k}": int(v) for k, v in diff["kind"].value_counts().items()})
    if Paths.heading_summary().exists():
        summary = read_output(Paths.heading_summary())
        found = summary[summary["filings_no_html"] < summary["filings"]]["found_in_html_pct"].dropna()
        if len(found):
            m["min_found_in_html_pct"] = round(float(found.min()), 1)
            low = summary.sort_values("found_in_html_pct").head(5)
            lines = ["Companies with the lowest share of headings found in the HTML:", "",
                     md_table(["ticker", "headings", "found in HTML %", "level 4+", "flag risk_sentence",
                               "flag before_table"],
                              [[r.ticker, r.headings, r.found_in_html_pct, r.level_4plus, r.flag_risk_sentence,
                                r.flag_before_table] for r in low.itertuples()]), ""]
    if no_html:
        notes.append(f"{no_html} filings without HTML: their headings are all siblings (run step 4b with "
                     "SEC_USER_AGENT set)")
    if m["sentence_level"]:
        notes.append(f"{m['sentence_level']} headings at level -1 (may be sentences): flagged for review only "
                     "(25 Nov); filter heading_review.csv on `level` -1")
    return m, notes, lines


def segment_numbers(units: list[dict]) -> tuple[dict, list, list]:
    if not units:
        return {}, [], []
    to_llm = [u for u in units if u["prefilter"] is None]
    versions = sorted({str(u.get("segmentation_version")) for u in units})
    with_units = {u["accession_number"] for u in units}
    with_llm = {u["accession_number"] for u in to_llm}
    exported = [f for f in filings() if Paths.sections(f).exists()]
    no_units = [f for f in exported if f["accession"] not in with_units]
    no_llm = [f for f in exported if f["accession"] in with_units and f["accession"] not in with_llm]
    m = {"units": len(units), "to_llm": len(to_llm), "to_llm_pct": pct(len(to_llm), len(units)),
         "segmentation_versions": len(versions),
         "filings_without_units": len(no_units), "filings_without_units_to_llm": len(no_llm),
         "list_groups": sum(u["role"] == "list_group" for u in units),
         "split_list_items": sum("list_intro_id" in u for u in units),
         "units_under_a_lead": sum(bool(u.get("lead_unit_id")) for u in units),
         "units_in_html_tables": sum(bool(u.get("html_table")) for u in units)}
    m.update({f"prefilter_{k}": v for k, v in Counter(u["prefilter"] for u in units if u["prefilter"]).items()})
    m.update({f"role_{k}": v for k, v in Counter(u["role"] for u in units).items()})
    m.update({f"lead_source_{k}": v for k, v in Counter(u.get("lead_source") for u in units
                                                       if u.get("lead_source")).items()})
    by_type = defaultdict(Counter)
    for u in units:
        by_type[u["section_type"]]["units"] += 1
        by_type[u["section_type"]]["to_llm"] += u["prefilter"] is None
    for t, c in by_type.items():
        m[f"to_llm_{t}"] = c["to_llm"]
    notes = []
    if len(versions) > 1:
        notes.append(f"sentences.jsonl mixes segmentation versions: {versions}")
    if no_units:
        notes.append("exported filings with no unit (step 3 kept nothing, or step 4 did not run on them): "
                     + ", ".join(f"{f['ticker']} {f['accession']}" for f in no_units[:8]))
    lines = [f"segmentation_version: `{', '.join(versions)}`", "",
             md_table(["section_type", "units", "to the LLM"],
                      [[t, f"{c['units']:,}", f"{c['to_llm']:,}"] for t, c in sorted(by_type.items())]), ""]
    return m, notes, lines


def risk_numbers(units: list[dict]) -> tuple[dict, list, list]:
    """Step 5: categories, heading groups, risk factors (computed from sentences.jsonl)."""
    if not units:
        return {}, [], []
    categories, assignments, factors = risk_factors.build(units)
    status = Counter(c["map_status"] for c in categories)
    unmapped = Counter((c["ticker"], c["category_text"]) for c in categories if c["map_status"] == "unmapped")
    with_group = sum(bool(a["heading_risk_group"]) for a in assignments)
    in_factor = sum(bool(a["risk_factor_id"]) for a in assignments)
    m = {"categories": len(categories), **{f"categories_{k}": status[k] for k in risk_taxonomy.MAP_STATUSES},
         "unmapped": status["unmapped"], "unmapped_texts": len(unmapped),
         "item_1a_units": len(assignments), "item_1a_units_with_group": with_group,
         "item_1a_with_group_pct": pct(with_group, len(assignments)),
         "item_1a_in_risk_factor_pct": pct(in_factor, len(assignments)),
         "risk_factors": len(factors)}
    checks = Counter(r["check"] for r in risk_factors.category_checks(units, categories))
    reasons = risk_factors.no_group_reasons(units, categories, assignments)
    m.update({f"check_{k}": checks[k] for k, _ in risk_factors.CATEGORY_CHECKS})
    m.update({f"no_group_{k}": reasons[k] for k in risk_factors.NO_GROUP_REASONS})
    lines = ["**Why Item 1A units have no heading group**", "",
             md_table(["reason", "units", "% of Item 1A units"],
                      [[k, f"{reasons[k]:,}", pct(reasons[k], len(assignments))]
                       for k in risk_factors.NO_GROUP_REASONS]), "",
             "**Are the categories complete?** (cases in `category_checks.csv`)", "",
             md_table(["check", "cases", "what it means"],
                      [[k, checks[k], why] for k, why in risk_factors.CATEGORY_CHECKS]), ""]
    if unmapped:
        lines += ["Unmapped category texts (add them to `config/risk_taxonomy/risk_category_map.csv`):", "",
                  md_table(["ticker", "category text", "filings"],
                           [[t, x, n] for (t, x), n in sorted(unmapped.items())]), ""]
    return m, [], lines


def risk_themes_numbers(units: list[dict]) -> tuple[dict, list, list]:
    """Step 5b: requests, answers, cost, and the theme status of every Item 1A unit (as step 9 will
    put it on the Claims: step 5's risk factors with the saved answers that still fit)."""
    m = {}
    requests = checks_dir("risk_themes") / "requests.csv"
    if requests.exists():
        rows = list(csv.DictReader(open(requests, encoding="utf-8")))
        m["requests"] = len(rows)
        m["distinct_requests"] = len({r["request_hash"] for r in rows})
        m["truncated"] = sum(r["truncated"] == "True" for r in rows)
    results = risk_themes.load_results()
    if Paths.risk_themes().exists():
        m["answered"] = len(results)
    ledger = Paths.step_usage("risk_themes")
    if ledger.exists():
        m["cost_usd"] = round(sum(r.get("estimated_cost_usd") or 0 for r in read_jsonl(ledger)), 4)
    if not units:
        return m, [], []
    _, assignments, factors = risk_factors.build(units)
    risk_themes.apply(assignments, factors, results)
    review = Counter(x for r in risk_themes.review_rows(factors, assignments, results)
                     for x in r["review"].split(";") if x)
    themed = Counter(a["risk_theme_status"] for a in assignments)
    m.update({f"risk_factors_{k}": review[k] for k in ("not_classified", "unknown", "differs_from_reference",
                                                       "overrules_heading")})
    m.update({f"units_{k}": themed[k] for k in risk_themes.THEME_STATUSES})
    primary = Counter(r["primary_theme"] for r in results.values() if r["primary_theme"])
    lines = ["**Item 1A units by risk theme status**", "",
             md_table(["status", "units", "% of Item 1A units"],
                      [[k, f"{themed[k]:,}", pct(themed[k], len(assignments))] for k in risk_themes.THEME_STATUSES]),
             ""]
    if primary:
        lines += ["**Primary themes** (risk factors)", "",
                  md_table(["theme", "risk factors"], [[t, n] for t, n in primary.most_common()]), ""]
    return m, [], lines


# ---------------------------------------------------------------- unit checks ----

UNIT_CHECKS = [
    ("lowercase_start", "Sent to the LLM, starts lower case, not a list item: a missed page-break join or a bad split."),
    ("no_end_punct", "Sent to the LLM, no end punctuation, no ':' or '|': a cut sentence or a missed heading."),
    ("very_long", "Sent to the LLM, over 1,500 characters: a missed split (often a ';' list in one sentence)."),
    ("single_pipe", "Sent to the LLM with one '|': a two-column table row."),
    ("long_heading", "Heading over 100 characters (table titles left out): often a sentence, never sent to the LLM."),
    ("risk_section_no_category", "Item 1A with 20+ units and no category heading: its Claims get no heading group."),
]
RATE_CHECKS = ["lowercase_start", "no_end_punct", "very_long", "single_pipe"]   # per 1,000 units to the LLM


def unit_checks(units: list[dict]) -> tuple[dict, list, list, list[dict]]:
    """Counts per check (and per 1,000 units to the LLM), filings far above a check's usual rate,
    and samples (up to SAMPLES_PER_CHECK per check and company)."""
    hits: dict[str, list[dict]] = defaultdict(list)
    per_filing_n: dict[str, Counter] = defaultdict(Counter)
    sections: dict[tuple, list[dict]] = defaultdict(list)
    tickers = {}
    for u in units:
        tickers[u["accession_number"]] = u["ticker"]
        per_filing_n[u["accession_number"]]["units"] += 1
        sections[(u["accession_number"], u["section_key"])].append(u)
        text, to_llm = u["text"], u["prefilter"] is None
        in_list = "list_intro_id" in u or u["role"] == "list_group"
        found = []
        if to_llm and not in_list and text[:1].islower() and not BRAND.match(text) and not ITEM_MARKER.match(text):
            found.append("lowercase_start")
        if (to_llm and not in_list and not TERMINAL.search(text) and not text.endswith(":")
                and not u.get("rule_heading") and "|" not in text):
            found.append("no_end_punct")
        if to_llm and len(text) > 1500:
            found.append("very_long")
        if to_llm and text.count("|") == 1:
            found.append("single_pipe")
        if u["role"] == "heading" and len(text) > 100 and not TABLE_TITLE.match(text):
            found.append("long_heading")
        for c in found:
            hits[c].append(u)
            per_filing_n[u["accession_number"]][c] += 1
    for (acc, _), us in sections.items():
        if us[0]["section_type"] == "risk_factors" and len(us) >= 20 and not any(is_category_unit(u) for u in us):
            hits["risk_section_no_category"].append(us[0])
            per_filing_n[acc]["risk_section_no_category"] += 1

    random.seed(0)
    samples = []
    for c, _ in UNIT_CHECKS:
        by_ticker = defaultdict(list)
        for u in hits[c]:
            by_ticker[u["ticker"]].append(u)
        for t, us in sorted(by_ticker.items()):
            for u in random.sample(us, min(SAMPLES_PER_CHECK, len(us))):
                samples.append({"check": c, "ticker": t, "form": u["form"], "accession_number": u["accession_number"],
                                "section_key": u["section_key"], "unit_id": u["unit_id"], "role": u["role"],
                                "prefilter": u["prefilter"], "text": u["text"][:400]})

    # Filings far above the usual rate of a check: usually one company's format.
    outliers = []
    for c, _ in UNIT_CHECKS[:-1]:
        rates = [(n[c] / n["units"], acc, n[c]) for acc, n in per_filing_n.items() if n["units"]]
        if not rates:
            continue
        med = statistics.median(r for r, _, _ in rates)
        for r, acc, k in rates:
            if k >= OUTLIER_MIN and r >= OUTLIER_FACTOR * max(med, 1e-9):
                outliers.append((c, tickers[acc], acc, k, r, med))
    to_llm = sum(u["prefilter"] is None for u in units)
    m = {c: len(hits[c]) for c, _ in UNIT_CHECKS}
    m.update({f"{c}_per_1k": round(1000 * m[c] / to_llm, 2) if to_llm else 0.0 for c in RATE_CHECKS})
    m["outlier_filings"] = len({acc for _, _, acc, *_ in outliers})
    lines = [md_table(["check", "what it means", "units", "per 1k to the LLM"],
                      [[f"`{c}`", meaning, f"{m[c]:,}", m.get(f"{c}_per_1k", "")] for c, meaning in UNIT_CHECKS]), ""]
    notes = []
    if outliers:
        by = defaultdict(Counter)
        for c, t, *_ in outliers:
            by[c][t] += 1
        notes.append("filings at 3x a check's median rate, by company (filings): "
                     + "; ".join(f"`{c}` " + ", ".join(f"{t} ({n})" for t, n in by[c].most_common())
                                 for c in by))
        lines += [f"Filings at {OUTLIER_FACTOR}x or more the median rate of a check ({OUTLIER_MIN}+ cases):", "",
                  md_table(["check", "ticker", "accession", "units", "per 1k units", "median per 1k"],
                           [[f"`{c}`", t, acc, k, f"{1000 * r:.1f}", f"{1000 * med:.1f}"]
                            for c, t, acc, k, r, med in sorted(outliers, key=lambda x: (x[0], x[1], x[2]))]), ""]
    return m, notes, lines, samples


def repeats_numbers() -> dict:
    """Pre-label repeats, from src/checks/unit_repeats.py (summary.csv), when it has run."""
    path = checks_dir("unit_repeats") / "summary.csv"
    if not path.exists():
        return {}
    import csv
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    m = Counter()
    for r in rows:
        m[f"{r['level']}_repeated_units"] += int(r["repeated_units"])
        m[f"{r['level']}_reusable_units"] += int(r["reusable_units"])
    return dict(m)


# ---------------------------------------------------------------- changes and checks ----

def flat(metrics: dict) -> dict[str, float]:
    return {f"{s}.{k}": v for s, ms in metrics.items() for k, v in ms.items() if isinstance(v, (int, float))}


def step_order(key: str) -> int:
    s = key.split(".")[0]
    return STEP_IDS.index(s) if s in STEP_IDS else 99


def changes(now: dict, before: dict) -> list[list]:
    a, b = flat(before.get("metrics", {})), flat(now["metrics"])
    out = []
    for k in sorted(set(a) | set(b), key=lambda k: (step_order(k), k)):
        x, y = a.get(k), b.get(k)
        if x == y:
            continue
        if x is None or y is None:
            out.append([f"`{k}`", "-" if x is None else f"{x:,}", "-" if y is None else f"{y:,}", ""])
        else:
            change = f"{100 * (y - x) / x:+.1f}%" if x else ""
            out.append([f"`{k}`", f"{x:,}", f"{y:,}", change])
    return out


def evaluate(report: dict, before: dict | None) -> list[dict]:
    """Every check: built in (stale steps, big changes) and the limits of settings.yaml report.checks."""
    cfg = settings().get("report") or {}
    numbers = flat(report["metrics"])
    out = []
    for s in STEPS:
        f = report["fresh"][s["id"]]
        if f["status"] == "stale":
            out.append({"step": s["id"], "number": "stale", "value": f["reason"], "limit": "run the step again",
                        "level": "error", "status": "error", "why": "the numbers below are from before a change"})
    if before:
        old = flat(before.get("metrics", {}))
        limit = cfg.get("big_change_pct", 5)
        for k in cfg.get("big_change") or []:
            if k in numbers and old.get(k):
                moved = 100 * (numbers[k] - old[k]) / old[k]
                out.append({"step": k.split(".")[0], "number": k, "value": f"{old[k]:,} → {numbers[k]:,} ({moved:+.1f}%)",
                            "limit": f"±{limit}%", "level": "warn",
                            "status": "warn" if abs(moved) > limit else "pass",
                            "why": "a key number moved more than usual since the last report: check it was meant"})
    for k, rule in (cfg.get("checks") or {}).items():
        level, value = rule.get("level", "warn"), numbers.get(k)
        row = {"step": k.split(".")[0], "number": k, "value": value, "level": level, "why": rule.get("why", "")}
        if "max" in rule:
            row["limit"], bad = f"≤ {rule['max']}", value is not None and value > rule["max"]
        else:
            row["limit"], bad = f"≥ {rule.get('min')}", value is not None and value < rule.get("min", 0)
        row["status"] = "not run" if value is None else level if bad else "pass"
        out.append(row)
    rank = {"error": 0, "warn": 1, "info": 2, "pass": 3, "not run": 4}
    return sorted(out, key=lambda r: (rank[r["status"]], step_order(r["step"] + "."), r["number"]))


# ---------------------------------------------------------------- report ----

def build(last: dict | None, before: dict | None) -> tuple[dict, dict, list[dict]]:
    units = list(read_jsonl(Paths.sentences())) if Paths.sentences().exists() else []
    numbers = {
        "fetch": fetch_numbers(), "subsidiaries": subsidiaries_numbers(),
        "entity_catalog": entity_catalog_numbers(), "page_breaks": page_breaks_numbers(),
        "clean": clean_numbers(), "filter": filter_numbers(), "headings": headings_numbers(),
        "segment": segment_numbers(units), "risk_factors": risk_numbers(units),
        "risk_themes": risk_themes_numbers(units),
    }
    checks_m, checks_notes, checks_lines, samples = unit_checks(units) if units else ({}, [], [], [])
    seg_m, seg_notes, seg_lines = numbers["segment"]
    numbers["segment"] = ({**seg_m, **{f"check_{k}": v for k, v in checks_m.items()}}, seg_notes + checks_notes,
                          seg_lines + (["**Unit checks**", ""] + checks_lines if checks_lines else []))
    rep = repeats_numbers()
    if rep and units:
        seg = numbers["segment"]
        levels = sorted({k.rsplit("_", 2)[0] for k in rep})
        table = md_table(["level", "repeated units", "reusable units (step 7 reuses their label)"],
                         [[f"`{lv}`", f"{rep.get(f'{lv}_repeated_units', 0):,}",
                           f"{rep.get(f'{lv}_reusable_units', 0):,}"] for lv in levels])
        numbers["segment"] = ({**seg[0], **{f"repeats_{k}": v for k, v in rep.items()}}, seg[1],
                              seg[2] + ["**Repeats** (a check after step 4, not a step: "
                                        f"{link(checks_dir('unit_repeats'))}; {readme_link('2-steps', 'README')})",
                                        "", table, ""])
    # Check folders written by their own scripts, not by a step: say when they are older than their input.
    for step_id, folder, inputs, script in (
            ("segment", checks_dir("unit_repeats"), [Paths.sentences()], "checks.unit_repeats"),
            ("subsidiaries", checks_dir("entity_inputs"), [Paths.subsidiaries(), Paths.entity_catalog()],
             "checks.entity_inputs")):
        written, _ = newest([folder])
        if written and newest(inputs)[0] > written + 1:
            numbers[step_id][1].append(f"{rel(folder)} is older than {', '.join(p.name for p in inputs)}: "
                                       f"run `python -m src.{script}` again")

    fresh = {s["id"]: freshness(s, last) for s in STEPS}
    report = {"written": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data_dir": str(data_dir()),
              "fresh": fresh, "metrics": {k: v[0] for k, v in numbers.items()},
              "notes": {k: v[1] for k, v in numbers.items() if v[1]}}
    report["checks"] = evaluate(report, before)
    return report, numbers, samples


def anchor(step_id: str) -> str:
    return f"step-{step_id.replace('_', '-')}"


def markdown(report: dict, numbers: dict, before: dict | None, against: Path | None) -> str:
    fresh, checks = report["fresh"], report["checks"]
    count = Counter(c["status"] for c in checks)
    stale = [s for s in STEPS if fresh[s["id"]]["status"] == "stale"]
    change_rows = changes(report, before) if before else []
    changed = {r[0].strip("`").split(".")[0] for r in change_rows}

    def step_link(step_id: str) -> str:
        return f"[{STEP_NAME.get(step_id, step_id)}](#{anchor(step_id)})"

    L = ["# Review report: steps 1–6 (before labelling)", "",
         f"Written {report['written']} from `{report['data_dir']}`. Reads files only; numbers are "
         f"those of the last run of each step. Limits: {link(CONFIG / 'settings.yaml')} `report:`. "
         f"Steps, names and numbers as in the pipeline {readme_link('2-steps', 'README')}; how to use "
         f"this report: {readme_link('what-to-review', 'What to review')}.", "",
         " · ".join(f"{ICON[k]} {count[k]} {k}" for k in ("error", "warn", "info", "pass", "not run") if count[k]), "",
         "## Contents", "",
         f"1. [Checks](#checks): {count['error']} errors, {count['warn']} warnings",
         f"2. [Stale steps](#stale): {len(stale)}",
         f"3. [Changes since the last report](#changes): {len(change_rows) if before else 'baseline'}",
         "4. [Steps](#steps)"]
    for s in STEPS:
        state = {"missing": "" if s.get("computed") else "not run", "stale": "stale", "ok": ""}[fresh[s["id"]]["status"]]
        bad = sum(c["status"] in ("error", "warn") for c in checks if c["step"] == s["id"])
        tags = [t for t in (state, f"{bad} to look at" if bad else "", "changed" if s["id"] in changed else "") if t]
        L += [f"   - {step_link(s['id'])}" + (f" ({', '.join(tags)})" if tags else "")]
    L += [""]

    L += ['<a id="checks"></a>', "## 1. Checks", "",
          "Errors first. `error`: fix before the label run; `warn`: look at it; `info`: worth knowing. "
          "Details and check files are in the step's section.", ""]
    def row(c: dict) -> list:
        value = c["value"] if c["value"] is not None else "–"
        value = f"{value:,}" if isinstance(value, (int, float)) else value
        return [f"{ICON[c['status']]} {c['status']}", step_link(c["step"]), f"`{c['number']}`", value,
                c["limit"], c["why"]]

    head = ["", "step", "check", "value", "limit", "why it matters"]
    problems = [row(c) for c in checks if c["status"] in LEVELS]
    L += [md_table(head, problems) if problems else "Every check passes.", ""]
    rest = [row(c) for c in checks if c["status"] not in LEVELS]
    if rest:
        L += [f"<details><summary>{count['pass']} passed, {count['not run']} not run (the step has not written "
              "its files)</summary>", "", md_table(head, rest), "", "</details>", ""]

    L += ['<a id="stale"></a>', "## 2. Stale steps", ""]
    if stale:
        L += ["An input is newer than the step's result: run these again before reading their numbers, "
              f"in run order (2b before 2, 4b before 4: {readme_link('2-steps', 'README')}).", "",
              md_table(["step", "result written", "why"],
                       [[step_link(s["id"]), fresh[s["id"]]["written"], fresh[s["id"]]["reason"]] for s in stale]), ""]
    else:
        L += ["None.", ""]

    L += ['<a id="changes"></a>', "## 3. Changes since the last report", ""]
    if before is None:
        L += ["No earlier report to compare with: this one is the baseline.", ""]
    else:
        since = f"`{against}`" if against else f"the report of {before.get('written', '?')}"
        L += [f"Compared with {since}." + (" Open the check files of the steps listed." if change_rows else ""), ""]
        L += [md_table(["number", "before", "now", "change"], change_rows) if change_rows else "No number changed.", ""]

    L += ['<a id="steps"></a>', "## 4. Steps", ""]
    for s in STEPS:
        f = fresh[s["id"]]
        m, notes, lines = numbers[s["id"]]
        state = {"missing": s.get("computed", "not run"), "stale": "**stale**", "ok": "up to date"}[f["status"]]
        mark = " (changed)" if s["id"] in changed else ""
        code = ", ".join(link(SRC / c) for c in s["code"])
        L += [f'<a id="{anchor(s["id"])}"></a>', f"### {s['name']}{mark}", "",
              f"`--steps {s['id']}` · code {code} · LLM: {s['llm']} · "
              f"{readme_link(readme_anchor(s), 'step details in the README')}", "",
              f"{state}" + (f", written {f['written']}" if f["written"] else "")
              + (f": {f['reason']}" if f["reason"] else ""), ""]
        files = [(p(), what) for p, what in s["checks"]]
        results = [p for p in summarised(s["results"]()) if p not in {q for q, _ in files}]
        if results:
            L += ["Results (read by the next steps):", ""]
            L += [f"- {link(p)}" + ("" if p.exists() else " (not written yet)") for p in results]
            L += [""]
        if files:
            L += ["Check files:", ""]
            L += [f"- {link(p)}" + ("" if p.exists() else " (not written yet)") + f": {what}" for p, what in files]
            L += [""]
        bad = [c for c in checks if c["step"] == s["id"] and c["status"] in ("error", "warn", "info")]
        if bad or notes:
            L += ["To look at:", ""]
            L += [f"- {ICON[c['status']]} `{c['number']}` = {c['value']} (limit {c['limit']}): {c['why']}" for c in bad]
            L += [f"- {n}" for n in notes]
            L += [""]
        if m:
            L += [md_table(["number", "value"], [[k, f"{v:,}" if isinstance(v, (int, float)) else v]
                                                for k, v in m.items()]), ""]
        L += lines
    return "\n".join(L)


def run(against: Path | None = None) -> None:
    started = time.time()
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    last_path = out / "report.json"
    last = json.loads(last_path.read_text(encoding="utf-8")) if last_path.exists() else None
    before = json.loads(against.read_text(encoding="utf-8")) if against else last
    report, numbers, samples = build(last, before)
    if last is not None:
        shutil.copyfile(last_path, out / "report_previous.json")
    last_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_csv(out / "samples.csv", samples, ["check", "ticker", "form", "accession_number", "section_key",
                                              "unit_id", "role", "prefilter", "text"])
    (out / "report.md").write_text(markdown(report, numbers, before, against), encoding="utf-8")
    count = Counter(c["status"] for c in report["checks"])
    stale = [s for s, f in report["fresh"].items() if f["status"] == "stale"]
    n_changed = len(changes(report, before)) if before else 0
    log(STEP, f"{count['error']} errors, {count['warn']} warnings, {len(stale)} stale steps, "
              f"{n_changed} numbers changed -> {out / 'report.md'} ({time.time() - started:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--against", type=Path, help="a saved report.json to compare with (default: the last one)")
    run(parser.parse_args().against)


if __name__ == "__main__":
    main()
