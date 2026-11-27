"""Step 9b - Claim links: Claims with the same or similar text in other filings, any company.

Input : data/output/claims.jsonl (step 9)
Output: data/output/claim_links.jsonl          one row per link (read by step 10)
        data/checks/claim_links/summary.csv    links per layer, same company or not
        data/checks/claim_links/scores.csv     how many pairs at each score, to choose the thresholds
        data/checks/claim_links/samples.csv    examples of each layer with both quotes
        data/interim/claim_embeddings/<model>/ the semantic layer's embeddings (cache)

Three layers (config/settings.yaml claim_links:), each a relationship type in Neo4j:
  exact     SAME_TEXT   the same quote, whitespace normalised (checks/unit_repeats.norm)
  edit      NEAR_TEXT   not the same, but exactly one token changed or a token similarity of at
                        least edit.min_similarity (difflib, Claudia's #111): "may" -> "could", a
                        year, a company name in a shared sentence
  semantic  SIMILAR_TO  cosine of OpenAI embeddings at least semantic.min_score, the top_k
                        nearest Claims of each Claim (paid; computed only with semantic.compute
                        or --semantic)

Rules for every layer:
  - Two Claims of the same filing are never linked.
  - from_claim_id is the later Claim (reporting_period_end, then accession_number), to_claim_id
    the earlier one; same_company says whether both are the same registrant (CIK).
  - Exact: in one company, each occurrence links to the one before it (a chain over the years);
    across companies, each company's first occurrence links to every earlier company's first.
  - Edit: for each Claim and each earlier company, only the best pair in that company's latest
    earlier filing is kept, so a sentence unchanged for years gives one link, not one per year.
  - Semantic: pairs that are already exact or edit links are left out.

Step 10 loads a layer only when load.claim_links.<layer> is true (all false by default).
Without the LLM except for the embeddings; nothing else reads these files.

  python -m src.claim_links              exact and edit layers (and semantic if compute: true)
  python -m src.claim_links --semantic   also the semantic layer (paid)
  python -m src.claim_links --dry-run    semantic: count texts and estimate cost, no API calls
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

from .checks.unit_repeats import norm
from .common import Paths, append_jsonl, checks_dir, data_dir, load_env, log, read_jsonl, settings, write_jsonl
from .llm.client import digest

STEP = "claim_links"
LAYERS = {"exact": "SAME_TEXT", "edit": "NEAR_TEXT", "semantic": "SIMILAR_TO"}
TOKEN = re.compile(r"\w+|[^\w\s]")


# ---------- claims ----------

def entries(claims) -> list[dict]:
    """One entry per claim_id, oldest filing first: what the layers compare."""
    seen, out = set(), []
    for c in claims:
        if c["claim_id"] in seen:
            continue
        seen.add(c["claim_id"])
        prov = c.get("provenance") or {}
        out.append({"claim_id": c["claim_id"], "quote": norm(c.get("quote")),
                    "cik": str(prov.get("cik") or "").zfill(10), "accession": c["accession_number"],
                    "period": prov.get("reporting_period_end") or "", "section_type": c.get("section_type")})
    out.sort(key=lambda e: (e["period"], e["accession"]))
    for k, e in enumerate(out):
        e["order"] = k
    return out


def filing_order(es: list[dict]) -> dict[str, int]:
    """accession -> position of the filing in time (earlier = smaller)."""
    order = {}
    for e in es:
        order.setdefault(e["accession"], len(order))
    return order


def link(layer: str, later: dict, earlier: dict, **props) -> dict:
    return {"layer": layer, "relationship": LAYERS[layer],
            "from_claim_id": later["claim_id"], "to_claim_id": earlier["claim_id"],
            "same_company": later["cik"] == earlier["cik"],
            "from_accession": later["accession"], "to_accession": earlier["accession"], **props}


# ---------- exact ----------

def exact_links(es: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in es:
        if e["quote"]:
            groups[e["quote"]].append(e)
    out = []
    for members in groups.values():
        if len({m["accession"] for m in members}) < 2:
            continue
        firsts: dict[str, dict] = {}                 # cik -> first occurrence
        last: dict[str, dict] = {}                   # cik -> latest occurrence so far
        for m in members:                            # already in time order
            prev = last.get(m["cik"])
            if prev is not None and prev["accession"] != m["accession"]:
                out.append(link("exact", m, prev))
            if prev is None:
                for cik, first in firsts.items():    # first in this company: link earlier companies
                    out.append(link("exact", m, first))
                firsts[m["cik"]] = m
            if prev is None or prev["accession"] != m["accession"]:
                last[m["cik"]] = m
    return out


# ---------- edit (#111) ----------

def edit(left: list[str], right: list[str], floor: float) -> dict | None:
    """Token similarity and the changed spans, or None below floor (one changed token always passes)."""
    if left == right:
        return None
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    if matcher.real_quick_ratio() < floor and abs(len(left) - len(right)) > 1:
        return None
    if matcher.quick_ratio() < floor and abs(len(left) - len(right)) > 1:
        return None
    changes = [{"operation": op, "left": " ".join(left[i:j]), "right": " ".join(right[k:m])}
               for op, i, j, k, m in matcher.get_opcodes() if op != "equal"]
    single = len(changes) == 1 and max(j - i for op, i, j, k, m in matcher.get_opcodes() if op != "equal") <= 1 \
        and max(m - k for op, i, j, k, m in matcher.get_opcodes() if op != "equal") <= 1
    score = matcher.ratio()
    if single or score >= floor:
        return {"similarity": round(score, 4), "single_token": single, "changes": changes}
    return None


def edit_pairs(es: list[dict], cfg: dict) -> tuple[list[dict], Counter]:
    """NEAR_TEXT links and the count of compared pairs per score bucket (for scores.csv).

    Candidates share a 3-token anchor: an earlier Claim's first, last or middle three tokens,
    found anywhere in the later one. An anchor shared by more than max_bucket earlier Claims is
    boilerplate ("the Company 's"), too common to narrow anything, and is skipped."""
    floor, keep = cfg["report_min"], cfg["min_similarity"]
    order = filing_order(es)
    index: dict[tuple, list[int]] = defaultdict(list)
    tokens: list[list[str]] = []
    scores: Counter = Counter()
    best: dict[tuple, tuple] = {}                      # (later claim, earlier cik) -> (key, link)
    for k, e in enumerate(es):
        toks = TOKEN.findall(e["quote"])
        tokens.append(toks)
        if sum(t.isalnum() for t in toks) < cfg["min_tokens"]:
            continue
        probes = {tuple(toks[j:j + 3]) for j in range(len(toks) - 2)}
        candidates = set()
        for p in probes:
            bucket = index.get(p)
            if bucket and len(bucket) <= cfg["max_bucket"]:
                candidates.update(bucket)
        for i in candidates:
            other = es[i]
            if other["accession"] == e["accession"] or other["quote"] == e["quote"]:
                continue
            if min(len(toks), len(tokens[i])) / max(len(toks), len(tokens[i])) < floor - 0.05:
                continue
            change = edit(tokens[i], toks, floor)
            if change is None:
                continue
            scores[("edit", min(int(change["similarity"] * 100), 99), other["cik"] == e["cik"])] += 1
            if not (change["single_token"] or change["similarity"] >= keep):
                continue
            key = (order[other["accession"]], change["similarity"])     # latest filing, then best
            slot = (e["claim_id"], other["cik"])
            if slot not in best or key > best[slot][0]:
                best[slot] = (key, link("edit", e, other, **change))
        mid = len(toks) // 2 - 1
        for anchor in {tuple(toks[:3]), tuple(toks[-3:]), tuple(toks[mid:mid + 3])}:
            index[anchor].append(k)
    return [v[1] for v in best.values()], scores


# ---------- semantic ----------

class Embeddings:
    """Embeddings of quotes, cached by sha256(text) in data/interim/claim_embeddings/<model>/."""

    def __init__(self, cfg: dict, client=None):
        self.cfg, self.client = cfg, client
        self.folder = data_dir() / "interim" / "claim_embeddings" / cfg["model"]
        self.keys: list[str] = []
        self.vectors = None
        if (self.folder / "keys.json").exists():
            import numpy as np
            self.keys = json.loads((self.folder / "keys.json").read_text(encoding="utf-8"))
            self.vectors = np.load(self.folder / "vectors.npy")

    def missing(self, texts: list[str]) -> list[str]:
        have = set(self.keys)
        out, seen = [], set()
        for t in texts:
            h = digest(t)
            if h not in have and h not in seen:
                seen.add(h)
                out.append(t)
        return out

    @staticmethod
    def tokens(texts: list[str]) -> int:
        return sum(len(t) for t in texts) // 4 + len(texts)        # about 4 characters a token

    def fetch(self, texts: list[str]) -> None:
        """Embed texts not cached yet, batch by batch; stop before budget_usd would be passed."""
        import numpy as np
        todo = self.missing(texts)
        if not todo:
            return
        if self.client is None:
            import openai
            load_env()
            self.client = openai.OpenAI(base_url=self.cfg["base_url"])
        spent = sum(r.get("estimated_cost_usd") or 0 for r in read_jsonl(Paths.usage_claim_links())) \
            if Paths.usage_claim_links().exists() else 0.0
        price = self.cfg["price_per_1m"]
        for start in range(0, len(todo), self.cfg["batch"]):
            batch = todo[start:start + self.cfg["batch"]]
            if spent + self.tokens(batch) * price / 1e6 > self.cfg["budget_usd"]:
                self.save()
                raise SystemExit(f"[{STEP}] budget of ${self.cfg['budget_usd']} reached "
                                 f"(spent about ${spent:.4f}); embeddings so far are kept")
            reply = self.client.embeddings.create(model=self.cfg["model"], input=batch)
            used = reply.usage.total_tokens
            spent += used * price / 1e6
            append_jsonl(Paths.usage_claim_links(), {
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": self.cfg["model"],
                "texts": len(batch), "total_tokens": used, "estimated_cost_usd": used * price / 1e6})
            new = np.array([d.embedding for d in sorted(reply.data, key=lambda d: d.index)], dtype=np.float32)
            self.keys += [digest(t) for t in batch]
            self.vectors = new if self.vectors is None else np.vstack([self.vectors, new])
        self.save()

    def save(self) -> None:
        import numpy as np
        if self.vectors is None:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        np.save(self.folder / "vectors.npy", self.vectors)
        (self.folder / "keys.json").write_text(json.dumps(self.keys), encoding="utf-8")

    def matrix(self, texts: list[str]):
        import numpy as np
        row = {k: i for i, k in enumerate(self.keys)}
        m = self.vectors[[row[digest(t)] for t in texts]]
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)


def semantic_links(es: list[dict], cfg: dict, taken: set[tuple], emb: Embeddings) -> tuple[list[dict], Counter]:
    """SIMILAR_TO: the top_k nearest Claims of each Claim in other filings, cosine >= min_score."""
    import numpy as np
    es = [e for e in es if e["quote"]]
    if len(es) < 2:
        return [], Counter()
    m = emb.matrix([e["quote"] for e in es])
    accession = np.array([e["accession"] for e in es])
    scores, out, done = Counter(), [], set()
    k = cfg["top_k"]
    for start in range(0, len(es), 1024):
        sims = m[start:start + 1024] @ m.T
        for r in range(sims.shape[0]):
            i = start + r
            row = sims[r]
            row[accession == accession[i]] = -1                      # same filing: never
            top = np.argpartition(-row, min(k, len(row) - 1))[:k]
            for j in top[np.argsort(-row[top])]:
                s = float(row[j])
                if s < cfg["report_min"]:
                    continue
                later, earlier = (es[i], es[j]) if es[i]["order"] > es[j]["order"] else (es[j], es[i])
                pair = (later["claim_id"], earlier["claim_id"])
                if pair in done:
                    continue
                done.add(pair)
                scores[("semantic", min(int(s * 100), 99), later["cik"] == earlier["cik"])] += 1
                if s >= cfg["min_score"] and pair not in taken:
                    out.append(link("semantic", later, earlier, score=round(s, 4), model=cfg["model"]))
    return out, scores


def semantic_cfg() -> dict:
    s = settings()
    cfg = dict(s["claim_links"]["semantic"])
    cfg.setdefault("base_url", s["label"]["base_url"])
    return cfg


# ---------- checks ----------

def write_csv(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["empty"])
        w.writeheader()
        w.writerows(rows)


def write_checks(links: list[dict], scores: Counter, es: list[dict]) -> None:
    folder = checks_dir("claim_links")
    count = Counter((l["layer"], l["same_company"]) for l in links)
    write_csv(folder / "summary.csv", [{"layer": layer, "relationship": LAYERS[layer], "same_company": same,
                                        "links": count[(layer, same)]}
                                       for layer in LAYERS for same in (True, False)])
    write_csv(folder / "scores.csv", [{"layer": layer, "score_from": f"{b / 100:.2f}", "same_company": same,
                                       "pairs": n} for (layer, b, same), n in sorted(scores.items())])
    quote = {e["claim_id"]: e["quote"] for e in es}
    rng, rows = random.Random(0), []
    for layer in LAYERS:
        mine = [l for l in links if l["layer"] == layer]
        for l in rng.sample(mine, min(40, len(mine))):
            rows.append({"layer": layer, "same_company": l["same_company"],
                         "score": l.get("similarity", l.get("score", 1.0)),
                         "from_accession": l["from_accession"], "to_accession": l["to_accession"],
                         "from_quote": quote[l["from_claim_id"]][:300], "to_quote": quote[l["to_claim_id"]][:300]})
    write_csv(folder / "samples.csv", rows)


# ---------- run ----------

def run(semantic: bool | None = None, dry_run: bool = False, client=None) -> None:
    cfg = settings()["claim_links"]
    if not Paths.claims().exists():
        log(STEP, f"no {Paths.claims()}: run step 9 first")
        return
    es = entries(read_jsonl(Paths.claims()))
    links = exact_links(es)
    found, scores = edit_pairs(es, cfg["edit"])
    links += found
    log(STEP, f"{len(es)} Claims: {sum(l['layer'] == 'exact' for l in links)} SAME_TEXT, "
              f"{len(found)} NEAR_TEXT links")
    want = cfg["semantic"].get("compute") if semantic is None else semantic
    if want:
        scfg = semantic_cfg()
        emb = Embeddings(scfg, client)
        todo = emb.missing([e["quote"] for e in es if e["quote"]])
        cost = emb.tokens(todo) * scfg["price_per_1m"] / 1e6
        log(STEP, f"semantic: {len(todo)} texts to embed with {scfg['model']}, about ${cost:.2f}")
        if dry_run:
            log(STEP, "dry run: no embeddings requested; semantic layer not built")
        else:
            emb.fetch([e["quote"] for e in es if e["quote"]])
            taken = {(l["from_claim_id"], l["to_claim_id"]) for l in links}
            more, s = semantic_links(es, scfg, taken, emb)
            links += more
            scores += s
            log(STEP, f"semantic: {len(more)} SIMILAR_TO links (top {scfg['top_k']}, score >= {scfg['min_score']})")
    write_jsonl(Paths.claim_links(), links)
    write_checks(links, scores, es)
    on = [k for k, v in (settings()["load"].get("claim_links") or {}).items() if v]
    log(STEP, f"-> {Paths.claim_links()}; step 10 loads: {', '.join(on) or 'none (load.claim_links all false)'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--semantic", action="store_true", help="also build the semantic layer (paid)")
    parser.add_argument("--dry-run", action="store_true", help="semantic: estimate only, no API calls")
    args = parser.parse_args()
    run(semantic=True if args.semantic else None, dry_run=args.dry_run)
