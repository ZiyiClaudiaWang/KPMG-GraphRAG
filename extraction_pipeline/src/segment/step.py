"""Step 4 run(): every kept section of every filing -> data/output/sentences.jsonl, plus the
lead review files."""

from __future__ import annotations

from collections import Counter

from ..common import Paths, filings, log, read_jsonl, settings, write_csv, write_jsonl
from ..filter_sections import kept_section_keys
from .link_leads import LEAD_REVIEW_FIELDS, LEAD_RULES, LEAD_SUMMARY_FIELDS, lead_report, mark_leads, uses_rule
from .section import segment_section

STEP = "segment"


def run() -> None:
    from ..headings import load_leads, load_levels, load_tables   # from step 4b, when it has run
    cfg = settings()["segment"]
    kept = kept_section_keys()
    levels = load_levels()
    if levels:
        log(STEP, f"{len(levels)} heading levels from step 4b")
    leads = load_leads()
    tables = load_tables()
    rule_mode = cfg.get("lead_rule", "fallback")
    if rule_mode not in LEAD_RULES:
        raise SystemExit(f"settings.yaml segment.lead_rule must be one of {', '.join(LEAD_RULES)}, not {rule_mode!r}")
    all_units = []
    no_html: set[str] = set()
    unmatched: dict[str, int] = {}
    for f in filings():
        facts = leads.get(f["accession"], {"html": False, "leads": {}})
        if not facts["html"]:
            no_html.add(f["accession"])
        table_facts = tables.get(f["accession"], {"html": False, "rows": {}})
        for sec in read_jsonl(Paths.cleaned(f)):
            if (sec["accession_number"], sec["section_key"]) not in kept:
                continue
            rows = table_facts["rows"].get(sec["section_key"], []) if table_facts["html"] else None
            units = segment_section(sec, cfg, levels, rows)
            section_leads = facts["leads"].get(sec["section_key"])
            miss = mark_leads(units, section_leads, use_rule=uses_rule(rule_mode, facts["html"], section_leads))
            unmatched[f["accession"]] = unmatched.get(f["accession"], 0) + miss
            all_units += units
            to_label = sum(u["prefilter"] is None for u in units)
            proposed = sum(bool(u["rule_heading"]) for u in units)
            log(STEP, f"{sec['ticker']} {sec['form']} {sec['section_key']}: {len(units)} units, "
                      f"{to_label} to label, {proposed} proposed risk headings")
    write_jsonl(Paths.sentences(), all_units)
    log(STEP, f"{len(all_units)} units -> {Paths.sentences()}")
    summary, review = lead_report(all_units, no_html, unmatched)
    write_csv(Paths.lead_summary(), summary, LEAD_SUMMARY_FIELDS)
    write_csv(Paths.lead_review(), review, LEAD_REVIEW_FIELDS)
    n_leads = sum(r["leads"] for r in summary)
    log(STEP, f"{n_leads} lead sentences ({sum(r['leads_html'] for r in summary)} html, "
              f"{sum(r['leads_html_runin'] for r in summary)} html_runin, {sum(r['leads_rule'] for r in summary)} rule; "
              f"segment.lead_rule: {rule_mode}), {sum(r['units_under_lead'] for r in summary)} units under a lead, "
              f"{sum(r['leads_unmatched'] for r in summary)} HTML leads matched no unit; "
              f"{len(no_html)} filings without HTML leads")
    log(STEP, f"leads: {Paths.lead_summary()}  {Paths.lead_review()}")
    no_tables = sum(1 for f in filings() if not tables.get(f["accession"], {}).get("html"))
    in_tables = Counter((u["html_table"], u["prefilter"] or "to_llm") for u in all_units if u["html_table"])
    log(STEP, f"HTML tables: {no_tables} filings without (text rule for tables); units in tables: "
              + ", ".join(f"{k} {v}" for k, v in sorted(in_tables.items(), key=lambda kv: (kv[0][0] or "", kv[0][1]))))
