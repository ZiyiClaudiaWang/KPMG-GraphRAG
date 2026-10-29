"""Step 7a - Risk factors: settle risk headings, assign sentences to them, match them across filings.

Input : data/output/sentences.jsonl, data/output/labels.jsonl
Output: data/output/risk_headings.csv          every heading, with rule and LLM opinions
        data/output/risk_factors.csv           one RiskFactor per group of matching headings
        data/interim/risk_assignments.jsonl    sentence -> heading -> RiskFactor

A heading is final when the LLM flags it (is_risk_heading); without an LLM
label the rule proposal from step 4 is used. Rows where the two disagree are
marked needs_review.

Each heading belongs to one RiskFactor. Headings of the same company match
across filings by text: identical after normalisation (exact), or similar at
or above the configured threshold (fuzzy, needs_review). Different companies
are never matched here: that needs a theme layer above the headings (an open
question in the outline; eval question M18 depends on it).
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

from .common import Paths, filings, log, read_jsonl, settings, write_csv, write_jsonl

STEP = "risk_factors"

HEADING_FIELDS = ["heading_id", "accession_number", "ticker", "form", "section_key", "category",
                  "heading_text", "rule_detected", "llm_detected", "needs_review", "n_units",
                  "risk_factor_id", "match_method", "match_score"]
FACTOR_FIELDS = ["risk_factor_id", "ticker", "name", "n_headings", "accessions", "match_method", "needs_review"]


def normalise(text: str) -> str:
    text = text.lower().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", text)).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def find_headings(units: list[dict], labels: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Return heading rows and per-sentence assignments for all Item 1A sections."""
    headings, assignments = [], []
    current = None
    category = None
    section = None
    for u in units:
        if u["section_type"] != "risk_factors":
            continue
        if (u["accession_number"], u["section_key"]) != section:
            section, current, category = (u["accession_number"], u["section_key"]), None, None
        if u["role"] == "category_heading":
            category, current = u["text"], None
            continue
        label = labels.get(u["sentence_id"])
        llm = bool(label and label["source"] == "llm" and label["is_risk_heading"])
        rule = bool(u.get("rule_heading"))
        decided_by_llm = bool(label and label["source"] == "llm")
        is_heading = llm if decided_by_llm else rule
        if is_heading:
            current = {
                "heading_id": u["sentence_id"], "accession_number": u["accession_number"],
                "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                "category": category, "heading_text": u["text"],
                "rule_detected": rule, "llm_detected": llm if decided_by_llm else None,
                "needs_review": decided_by_llm and llm != rule, "n_units": 0,
            }
            headings.append(current)
        elif rule and decided_by_llm:
            # The rules proposed a heading that the LLM rejected: keep it visible for review.
            headings.append({
                "heading_id": u["sentence_id"], "accession_number": u["accession_number"],
                "ticker": u["ticker"], "form": u["form"], "section_key": u["section_key"],
                "category": category, "heading_text": u["text"], "rule_detected": True,
                "llm_detected": False, "needs_review": True, "n_units": None,
                "risk_factor_id": None, "match_method": "rejected", "match_score": None,
            })
        if current:
            current["n_units"] += 1
            assignments.append({"sentence_id": u["sentence_id"], "risk_heading_id": current["heading_id"],
                                "risk_heading": current["heading_text"]})
    return headings, assignments


def match_headings(headings: list[dict], threshold: float) -> list[dict]:
    """Group headings of the same company into RiskFactors. Adds risk_factor_id to each heading."""
    period = {f["accession"]: f["period_end"] for f in filings()}
    live = [h for h in headings if h.get("match_method") != "rejected"]
    live.sort(key=lambda h: (period[h["accession_number"]], h["heading_id"]))  # earliest filing first

    parent = {h["heading_id"]: h["heading_id"] for h in live}
    method = {h["heading_id"]: ("single", None) for h in live}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if a["ticker"] != b["ticker"] or a["accession_number"] == b["accession_number"]:
                continue
            if normalise(a["heading_text"]) == normalise(b["heading_text"]):
                kind, score = "exact", 1.0
            else:
                score = similarity(a["heading_text"], b["heading_text"])
                if score < threshold:
                    continue
                kind = "fuzzy"
            ra, rb = root(a["heading_id"]), root(b["heading_id"])
            if ra != rb:
                parent[rb] = ra
            for h in (a, b):
                if method[h["heading_id"]][0] != "fuzzy":
                    method[h["heading_id"]] = (kind, round(score, 3))

    groups: dict[str, list[dict]] = {}
    for h in live:
        groups.setdefault(root(h["heading_id"]), []).append(h)

    factors = []
    for members in groups.values():
        canonical = members[0]  # heading from the earliest filing names the RiskFactor
        digest = hashlib.sha1(normalise(canonical["heading_text"]).encode()).hexdigest()[:10]
        rf_id = f"rf-{canonical['ticker'].lower()}-{digest}"
        methods = {method[h["heading_id"]][0] for h in members}
        overall = "fuzzy" if "fuzzy" in methods else "exact" if "exact" in methods else "single"
        for h in members:
            h["risk_factor_id"] = rf_id
            h["match_method"], h["match_score"] = method[h["heading_id"]]
            if h["match_method"] == "fuzzy":
                h["needs_review"] = True
        factors.append({
            "risk_factor_id": rf_id, "ticker": canonical["ticker"], "name": canonical["heading_text"],
            "n_headings": len(members),
            "accessions": " ".join(sorted({h["accession_number"] for h in members})),
            "match_method": overall, "needs_review": overall == "fuzzy",
        })
    return factors


def run() -> None:
    units = list(read_jsonl(Paths.sentences()))
    labels = {l["sentence_id"]: l for l in read_jsonl(Paths.labels())} if Paths.labels().exists() else {}
    headings, assignments = find_headings(units, labels)
    factors = match_headings(headings, settings()["risk_factors"]["fuzzy_match_threshold"])

    factor_of = {h["heading_id"]: h.get("risk_factor_id") for h in headings}
    for a in assignments:
        a["risk_factor_id"] = factor_of.get(a["risk_heading_id"])

    write_csv(Paths.risk_headings(), headings, HEADING_FIELDS)
    write_csv(Paths.risk_factors(), factors, FACTOR_FIELDS)
    write_jsonl(Paths.risk_assignments(), assignments)
    review = sum(bool(h["needs_review"]) for h in headings)
    log(STEP, f"{len(headings)} headings ({review} to review) -> {Paths.risk_headings()}")
    log(STEP, f"{len(factors)} RiskFactors -> {Paths.risk_factors()}")


if __name__ == "__main__":
    run()
