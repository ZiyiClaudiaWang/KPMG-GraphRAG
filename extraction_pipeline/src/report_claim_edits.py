"""Report one-token edits between filings. Run from extraction_pipeline with
python -m src.report_claim_edits claims.jsonl --output edits.jsonl
"""
import argparse
from collections import defaultdict
from pathlib import Path
import json
import re

from .common import read_jsonl


def edit(left, right):
    """Return changed tokens for one insertion, deletion or replacement."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return None
    start = 0
    while start < min(len(left), len(right)) and left[start] == right[start]:
        start += 1
    a, b = len(left), len(right)
    while a > start and b > start and left[a - 1] == right[b - 1]:
        a -= 1
        b -= 1
    if max(a - start, b - start) == 1:
        return {"left": left[start:a], "right": right[start:b]}


def pairs(claims):
    index = defaultdict(list)
    entries = []
    for claim in claims:
        prov = claim["provenance"]
        cik = str(prov.get("cik") or "").strip()
        accession = claim.get("accession_number")
        section = claim.get("section_type")
        tokens = re.findall(r"\w+|[^\w\s]", claim["quote"])
        if not (cik.isascii() and cik.isdecimal() and int(cik) and accession and section):
            continue
        if sum(t.isalnum() for t in tokens) < 6:
            continue
        # A single edit leaves at least one three-token end intact.
        keys = [(int(cik), section, side, tuple(end)) for side, end in
                (("prefix", tokens[:3]), ("suffix", tokens[-3:]))]
        source = {k: claim[k] for k in ("claim_id", "accession_number", "quote", "section_id")}
        source.update(period_end=prov.get("reporting_period_end"), source_url=prov.get("source_url"))
        for i in sorted({i for key in keys for i in index[key]}):
            previous, other = entries[i]
            if other["accession_number"] == accession:
                continue
            change = edit(previous, tokens)
            if change:
                yield {"cik": cik.zfill(10), "section_type": section, "edit": change,
                       "left": other, "right": source}
        for key in keys:
            index[key].append(len(entries))
        entries.append((tokens, source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claims", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.claims.resolve() == args.output.resolve():
        parser.error("choose an output distinct from the input")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for row in pairs(read_jsonl(args.claims)):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
