"""Report high-similarity edits between filings. Run from extraction_pipeline with
python -m src.report_claim_edits claims.jsonl --output edits.jsonl
"""
import argparse
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import json
import re

from .common import read_jsonl


def edit(left, right):
    """Keep single-token edits or a token similarity of at least 0.90."""
    if left == right:
        return None
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    if abs(len(left) - len(right)) > 1 and matcher.real_quick_ratio() < 0.90:
        return None
    changes = [{"operation": op, "left": left[i:j], "right": right[k:m],
                "left_start": i, "right_start": k}
               for op, i, j, k, m in matcher.get_opcodes() if op != "equal"]
    single = len(changes) == 1 and max(len(changes[0]["left"]), len(changes[0]["right"])) == 1
    score = matcher.ratio()
    if single or score >= 0.90:
        return {"similarity": score, "changes": changes}


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
        # Candidate filter: a matching prefix, suffix, or interior three-token sequence.
        # Index three anchors per prior quote; probe its interior anchor anywhere.
        keys = [(int(cik), section, side, tuple(end)) for side, end in
                (("prefix", tokens[:3]), ("suffix", tokens[-3:]))]
        source = {k: claim[k] for k in ("claim_id", "accession_number", "quote", "section_id")}
        source.update(period_end=prov.get("reporting_period_end"), source_url=prov.get("source_url"))
        middle = len(tokens) // 2 - 1
        interior = (int(cik), section, "interior", tuple(tokens[middle:middle + 3]))
        probes = set(keys)
        probes.update((int(cik), section, "interior", tuple(tokens[j:j + 3]))
                      for j in range(len(tokens) - 2))
        for i in sorted({i for key in probes for i in index.get(key, ())}):
            previous, other = entries[i]
            if other["accession_number"] == accession:
                continue
            change = edit(previous, tokens)
            if change:
                yield {"cik": cik.zfill(10), "section_type": section, "edit": change,
                       "left": other, "right": source}
        for key in [*keys, interior]:
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
