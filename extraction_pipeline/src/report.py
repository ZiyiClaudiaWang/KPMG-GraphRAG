"""Review report for the steps that need no labelling (1-4 and 7a): what is stale, what changed
since the last report, and which check files to open.

  python -m src.report                  read what the steps wrote, write data/checks/report/
  python -m src.report --against FILE   compare with a saved report.json instead of the last one

Reads files only: no step is re-run, nothing in output/ or interim/ is written, no API, no
database. run_pipeline.py runs it at the end of every run that includes one of these steps.

A step is *stale* when one of its inputs (an earlier step's result, its code under src/, or a
config file) is newer than its result: run that step again before reading its numbers. The
"changes" section compares every number with the report before (report_previous.json), so a
before / after check of a rule change needs no throwaway script for the counts; read the
examples in the check files for the cases themselves.

Outputs (data/checks/report/):
  report.md             read this first
  report.json           the numbers, the flags and the stale steps (for comparing runs, and
                        later the dashboard)
  report_previous.json  the report.json before this one
  samples.csv           examples for each unit check (up to SAMPLES_PER_CHECK per company)

Unit checks (step 4, units the LLM would see) are those of the 18 Nov scale-up diagnostics
(src/diagnose.py) that can be read from sentences.jsonl alone. diagnose.py still runs steps 2-4
in memory on every section, also the ones the filter drops; run it for a deeper look.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import risk_factors
from .clean import ITEM_MARKER
from .common import ROOT, Paths, checks_dir, data_dir, filings, log, read_csv, read_jsonl, settings, write_csv
from .diagnose import BRAND, TABLE_TITLE, md_table
from .report_entity_inputs import subsidiaries_by_year
from .segment import is_category_unit
from .segment.sentences import TERMINAL

STEP = "report"
SRC = ROOT / "src"
CONFIG = ROOT / "config"
SAMPLES_PER_CHECK = 5          # per check and company
OUTLIER_FACTOR, OUTLIER_MIN = 3, 5   # a filing at 3x the median rate of a check, with 5+ cases


def out_dir() -> Path:
    return checks_dir("report")


# ---------------------------------------------------------------- the steps ----

def per_filing(kind) -> list[Path]:
    """The per-filing files of one kind (Paths.sections or Paths.cleaned) that exist."""
    return [p for p in (kind(f) for f in filings()) if p.exists()]


# Each step: what it writes (results), what it reads (inputs: earlier results, its code, its
# config files), the blocks of config/settings.yaml it reads (settings), and the check files to
# open with what to look at. Paths are functions, read late.
STEPS = [
    {"id": "fetch", "name": "1 Fetch",
     "results": lambda: per_filing(Paths.sections),
     "inputs": lambda: [],
     "checks": []},
    {"id": "subsidiaries", "name": "1b Subsidiaries",
     "results": lambda: [Paths.subsidiaries(), Paths.subsidiary_catalog()],
     "inputs": lambda: [CONFIG / "filings.yaml", CONFIG / "subsidiary_aliases.yaml",
                        SRC / "subsidiaries.py", SRC / "ex21"],
     "checks": [(Paths.subsidiary_qa, "rows whose `status` is not OK; `duplicate_names`, `unmapped_jurisdiction`"),
                (Paths.subsidiary_renames, "rows with an empty `review`: same entity renamed, or two entities? "
                                           "Reviewed pairs go to config/subsidiary_aliases.yaml"),
                (lambda: checks_dir("entity_inputs"), "`python -m src.report_entity_inputs`: big year-to-year "
                                                      "changes, repeats, companies without segments")]},
    {"id": "entity_catalog", "name": "1c Entity catalog",
     "results": lambda: [Paths.entity_catalog()],
     "inputs": lambda: [Paths.subsidiary_catalog(), CONFIG / "entity_linking.yaml", SRC / "entity_catalog.py"],
     "settings": ["entity_catalog"],
     "checks": []},
    {"id": "page_breaks", "name": "2b Page breaks (paid, run on purpose)",
     "results": lambda: [Paths.page_break_decisions()],
     "inputs": lambda: [],            # never re-run by a default run: not reported stale
     "checks": [(Paths.page_break_candidates, "gray boundaries sent to the model, with both sides")]},
    {"id": "clean", "name": "2 Clean",
     "results": lambda: per_filing(Paths.cleaned),
     "inputs": lambda: per_filing(Paths.sections) + [Paths.page_break_decisions(), SRC / "clean.py"],
     "settings": ["clean"],
     "checks": []},
    {"id": "filter", "name": "3 Filter",
     "results": lambda: [Paths.manifest()],
     "inputs": lambda: per_filing(Paths.cleaned) + [CONFIG / "sections.yaml", SRC / "filter_sections.py"],
     "checks": [(Paths.manifest, "`review` rows; add overrides in config/sections.yaml")]},
    {"id": "headings", "name": "4b Heading levels (from the HTML)",
     "results": lambda: [Paths.heading_levels(), Paths.lead_sentences(), Paths.html_tables()],
     "inputs": lambda: [Paths.manifest(), SRC / "headings"] + per_filing(Paths.cleaned),
     "settings": ["headings", "segment"],
     "checks": [(Paths.heading_summary, "companies with a low `found_in_html_pct`, `filings_no_html` > 0, "
                                        "`flag_risk_sentence`, `flag_before_table`, `level_4plus`"),
                (Paths.heading_review, "the headings behind those counts (filter on `flags`, `reason`)"),
                (Paths.heading_html_diff, "`html_only`: HTML headings step 4 missed; `step4_plain`: step-4 "
                                          "headings the HTML shows as plain text")]},
    {"id": "segment", "name": "4 Segment",
     "results": lambda: [Paths.sentences()],
     "inputs": lambda: [Paths.manifest(), Paths.heading_levels(), Paths.lead_sentences(), Paths.html_tables(),
                        SRC / "segment"] + per_filing(Paths.cleaned),
     "settings": ["segment", "headings"],
     "checks": [(lambda: out_dir() / "samples.csv", "examples of the unit checks below (filter on `check`)"),
                (Paths.lead_summary, "per company: leads by source, units under a lead"),
                (Paths.lead_review, "one row per lead; `rule_heading` rows the HTML does not confirm"),
                (lambda: checks_dir("unit_repeats"), "`python -m src.report_unit_repeats`: repeats across "
                                                     "filings, before labelling")]},
    {"id": "risk_factors", "name": "7a Risk categories (needs no labels)",
     "results": lambda: [Paths.risk_categories()],
     "inputs": lambda: [Paths.sentences(), CONFIG / "risk_theme_map.csv", CONFIG / "risk_themes.csv",
                        SRC / "risk_factors.py"],
     "checks": [(lambda: CONFIG / "risk_theme_map.csv", "add the unmapped category texts listed below")]},
]
STEP_IDS = [s["id"] for s in STEPS]


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


# ---------------------------------------------------------------- numbers per step ----
# Each returns (metrics, flags, lines): numbers to compare between runs, short warnings, and
# extra markdown (tables) for the report.

def fetch_numbers() -> tuple[dict, list, list]:
    fs, all_fs = filings(), filings(include_on_hold=True)
    missing = [f for f in fs if not Paths.sections(f).exists()]
    m = {"filings_passed": len(fs), "filings_on_hold": len(all_fs) - len(fs),
         "10-K": sum(f["form"] == "10-K" for f in fs), "10-Q": sum(f["form"] == "10-Q" for f in fs),
         "exports": len(fs) - len(missing), "exports_missing": len(missing)}
    flags = [f"no export for {len(missing)} passed filings: "
             + ", ".join(f"{f['ticker']} {f['form']} {f['accession']}" for f in missing[:10])] if missing else []
    return m, flags, []


def subsidiaries_numbers() -> tuple[dict, list, list]:
    if not Paths.subsidiaries().exists():
        return {}, [], []
    rows = list(read_jsonl(Paths.subsidiaries()))
    qa = read_csv(Paths.subsidiary_qa()) if Paths.subsidiary_qa().exists() else []
    renames = read_csv(Paths.subsidiary_renames()) if Paths.subsidiary_renames().exists() else []
    catalog = json.loads(Paths.subsidiary_catalog().read_text(encoding="utf-8")) \
        if Paths.subsidiary_catalog().exists() else []
    ten_ks = {f["accession"] for f in filings() if f["form"] == "10-K"}
    not_ok = [q for q in qa if q["status"] != "OK"]
    big = [r for r in subsidiaries_by_year(rows) if r["flag"]]
    m = {"10k_in_filings": len(ten_ks), "10k_checked": len(qa), "10k_not_ok": len(not_ok),
         "rows": len(rows), "distinct_subsidiaries": len(catalog),
         "renames": len(renames), "renames_not_reviewed": sum(1 for r in renames if not r["review"]),
         "big_year_changes": len(big)}
    flags = []
    if not_ok:
        flags.append(f"{len(not_ok)} 10-Ks not OK in subsidiary_qa.csv: "
                     + ", ".join(f"{q['ticker']} {q['accession']} ({q['status']})" for q in not_ok[:8]))
    unchecked = ten_ks - {q["accession"] for q in qa}
    if unchecked:
        flags.append(f"{len(unchecked)} 10-Ks in filings.yaml are not in subsidiary_qa.csv: run step 1b")
    if m["renames_not_reviewed"]:
        flags.append(f"{m['renames_not_reviewed']} possible renames not reviewed (subsidiary_renames.csv)")
    lines = []
    if big:
        lines = ["Row count of a 10-K moved by more than half from the 10-K before:", "",
                 md_table(["ticker", "period", "rows", "10-K before", "change"],
                          [[r["ticker"], r["reporting_period_end"], r["rows"], r["rows_10k_before"], r["change"]]
                           for r in big]), ""]
    return m, flags, lines


def entity_catalog_numbers() -> tuple[dict, list, list]:
    if not Paths.entity_catalog().exists():
        return {}, [], []
    catalog = json.loads(Paths.entity_catalog().read_text(encoding="utf-8"))
    kinds = Counter(e["entity_type"] for e in catalog)
    ticker = {str(f["cik"]).zfill(10): f["ticker"] for f in filings()}
    with_segments = {e["issuer_cik"] for e in catalog if e["entity_type"] == "segment"}
    without = sorted({t for c, t in ticker.items() if c not in with_segments})
    m = {f"entries_{k}": v for k, v in sorted(kinds.items())}
    m["companies_without_segments"] = len(without)
    flags = [f"companies with no segment in the catalog: {', '.join(without)}"] if without else []
    return m, flags, []


def page_breaks_numbers() -> tuple[dict, list, list]:
    m = {}
    if Paths.page_break_candidates().exists():
        m["gray_boundaries"] = sum(1 for _ in read_jsonl(Paths.page_break_candidates()))
    if Paths.page_break_decisions().exists():
        rows = list(read_jsonl(Paths.page_break_decisions()))
        m["decisions"] = len(rows)
        m["joined"] = sum(bool(r.get("same_sentence")) for r in rows)
    ledger = Paths.step_usage("page_breaks")
    if ledger.exists():
        m["cost_usd"] = round(sum(r.get("estimated_cost_usd") or 0 for r in read_jsonl(ledger)), 4)
    return m, [], []


def clean_numbers() -> tuple[dict, list, list]:
    removed, m = Counter(), Counter()
    exported = {Paths.sections(f).name for f in filings() if Paths.sections(f).exists()}
    cleaned = per_filing(Paths.cleaned)
    for p in cleaned:
        m["filings"] += 1
        for sec in read_jsonl(p):
            m["sections"] += 1
            m["paragraphs"] += len(sec["paragraphs"])
            m["joined"] += sec.get("joined", 0)
            removed.update(r["rule"] for r in sec.get("removed", []))
    m["removed"] = sum(removed.values())
    m.update({f"removed_{k}": v for k, v in removed.items()})
    flags = []
    not_cleaned = len(exported) - len(cleaned)
    if not_cleaned > 0:
        flags.append(f"{not_cleaned} exported filings have no cleaned file: run step 2")
    return dict(m), flags, []


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
    review = [f"{r['section_key']} of {r['accession_number']}" for r in rows if r["decision"] == "review"]
    flags = []
    if gaps:
        flags.append(f"{len(gaps)} filings lack a section kept for their form: " + "; ".join(gaps[:8]))
    if review:
        flags.append(f"{len(review)} sections marked review (decide in config/sections.yaml)")
    return m, flags, []


def headings_numbers() -> tuple[dict, list, list]:
    if not Paths.heading_levels().exists():
        return {}, [], []
    levels = list(read_jsonl(Paths.heading_levels()))
    lv = Counter(min(int(r["level"]), 4) for r in levels)
    reasons = Counter(r["reason"] for r in levels)
    m = {"headings": len(levels), "sentence_level": lv.get(-1, 0), "not_a_heading_level_0": lv.get(0, 0),
         "level_1": lv.get(1, 0), "level_2": lv.get(2, 0), "level_3": lv.get(3, 0), "level_4plus": lv.get(4, 0)}
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
    if Paths.heading_html_diff().exists():
        from .headings.step import read_output
        diff = read_output(Paths.heading_html_diff())
        if "kind" in diff:
            m.update({f"html_diff_{k}": int(v) for k, v in diff["kind"].value_counts().items()})
    flags = []
    if no_html:
        flags.append(f"{no_html} filings without HTML: their headings are all siblings (run step 4b with "
                     "SEC_USER_AGENT set)")
    if m["sentence_level"]:
        flags.append(f"{m['sentence_level']} headings at level -1 (may be sentences): review in heading_review.csv")
    return m, flags, []


def segment_numbers(units: list[dict]) -> tuple[dict, list, list]:
    if not units:
        return {}, [], []
    to_llm = [u for u in units if u["prefilter"] is None]
    m = {"units": len(units), "to_llm": len(to_llm),
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
    versions = sorted({u.get("segmentation_version") for u in units}, key=str)
    flags = [f"sentences.jsonl mixes segmentation versions: {versions}"] if len(versions) > 1 else []
    lines = [f"segmentation_version: `{', '.join(map(str, versions))}`", "",
             md_table(["section_type", "units", "to the LLM"],
                      [[t, f"{c['units']:,}", f"{c['to_llm']:,}"] for t, c in sorted(by_type.items())]), ""]
    return m, flags, lines


def risk_numbers(units: list[dict]) -> tuple[dict, list, list]:
    if not units:
        return {}, [], []
    categories, assignments = risk_factors.assign(units, risk_factors.theme_map(risk_factors.theme_library()))
    status = Counter(c["map_status"] for c in categories)
    unmapped = Counter((c["ticker"], c["category_text"]) for c in categories if c["map_status"] == "unmapped")
    m = {"categories": len(categories), "mapped": status["mapped"], "not_a_theme": status["not_a_theme"],
         "unmapped": status["unmapped"], "unmapped_texts": len(unmapped),
         "item_1a_units": len(assignments),
         "item_1a_units_with_theme": sum(bool(a["risk_ids"]) for a in assignments)}
    flags = [f"{len(unmapped)} category texts not in config/risk_theme_map.csv"] if unmapped else []
    lines = []
    if unmapped:
        lines = ["Unmapped category texts (add them to `config/risk_theme_map.csv`):", "",
                 md_table(["ticker", "category text", "filings"],
                          [[t, x, n] for (t, x), n in sorted(unmapped.items())]), ""]
    return m, flags, lines


# ---------------------------------------------------------------- unit checks ----

UNIT_CHECKS = [
    ("lowercase_start", "Sent to the LLM, starts lower case, not a list item: a missed page-break join or a bad split."),
    ("no_end_punct", "Sent to the LLM, no end punctuation, no ':' or '|': a cut sentence or a missed heading."),
    ("very_long", "Sent to the LLM, over 1,500 characters: a missed split (often a ';' list in one sentence)."),
    ("single_pipe", "Sent to the LLM with one '|': a two-column table row."),
    ("long_heading", "Heading over 100 characters (table titles left out): often a sentence, never sent to the LLM."),
    ("risk_section_no_category", "Item 1A with 20+ units and no category heading: its Claims get no risk theme."),
]


def unit_checks(units: list[dict]) -> tuple[dict, list, list, list[dict]]:
    """Counts per check, per filing, and samples (up to SAMPLES_PER_CHECK per check and company)."""
    hits: dict[str, list[dict]] = defaultdict(list)
    per_filing_n: dict[str, Counter] = defaultdict(Counter)
    sections: dict[tuple, list[dict]] = defaultdict(list)
    for u in units:
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
                outliers.append([f"`{c}`", acc, k, f"{1000 * r:.1f}", f"{1000 * med:.1f}"])
    m = {c: len(hits[c]) for c, _ in UNIT_CHECKS}
    lines = [md_table(["check", "what it means", "units"], [[f"`{c}`", meaning, f"{m[c]:,}"]
                                                             for c, meaning in UNIT_CHECKS]), ""]
    if outliers:
        lines += [f"Filings at {OUTLIER_FACTOR}x or more the median rate of a check ({OUTLIER_MIN}+ cases):", "",
                  md_table(["check", "accession", "units", "per 1k units", "median per 1k"], outliers), ""]
    flags = [f"{m['risk_section_no_category']} Item 1A sections with no category heading"] \
        if m["risk_section_no_category"] else []
    return m, flags, lines, samples


def repeats_numbers() -> dict:
    """Pre-label repeats, from python -m src.report_unit_repeats (summary.csv), when it has run."""
    path = checks_dir("unit_repeats") / "summary.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        import csv
        rows = list(csv.DictReader(f))
    m = Counter()
    for r in rows:
        m[f"{r['level']}_repeated_units"] += int(r["repeated_units"])
        m[f"{r['level']}_reusable_units"] += int(r["reusable_units"])
    return dict(m)


# ---------------------------------------------------------------- changes ----

def flat(metrics: dict) -> dict[str, float]:
    return {f"{s}.{k}": v for s, ms in metrics.items() for k, v in ms.items() if isinstance(v, (int, float))}


def changes(now: dict, before: dict) -> list[list]:
    a, b = flat(before.get("metrics", {})), flat(now["metrics"])
    out = []
    for k in sorted(set(a) | set(b), key=lambda k: (STEP_IDS.index(k.split(".")[0])
                                                    if k.split(".")[0] in STEP_IDS else 99, k)):
        x, y = a.get(k), b.get(k)
        if x == y:
            continue
        if x is None or y is None:
            out.append([f"`{k}`", "-" if x is None else f"{x:,}", "-" if y is None else f"{y:,}", ""])
        else:
            pct = f"{100 * (y - x) / x:+.1f}%" if x else ""
            out.append([f"`{k}`", f"{x:,}", f"{y:,}", pct])
    return out


# ---------------------------------------------------------------- report ----

def build(last: dict | None) -> tuple[dict, dict, list[dict]]:
    units = list(read_jsonl(Paths.sentences())) if Paths.sentences().exists() else []
    numbers = {
        "fetch": fetch_numbers(), "subsidiaries": subsidiaries_numbers(),
        "entity_catalog": entity_catalog_numbers(), "page_breaks": page_breaks_numbers(),
        "clean": clean_numbers(), "filter": filter_numbers(), "headings": headings_numbers(),
        "segment": segment_numbers(units), "risk_factors": risk_numbers(units),
    }
    checks_m, checks_flags, checks_lines, samples = unit_checks(units) if units else ({}, [], [], [])
    seg_m, seg_flags, seg_lines = numbers["segment"]
    numbers["segment"] = ({**seg_m, **{f"check_{k}": v for k, v in checks_m.items()}}, seg_flags + checks_flags,
                          seg_lines + (["**Unit checks**", ""] + checks_lines if checks_lines else []))
    rep = repeats_numbers()
    if rep:
        seg = numbers["segment"]
        numbers["segment"] = ({**seg[0], **{f"repeats_{k}": v for k, v in rep.items()}}, seg[1], seg[2])

    fresh = {s["id"]: freshness(s, last) for s in STEPS}
    report = {"written": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data_dir": str(data_dir()),
              "fresh": fresh, "metrics": {k: v[0] for k, v in numbers.items()},
              "flags": {k: v[1] for k, v in numbers.items() if v[1]}}
    return report, numbers, samples


def markdown(report: dict, numbers: dict, before: dict | None, against: Path | None) -> str:
    fresh = report["fresh"]
    L = ["# Review report: steps before labelling", "",
         f"Written {report['written']} from `{report['data_dir']}`. Reads files only; numbers are "
         "those of the last run of each step.", ""]

    stale = [s for s in STEPS if fresh[s["id"]]["status"] == "stale"]
    L += ["## 1. Stale steps", ""]
    if stale:
        L += ["An input is newer than the step's result: run these again before reading their numbers "
              "(in pipeline order).", "",
              md_table(["step", "result written", "why"],
                       [[s["name"], fresh[s["id"]]["written"], fresh[s["id"]]["reason"]] for s in stale]), ""]
    else:
        L += ["None.", ""]

    L += ["## 2. Changes since the last report", ""]
    if before is None:
        L += ["No earlier report to compare with: this one is the baseline.", ""]
    else:
        rows = changes(report, before)
        since = f"`{against}`" if against else f"the report of {before.get('written', '?')}"
        L += [f"Compared with {since}."
              + (" Open the check files of the steps listed (section 4)." if rows else ""), ""]
        L += [md_table(["number", "before", "now", "change"], rows) if rows else "No number changed.", ""]

    flags = [(s["name"], f) for s in STEPS for f in report["flags"].get(s["id"], [])]
    L += ["## 3. To look at", ""]
    L += [f"- **{name}**: {f}" for name, f in flags] if flags else ["Nothing flagged."]
    L += [""]

    changed = {r[0].strip("`").split(".")[0] for r in (changes(report, before) if before else [])}
    L += ["## 4. Steps", ""]
    for s in STEPS:
        f = fresh[s["id"]]
        m, _, lines = numbers[s["id"]]
        state = {"missing": "not run", "stale": "**stale**", "ok": "up to date"}[f["status"]]
        mark = " (changed)" if s["id"] in changed else ""
        L += [f"### {s['name']}{mark}", "", f"{state}" + (f", written {f['written']}" if f["written"] else ""), ""]
        if m:
            L += [md_table(["number", "value"], [[k, f"{v:,}" if isinstance(v, (int, float)) else v]
                                                for k, v in m.items()]), ""]
        L += lines
        files = [(p(), what) for p, what in s["checks"]]
        if files:
            L += ["Check files:", ""]
            for p, what in files:
                L += [f"- `{rel(p)}`" + ("" if p.exists() else " (not written yet)") + f": {what}"]
            L += [""]
    return "\n".join(L)


def run(against: Path | None = None) -> None:
    started = time.time()
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    last_path = out / "report.json"
    last = json.loads(last_path.read_text(encoding="utf-8")) if last_path.exists() else None
    before = json.loads(against.read_text(encoding="utf-8")) if against else last
    report, numbers, samples = build(last)
    if last is not None:
        shutil.copyfile(last_path, out / "report_previous.json")
    last_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_csv(out / "samples.csv", samples, ["check", "ticker", "form", "accession_number", "section_key",
                                              "unit_id", "role", "prefilter", "text"])
    (out / "report.md").write_text(markdown(report, numbers, before, against), encoding="utf-8")
    stale = [s for s, f in report["fresh"].items() if f["status"] == "stale"]
    n_changed = len(changes(report, before)) if before else 0
    log(STEP, f"{len(stale)} stale steps{' (' + ', '.join(stale) + ')' if stale else ''}, "
              f"{n_changed} numbers changed, {sum(map(len, report['flags'].values()))} flags "
              f"-> {out / 'report.md'} ({time.time() - started:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--against", type=Path, help="a saved report.json to compare with (default: the last one)")
    run(parser.parse_args().against)


if __name__ == "__main__":
    main()
