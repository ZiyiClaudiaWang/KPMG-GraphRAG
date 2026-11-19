"""Before/after report for Claim -> subsidiary/segment links (#91 P1.6). No OpenAI calls.

Re-runs step 7b's matching (entity_annotation.annotate_mentions) on the model's mentions in an
existing claims.jsonl, with the current catalog, and compares with what that claims.jsonl says.
So a frozen run (runs/<name>/) can be measured without relabelling.

Input : <run>/output/claims.jsonl, <run>/output/sentences.jsonl   (--run, default: data/)
        data/entity_catalog.json (step 1c)                          (--catalog)
Output: <out>/entity_link_report.json   counts before and after, by status, origin, type, ticker
        <out>/entity_links.csv          every mention that links (matched / matched_auto)
        <out>/entity_link_sample.csv    --sample links drawn at random (seeded), with empty
                                        "correct" and "note" columns for checking by hand
        <out> defaults to <run>/output/entity_links/

Usage:
  python -m src.report_entity_links --run ../runs/2026-11_20filings_label-v2
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from .common import data_dir, log, read_jsonl, write_csv
from .entity_annotation import LINKED, VERSION, annotate_mentions, validate_catalog

STEP = "report_entity_links"
MODEL_FIELDS = ("mention_text", "entity_type", "reference_kind", "evidence_quote")
LINK_COLS = ["ticker", "accession_number", "section_type", "claim_id", "origin", "mention_text",
             "model_entity_type", "entity_type", "also_entity_types", "match_status", "entity_id",
             "entity_name", "entity_source", "quote"]
SAMPLE_COLS = LINK_COLS + ["correct", "note"]


def model_mentions(record: dict) -> list[dict]:
    """The mentions as the model returned them (older claims.jsonl have no origin field)."""
    return [{k: m[k] for k in MODEL_FIELDS} for m in record["entity_mentions"]
            if m.get("origin", "model") == "model"]


def measure(claims: list[dict], units: dict[str, dict], catalog: list[dict]) -> tuple[dict, list[dict]]:
    names = {e["entity_id"]: e for e in catalog}
    before = Counter(m["match_status"] for r in claims for m in r["entity_mentions"])
    before_claims = sum(bool(r.get("related_subsidiary_ids") or r.get("related_segment_ids")) for r in claims)
    after, by_origin, by_type = Counter(), Counter(), Counter()
    per_ticker = defaultdict(Counter)
    entity_claims: dict[str, set] = defaultdict(set)
    links = []
    linked_claims = 0
    for r in claims:
        unit = units[r["claim_id"]]
        out = annotate_mentions(unit["text"], model_mentions(r), issuer_cik=r["provenance"]["cik"],
                                accession=r["accession_number"], catalog=catalog)
        ticker = unit["ticker"]
        if out["related_subsidiary_ids"] or out["related_segment_ids"]:
            linked_claims += 1
            per_ticker[ticker]["claims_linked"] += 1
        per_ticker[ticker]["claims"] += 1
        for m in out["entity_mentions"]:
            after[m["match_status"]] += 1
            by_origin[(m["origin"], m["match_status"])] += 1
            if m["match_status"] not in LINKED:
                continue
            e = names[m["matched_entity_id"]]
            by_type[e["entity_type"]] += 1
            per_ticker[ticker][f"{m['origin']}_links"] += 1
            entity_claims[e["entity_id"]].add(r["claim_id"])
            links.append({"ticker": ticker, "accession_number": r["accession_number"],
                          "section_type": r["section_type"], "claim_id": r["claim_id"],
                          "origin": m["origin"], "mention_text": m["mention_text"],
                          "model_entity_type": m.get("model_entity_type") or "",
                          "entity_type": m["entity_type"],
                          "also_entity_types": ";".join(m.get("also_entity_types") or []),
                          "match_status": m["match_status"], "entity_id": e["entity_id"],
                          "entity_name": e["name"], "entity_source": e.get("source", ""),
                          "quote": unit["text"]})
    top = sorted(entity_claims.items(), key=lambda kv: -len(kv[1]))
    report = {
        "annotation_version": VERSION, "claims": len(claims),
        "before": {"mentions": sum(before.values()), "by_status": dict(before.most_common()),
                   "claims_with_a_link": before_claims},
        "after": {"mentions": sum(after.values()), "by_status": dict(after.most_common()),
                  "by_origin_and_status": {f"{o}/{s}": n for (o, s), n in sorted(by_origin.items())},
                  "linked_mentions": len(links), "linked_mentions_by_type": dict(by_type),
                  "claims_with_a_link": linked_claims,
                  "distinct_entities_linked": len(entity_claims),
                  "by_ticker": {t: dict(c) for t, c in sorted(per_ticker.items())},
                  "top_entities": [{"name": names[i]["name"], "type": names[i]["entity_type"],
                                    "claims": len(c)} for i, c in top[:30]]},
    }
    return report, links


def run(run_dir: Path, catalog_file: Path, out: Path | None, sample: int, seed: int) -> dict:
    out = out or run_dir / "output" / "entity_links"
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    validate_catalog(catalog)
    claims = list(read_jsonl(run_dir / "output" / "claims.jsonl"))
    units = {u["unit_id"]: u for u in read_jsonl(run_dir / "output" / "sentences.jsonl")}
    report, links = measure(claims, units, catalog)
    out.mkdir(parents=True, exist_ok=True)
    (out / "entity_link_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
    write_csv(out / "entity_links.csv", links, LINK_COLS)
    picked = random.Random(seed).sample(links, min(sample, len(links)))
    write_csv(out / "entity_link_sample.csv", [{**p, "correct": "", "note": ""} for p in picked], SAMPLE_COLS)
    a = report["after"]
    log(STEP, f"{report['claims']} Claims: links {report['before']['claims_with_a_link']} -> "
              f"{a['claims_with_a_link']} Claims ({a['linked_mentions']} mentions, "
              f"{a['distinct_entities_linked']} entities) -> {out}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, default=None, help="run folder (default: data/)")
    parser.add_argument("--catalog", type=Path, default=None, help="default: data/entity_catalog.json")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--sample", type=int, default=50)
    parser.add_argument("--seed", type=int, default=91)
    a = parser.parse_args()
    run(a.run or data_dir(), a.catalog or data_dir() / "entity_catalog.json", a.out, a.sample, a.seed)
