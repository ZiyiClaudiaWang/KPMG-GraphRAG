"""Report exact Claim text repeated across filings of the same company."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .common import read_jsonl


def build_report(claims: Iterable[dict]) -> dict:
    groups = defaultdict(list)
    total_claims = skipped_claims = 0
    for claim in claims:
        total_claims += 1
        provenance = claim.get("provenance") or {}
        cik = str(provenance.get("cik") or "").strip()
        quote = " ".join((claim.get("quote") or "").split())
        accession = claim.get("accession_number")
        if not cik.isascii() or not cik.isdecimal() or not int(cik) or not quote or not accession:
            skipped_claims += 1
            continue
        groups[(cik.zfill(10), quote)].append({
            "accession_number": accession,
            "section_id": claim["section_id"],
            "claim_id": claim["claim_id"],
            "source_url": provenance.get("source_url"),
        })

    repeats = []
    for (cik, quote), occurrences in sorted(groups.items()):
        filing_count = len({item["accession_number"] for item in occurrences})
        if filing_count < 2:
            continue
        repeats.append({
            "cik": cik,
            "normalized_quote": quote,
            "filing_count": filing_count,
            "occurrences": sorted(occurrences, key=lambda item: (
                item["accession_number"], item["section_id"] or "", item["claim_id"],
            )),
        })

    return {
        "total_claims": total_claims,
        "skipped_claims": skipped_claims,
        "repeat_groups": len(repeats),
        "claims_in_repeat_groups": sum(len(group["occurrences"]) for group in repeats),
        "groups": repeats,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claims", type=Path, help="input claims.jsonl")
    parser.add_argument("--output", required=True, type=Path, help="output JSON report")
    args = parser.parse_args()
    if args.claims.resolve() == args.output.resolve():
        parser.error("the input and output must be different files")
    report = build_report(read_jsonl(args.claims))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['repeat_groups']} groups covering {report['claims_in_repeat_groups']} "
          f"of {report['total_claims']} Claims ({report['skipped_claims']} skipped)")


if __name__ == "__main__":
    main()
