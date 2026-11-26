"""Lead sentences (every section): which units are leads, and the lead above every unit.

A lead sentence sums up the sentences under it. It is not a heading: it is labelled like any
sentence and changes no section_path. Step 4b finds leads in the filing's HTML
(src/headings/leads.py); here they are matched to units and every unit gets:

  lead_source    this unit is a lead: html, html_runin (from the HTML) or rule (the rule_heading
                 proposal in Item 1A, risk.py), used for filings without HTML; with
                 segment.lead_rule: section also in sections whose HTML shows no lead, with union
                 everywhere; null = not a lead
  lead_unit_id   the unit_id of the lead above it, until the next lead, the next heading (any
                 level but 0) or the end of the section; null for a lead itself and for text
                 under no lead (directly under its heading or section)

Review files: data/output/lead_summary.csv (one row per company) and lead_review.csv (one row
per lead, and per rule_heading the HTML does not confirm).
"""

from __future__ import annotations

LEAD_ROLES = ("body", "list_group")
LEAD_RULES = ("fallback", "section", "union")
NOT_LEAD = ("cross_reference", "table_residue")   # prefilters that are never a lead ("Refer to Note 7 ...")


def uses_rule(mode: str, has_html: bool, section_leads: list | None) -> bool:
    """Whether rule_heading units are leads in a section (settings.yaml segment.lead_rule):
    fallback = only in filings without HTML; section = also in sections whose HTML shows no lead
    (a filing that styles no lead there, AAPL 10-Q); union = everywhere."""
    if mode == "union" or not has_html:
        return True
    return mode == "section" and not section_leads


def mark_leads(units: list[dict], leads: list[dict] | None, use_rule: bool) -> int:
    """Set lead_source and lead_unit_id on the units of one section (in source order).

    leads: the section's lead spans from step 4b (headings/leads.py), [{start, end, source}]
    in `content` positions; the first body or list unit that starts inside a span is that lead,
    unless step 4 prefiltered it as a cross reference or table residue.
    use_rule: units with rule_heading are leads too (source rule) when the HTML did not already
    make them one. Returns the number of lead spans no unit starts in.
    """
    for u in units:
        u["lead_source"] = None
        u["lead_unit_id"] = None
    unmatched = 0
    candidates = [u for u in units if u["role"] in LEAD_ROLES and u["raw_spans"]]
    for lead in sorted(leads or [], key=lambda x: x["start"]):
        hit = next((u for u in candidates if lead["start"] - 2 <= u["raw_spans"][0][0] < lead["end"]
                    and not u["lead_source"]), None)
        if hit is None:
            unmatched += 1
        elif hit["prefilter"] not in NOT_LEAD:
            hit["lead_source"] = lead["source"]
    if use_rule:
        for u in candidates:
            if u.get("rule_heading") and not u["lead_source"] and u["prefilter"] not in NOT_LEAD:
                u["lead_source"] = "rule"
    current = None
    for u in units:
        if u["role"] == "section_title" or (u["role"] == "heading" and u.get("heading_level", 1) != 0):
            current = None
        elif u["lead_source"]:
            current = u["unit_id"]
        elif u["role"] in LEAD_ROLES:
            u["lead_unit_id"] = current
    return unmatched


LEAD_SUMMARY_FIELDS = ["ticker", "filings", "filings_no_html", "units", "leads", "leads_html", "leads_html_runin",
                       "leads_rule", "units_under_lead", "under_lead_pct", "leads_unmatched",
                       "rule_heading", "rule_and_html", "rule_only", "html_not_rule_1a"]
LEAD_REVIEW_FIELDS = ["kind", "ticker", "form", "accession_number", "section_key", "section_type",
                      "lead_source", "rule_heading", "units_under", "text", "next_text", "unit_id"]


def lead_report(units: list[dict], no_html: set[str], unmatched: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """Rows of lead_summary.csv (per company) and lead_review.csv (per lead, and per rule_heading
    unit the HTML did not make a lead). no_html: accessions without HTML leads; unmatched:
    accession -> lead spans no unit starts in."""
    under: dict[str, int] = {}
    for u in units:
        if u.get("lead_unit_id"):
            under[u["lead_unit_id"]] = under.get(u["lead_unit_id"], 0) + 1
    by_ticker: dict[str, list[dict]] = {}
    for u in units:
        by_ticker.setdefault(u["ticker"], []).append(u)
    summary, review = [], []
    for ticker in sorted(by_ticker):
        us = by_ticker[ticker]
        accessions = {u["accession_number"] for u in us}
        html_units = [u for u in us if u["accession_number"] not in no_html]
        leads = [u for u in us if u["lead_source"]]
        rule = [u for u in html_units if u.get("rule_heading")]
        n_under = sum(bool(u.get("lead_unit_id")) for u in us)
        summary.append({
            "ticker": ticker, "filings": len(accessions), "filings_no_html": len(accessions & no_html),
            "units": len(us), "leads": len(leads),
            **{f"leads_{k}": sum(u["lead_source"] == k for u in leads) for k in ("html", "html_runin", "rule")},
            "units_under_lead": n_under, "under_lead_pct": round(100 * n_under / len(us), 1) if us else "",
            "leads_unmatched": sum(unmatched.get(a, 0) for a in accessions),
            "rule_heading": len(rule),
            "rule_and_html": sum(u["lead_source"] in ("html", "html_runin") for u in rule),
            "rule_only": sum(u["lead_source"] in (None, "rule") for u in rule),
            "html_not_rule_1a": sum(u["lead_source"] in ("html", "html_runin") and not u.get("rule_heading")
                                    and u["section_type"] == "risk_factors" for u in html_units),
        })
        nexts = {}
        for k, u in enumerate(us):
            if u["lead_source"] or u.get("rule_heading"):
                nxt = next((v["text"] for v in us[k + 1:k + 2]), "")
                nexts[u["unit_id"]] = " ".join(nxt.split()[:15])
        for u in us:
            in_html = u["accession_number"] not in no_html
            if u["lead_source"]:
                kind = "lead"
            elif u.get("rule_heading") and in_html:
                kind = "rule_only"
            else:
                continue
            review.append({"kind": kind, **{k: u.get(k) for k in LEAD_REVIEW_FIELDS if k in u},
                           "units_under": under.get(u["unit_id"], 0), "next_text": nexts.get(u["unit_id"], "")})
    return summary, review
