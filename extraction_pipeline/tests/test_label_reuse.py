"""Step 7 label reuse (label.reuse_repeats: label) on two filings of one company: the second is
the fixture filing again under another accession, so every unit repeats the first filing."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "sections" / "TEST_0000000000-25-000001.jsonl"
FIRST, SECOND = "0000000000-25-000001", "0000000000-26-000002"

from src import build_claims, clean, filter_sections, label, load, segment, validate  # noqa: E402
from src.common import Paths, read_jsonl  # noqa: E402


class FakeOpenAI:
    calls: list[dict] = []
    reply = None

    def __init__(self, **kwargs):
        self.chat = self.completions = self

    def with_options(self, **kwargs):
        return self

    def create(self, **request):
        FakeOpenAI.calls.append(request)
        payload = json.loads(request["messages"][1]["content"])
        content = {"results": FakeOpenAI.reply(payload)}
        raw = {"id": "fake", "model": "fake-model",
               "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "refusal": None,
                                                                 "content": json.dumps(content)}}],
               "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                         "prompt_tokens_details": {"cached_tokens": 0}}}
        return types.SimpleNamespace(_request_id="fake", model_dump=lambda mode="json": raw)


def stated_fact(payload: dict) -> list[dict]:
    """Every sentence stated_fact; each cites the sentence before it in the batch, if any."""
    ids = [s["id"] for s in payload["sentences"]]
    return [{"id": sid, "label": "stated_fact", "context_ids": ids[k - 1:k], "subject_scope": None,
             "annotation_note": None, "is_risk_heading": False, "entity_mentions": []}
            for k, sid in enumerate(ids)]


class LabelReuse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_env = {k: os.environ.get(k) for k in ("PIPELINE_DATA_DIR", "PIPELINE_FILINGS")}
        cls.tmp = Path(tempfile.mkdtemp(prefix="label-reuse-"))
        (cls.tmp / "sections").mkdir()
        shutil.copy(FIXTURE, cls.tmp / "sections" / FIXTURE.name)
        text = FIXTURE.read_text(encoding="utf-8").replace(FIRST, SECOND)
        (cls.tmp / "sections" / f"TEST_{SECOND}.jsonl").write_text(text, encoding="utf-8")
        base = {"ticker": "TEST", "cik": "0000000000", "form": "10-K"}
        filings = {"filings": [dict(base, reporting_period_end="2025-09-30", accession=FIRST),
                               dict(base, reporting_period_end="2026-09-30", accession=SECOND)]}
        (cls.tmp / "filings.yaml").write_text(yaml.safe_dump(filings), encoding="utf-8")
        os.environ["PIPELINE_DATA_DIR"] = str(cls.tmp)
        os.environ["PIPELINE_FILINGS"] = str(cls.tmp / "filings.yaml")
        os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
        cls.saved_openai = sys.modules.get("openai")
        sys.modules["openai"] = types.SimpleNamespace(OpenAI=FakeOpenAI)
        FakeOpenAI.calls, FakeOpenAI.reply = [], staticmethod(stated_fact)

        clean.run()
        filter_sections.run()
        segment.run()
        label.run()
        cls.units = {u["unit_id"]: u for u in read_jsonl(Paths.sentences())}
        cls.labels = {l["unit_id"]: l for l in read_jsonl(Paths.labels())}
        cls.errors = validate.run()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if cls.saved_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = cls.saved_openai

    def test_only_the_first_filing_is_sent(self):
        sent = {s["text"] for r in FakeOpenAI.calls
                for s in json.loads(r["messages"][1]["content"])["sentences"]}
        first = {u["text"] for u in self.units.values() if u["accession_number"] == FIRST and u["prefilter"] is None}
        self.assertEqual(sent, first)
        self.assertTrue(all(json.loads(r["messages"][1]["content"])["filing"] == "TEST 10-K" for r in FakeOpenAI.calls))

    def test_every_unit_of_the_second_filing_reuses_its_twin(self):
        twins = {(u["section_key"], u["char_start"]): uid for uid, u in self.units.items()
                 if u["accession_number"] == FIRST}
        second = [u for u in self.units.values() if u["accession_number"] == SECOND and u["prefilter"] is None]
        self.assertTrue(second)
        for u in second:
            row, twin = self.labels[u["unit_id"]], twins[(u["section_key"], u["char_start"])]
            self.assertEqual(row["source"], "llm_reused")
            self.assertEqual(row["reused_from_unit_id"], twin)
            self.assertEqual(row["request_hash"], self.labels[twin]["request_hash"])
            # context moved to the second filing's own units, at the same place
            self.assertEqual([self.units[c]["char_start"] for c in row["context_unit_ids"]],
                             [self.units[c]["char_start"] for c in self.labels[twin]["context_unit_ids"]])
            self.assertTrue(all(self.units[c]["accession_number"] == SECOND for c in row["context_unit_ids"]))

    def test_selected_filing_with_an_uncached_donor_is_labelled(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(FakeOpenAI, "calls", []):
            root = Path(folder)
            shutil.copytree(self.tmp / "sections", root / "sections")
            with patch.dict(os.environ, {"PIPELINE_DATA_DIR": folder}):
                clean.run()
                filter_sections.run()
                segment.run()
                label.run(filing=SECOND)
                targets = {uid for uid, u in self.units.items()
                           if u["accession_number"] == SECOND and u["prefilter"] is None}
                direct = {row["unit_id"] for row in read_jsonl(Paths.labels()) if row["source"] == "llm"}
                self.assertEqual(direct, targets)
                self.assertTrue(FakeOpenAI.calls)

    def test_list_intro_and_form_must_match_before_copying(self):
        units = []
        for acc in (FIRST, SECOND):
            for i, text in enumerate(["Earlier risks include:", "One", "Two", "Three", "Four", "Target item"]):
                units.append({"unit_id": f"{acc}/{i}", "ticker": "TEST", "form": "10-K",
                              "section_key": "risk", "section_type": "risk_factors", "section_path": "Item 1A",
                              "accession_number": acc, "role": "body", "text": text, "char_start": i,
                              "prefilter": None, "list_intro_id": f"{acc}/0" if i == 5 else None})
        target = units[-1]["unit_id"]
        cfg = label.settings()["label"]
        self.assertIn(target, label.reuse_plan(units, cfg))
        units[6]["text"] = "Current operations include:"
        self.assertNotIn(target, label.reuse_plan(units, cfg))
        units[6]["text"] = units[0]["text"]
        for unit in units[6:]:
            unit["form"] = "10-Q"
        self.assertNotIn(target, label.reuse_plan(units, cfg))

    def test_some_reused_labels_cite_context(self):
        reused = [l for l in self.labels.values() if l["source"] == "llm_reused"]
        self.assertTrue(any(l["context_unit_ids"] for l in reused))

    def test_every_unit_labelled_once_and_valid(self):
        self.assertEqual(set(self.labels), set(self.units))
        self.assertEqual(self.errors, 0)

    def test_context_that_cannot_be_moved_falls_back(self):
        units = list(self.units.values())
        reuse = label.reuse_plan(units, label.settings()["label"])
        uid, first = next(iter(reuse.items()))
        firsts = [dict(self.labels[first])]
        far = next(u["unit_id"] for u in units if u["accession_number"] == FIRST
                   and u["section_key"] == self.units[first]["section_key"]
                   and u["char_start"] > self.units[first]["char_start"])        # a unit after it
        firsts[0]["context_unit_ids"] = [far]
        copies, fallback = label.reused_labels(firsts, {uid: first}, units)
        self.assertEqual((copies, fallback), ([], {uid}))
        held = [dict(self.labels[first], source="code_quarantine")]
        self.assertEqual(label.reused_labels(held, {uid: first}, units), ([], {uid}))
        self.assertEqual(label.reused_labels([], {uid: first}, units), ([], set()))

    def test_reused_labels_become_claims_and_load_rows(self):
        """A unit that was not sent still ends up in the graph, with its first occurrence's label."""
        build_claims.run()
        claims = {c["claim_id"]: c for c in read_jsonl(Paths.claims())}
        rows = {r["claim_id"]: r for r in load.build_rows()["claims"]}
        held = validate.held_ids()
        reused = [l for l in self.labels.values() if l["source"] == "llm_reused"
                  and l["label"] in build_claims.CLAIM_TYPES and l["unit_id"] not in held]
        self.assertTrue(reused)
        for l in reused:
            first = self.labels[l["reused_from_unit_id"]]
            for row in (claims.get(l["unit_id"]), rows.get(l["unit_id"])):
                self.assertIsNotNone(row, l["unit_id"])
                self.assertEqual(row["claim_type"], first["label"])
                self.assertEqual(row["accession_number"], SECOND)
                self.assertEqual(row["section_id"], self.units[l["unit_id"]]["section_id"])
            self.assertEqual(claims[l["unit_id"]]["provenance"]["context_unit_ids"], l["context_unit_ids"])
        per_filing = {acc: sum(r["accession_number"] == acc for r in rows.values()) for acc in (FIRST, SECOND)}
        self.assertEqual(per_filing[FIRST], per_filing[SECOND])


if __name__ == "__main__":
    unittest.main()
