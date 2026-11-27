"""Step 9b Claim links: exact, edit and semantic layers (src/claim_links.py)."""

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src import claim_links as cl

A, B = "0000000001", "0000000002"


def claim(cid, quote, cik, acc, period):
    return {"claim_id": cid, "quote": quote, "accession_number": acc, "section_type": "risk_factors",
            "provenance": {"cik": cik, "reporting_period_end": period}}


RISK = "Our business could be harmed by changes in global economic conditions and trade policy."


def claims():
    return [
        claim("a21", RISK, A, "A-21", "2021-12-31"),
        claim("a22", RISK, A, "A-22", "2022-12-31"),
        claim("a23", RISK.replace("could", "may"), A, "A-23", "2023-12-31"),    # one token changed
        claim("a23b", RISK, A, "A-23", "2023-12-31"),                         # same filing as a23
        claim("b22", RISK, B, "B-22", "2022-06-30"),                           # another company
        claim("a24", "Revenue rose 5% in the year.", A, "A-24", "2024-12-31"),
    ]


class Layers(unittest.TestCase):
    def test_exact_chain_and_first_across_companies(self):
        es = cl.entries(claims())
        got = {(l["from_claim_id"], l["to_claim_id"], l["same_company"]) for l in cl.exact_links(es)}
        self.assertEqual(got, {("a22", "a21", True), ("a23b", "a22", True), ("b22", "a21", False)})

    def test_edit_keeps_the_latest_earlier_filing_of_each_company(self):
        es = cl.entries(claims())
        cfg = {"min_similarity": 0.9, "report_min": 0.8, "min_tokens": 6, "max_bucket": 500}
        links, scores = cl.edit_pairs(es, cfg)
        got = {(l["from_claim_id"], l["to_claim_id"]) for l in links}
        # a23 vs A: a22 (latest earlier A filing; a23b is the same filing); vs B: b22
        self.assertEqual(got, {("a23", "a22"), ("a23", "b22")})
        one = next(l for l in links if l["to_claim_id"] == "a22")
        self.assertTrue(one["single_token"])
        self.assertEqual(one["changes"], [{"operation": "replace", "left": "could", "right": "may"}])
        self.assertTrue(scores)

    def test_too_common_anchor_is_skipped(self):
        es = cl.entries(claims())
        links, _ = cl.edit_pairs(es, {"min_similarity": 0.9, "report_min": 0.8, "min_tokens": 6, "max_bucket": 0})
        self.assertEqual(links, [])


class Semantic(unittest.TestCase):
    def test_embeddings_cached_and_neighbours_linked(self):
        vectors = {RISK: [1.0, 0.0], RISK.replace("could", "may"): [0.99, 0.1],
                   "Revenue rose 5% in the year.": [0.0, 1.0]}
        calls = []

        class Fake:
            class embeddings:
                @staticmethod
                def create(model, input):
                    calls.append(list(input))
                    data = [types.SimpleNamespace(index=i, embedding=vectors[t]) for i, t in enumerate(input)]
                    return types.SimpleNamespace(data=data, usage=types.SimpleNamespace(total_tokens=10 * len(input)))

        cfg = {"model": "fake", "price_per_1m": 0.02, "budget_usd": 1.0, "batch": 2, "top_k": 3,
               "min_score": 0.9, "report_min": 0.5, "base_url": "x"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PIPELINE_DATA_DIR": tmp}):
            es = cl.entries(claims())
            emb = cl.Embeddings(cfg, Fake())
            emb.fetch([e["quote"] for e in es])
            self.assertEqual(sum(len(c) for c in calls), 3)                    # distinct texts only
            again = cl.Embeddings(cfg, Fake())
            self.assertEqual(again.missing([e["quote"] for e in es]), [])       # read back from disk
            taken = {(l["from_claim_id"], l["to_claim_id"]) for l in cl.exact_links(es)}
            links, _ = cl.semantic_links(es, cfg, taken, again)
            pairs = {(l["from_claim_id"], l["to_claim_id"]) for l in links}
            self.assertNotIn(("a22", "a21"), pairs)                              # already SAME_TEXT
            self.assertIn(("a23", "a22"), pairs)
            self.assertFalse(any("a24" in p for p in pairs))
            self.assertTrue(all(l["from_accession"] != l["to_accession"] for l in links))
            self.assertTrue(Path(tmp, "output", "usage_claim_links.jsonl").exists())


class Load(unittest.TestCase):
    def test_layers_load_only_when_switched_on(self):
        from src import load
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PIPELINE_DATA_DIR": tmp}):
            es = cl.entries(claims())
            from src.common import Paths, write_jsonl
            Paths.claim_links().parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(Paths.claim_links(), cl.exact_links(es))
            self.assertEqual(sum(map(len, load.claim_link_rows().values())), 0)   # default: all off
            real = load.settings

            def on():
                s = real()
                s["load"] = dict(s["load"], claim_links={"exact": True, "edit": False, "semantic": False})
                return s
            with patch.object(load, "settings", on):
                rows = load.claim_link_rows()
            self.assertEqual(len(rows["exact"]), 3)
            self.assertIn("same_company", rows["exact"][0]["props"])


if __name__ == "__main__":
    unittest.main()
