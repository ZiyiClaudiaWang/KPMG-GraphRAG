"""Tests for the pipeline, on a small synthetic filing. No API key or database needed.

Run from the pipeline folder:  python -m unittest discover tests
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

# Point the pipeline at a temporary data folder and the fixture filing before importing it.
TMP = Path(tempfile.mkdtemp(prefix="pipeline-test-"))
os.environ["PIPELINE_DATA_DIR"] = str(TMP)
os.environ["PIPELINE_FILINGS"] = str(FIXTURES / "filings.yaml")
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
shutil.copytree(FIXTURES / "sections", TMP / "sections")

from src import build_claims, clean, filter_sections, label, load, risk_factors, segment, validate  # noqa: E402
from src.clean import ONE_FOR_ONE  # noqa: E402
from src.common import Paths, read_jsonl  # noqa: E402
from src.segment import split_sentences  # noqa: E402
from src.validate import check_reply  # noqa: E402


class FakeOpenAI:
    """Stands in for openai.OpenAI. reply(payload, request) returns the reply JSON for one request:
    a list is wrapped as {"results": [...]}, a dict is sent as is."""
    reply = None
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.chat = self.completions = self

    def with_options(self, **kwargs):
        return self

    def create(self, **request):
        FakeOpenAI.calls.append(request)
        content = FakeOpenAI.reply(json.loads(request["messages"][1]["content"]), request)
        if isinstance(content, list):
            content = {"results": content}
        return FakeResponse({"id": "fake", "model": "fake-model",
                             "choices": [{"finish_reason": "stop", "message": {
                                 "role": "assistant", "refusal": None, "content": json.dumps(content)}}],
                             "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                                       "prompt_tokens_details": {"cached_tokens": 0}}})


class FakeResponse:
    _request_id = "fake-request"

    def __init__(self, raw):
        self.raw = raw

    def model_dump(self, mode="json"):
        return self.raw


def use_fake_openai(reply):
    """Replace the openai package for label.py; works without the package installed."""
    import sys
    import types
    FakeOpenAI.reply, FakeOpenAI.calls = staticmethod(reply), []
    original = sys.modules.get("openai")
    sys.modules["openai"] = types.SimpleNamespace(OpenAI=FakeOpenAI)

    def restore():
        if original is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original
    return restore


def texts(spans, text):
    return [text[s:e] for s, e in spans]


class SentenceSplitting(unittest.TestCase):
    def test_abbreviations_do_not_split(self):
        text = "Sales in the U.S. rose. Costs fell, e.g. freight. Ask the D.C. Circuit. Apple Inc. agreed."
        self.assertEqual(texts(split_sentences(text), text),
                         ["Sales in the U.S. rose.", "Costs fell, e.g. freight.", "Ask the D.C. Circuit.",
                          "Apple Inc. agreed."])

    def test_abbreviation_before_a_new_sentence(self):
        text = "Most sales are outside the U.S. The Company expects this to continue."
        self.assertEqual(len(split_sentences(text)), 2)


class CheckReply(unittest.TestCase):
    batch = {"local_ids": ["s0002", "s0003"], "context_local_ids": ["s0001"]}

    def result(self, sid, ctx=()):
        return {"id": sid, "label": "stated_fact", "context_ids": list(ctx)}

    def test_good_reply(self):
        self.assertEqual(check_reply(self.batch, [self.result("s0002", ["s0001"]), self.result("s0003")]), [])

    def test_context_order_and_duplicates(self):
        batch = {"local_ids": ["s0003"], "context_local_ids": ["s0001", "s0002"]}
        problems = " ".join(check_reply(batch, [self.result("s0003", ["s0002", "s0001", "s0001"])]))
        self.assertIn("duplicate context ids", problems)
        self.assertIn("source order", problems)

    def test_missing_extra_and_later_context(self):
        problems = check_reply(self.batch, [self.result("s0002", ["s0003"]), self.result("s0009")])
        joined = " ".join(problems)
        self.assertIn("missing ids: s0003", joined)
        self.assertIn("unknown ids: s0009", joined)
        self.assertIn("must come before", joined)


class ReviewRules(unittest.TestCase):
    """Step 6 rules (Claudia's source_review and review_entry)."""
    source = {"content": "", "cik": "0000320193", "accession_number": "A", "section_type": "mda",
              "source_locator": None, "processing_set_id": "p"}

    def unit(self, text, sid="A|k|s0002", spans=None):
        return {"sentence_id": sid, "text": text, "raw_spans": spans or [[0, len(text)]],
                "section_type": "mda", "accession_number": "A"}

    def entry(self, unit, label, units=None, neighbours=None, prefixes=()):
        result = {"label": label, "context_ids": [], "subject_scope": None, "annotation_note": None,
                  "is_risk_heading": False, "entity_mentions": []}
        if units:
            result["context_ids"] = [k.rsplit("|", 1)[1] for k in units]
        source = dict(self.source, content=unit["text"])
        return validate.review_entry(unit, result, units or {}, neighbours or [unit], source, [], [], prefixes)

    def test_mixed_tense_held(self):
        got = self.entry(self.unit("Several jurisdictions have passed laws, and others are considering more."),
                         "stated_fact")
        self.assertEqual(got["claim_review_flags"], ["mixed_existing_and_prospective_statement_review"])
        self.assertEqual(got["reason"], validate.HELD_RULE_REASON)

    def test_not_a_claim_never_held(self):
        unit = self.unit("Several jurisdictions have passed laws, and others are considering more.")
        self.assertIsNone(self.entry(unit, "not_a_claim"))

    def test_list_intro_held(self):
        units = {"A|k|s0001": {"text": "Revenue is affected by:"}}
        got = self.entry(self.unit("Growth of the AI PC category."), "stated_fact", units)
        self.assertEqual(got["claim_review_flags"], ["list_intro_dependency_boundary_review"])
        self.assertEqual(got["intro_sentence_ids"], ["A|k|s0001"])

    def test_page_break_held_as_non_verbatim(self):
        unit = self.unit("Growth of the AI PC category.", spans=[[0, 10], [11, 29]])
        self.assertEqual(self.entry(unit, "stated_fact")["reason"], validate.HELD_UNCERTAIN_REASON)

    def test_known_list_fragment(self):
        intro = self.unit("This segment primarily comprises:", "A|k|s0001")
        unit = self.unit("XBOX (formerly Gaming), including XBOX hardware.")
        got = self.entry(unit, "stated_fact", neighbours=[intro, unit], prefixes=("XBOX (formerly Gaming),",))
        self.assertEqual(got["claim_review_flags"], ["known_product_list_fragment_boundary_review"])

    def test_segment_vocabulary_and_entity_review(self):
        from src.source_review import entity_review, source_vocabulary
        text = "The Company's reportable segments consist of the Americas, Europe and Japan."
        source = dict(self.source, content=text, section_type="business")
        vocab = source_vocabulary({("A", "k"): source})
        self.assertEqual(sorted(v["name"] for v in vocab), ["Americas", "Europe", "Japan"])
        unit = dict(self.unit(text), section_type="business")
        mention = {"mention_text": "Mac", "evidence_quote": "Mac", "entity_type": "segment",
                   "reference_kind": "named"}
        flags = entity_review(unit, source, [mention], vocab)["review_flags"]
        self.assertIn("model_entity_quote_not_in_target", flags)
        self.assertIn("model_missed_source_defined_segment_names", flags)


class EndToEnd(unittest.TestCase):
    """Runs every step on the fixture; step 5 uses a fake model instead of OpenAI."""

    @classmethod
    def setUpClass(cls):
        clean.run()
        filter_sections.run()
        segment.run()
        cls.units = list(read_jsonl(Paths.sentences()))

        def reply(payload, request):
            """Labels everything stated_fact. The first reply leaves its last sentence out and
            cites a later sentence as context for its first one."""
            units = {u["sentence_id"].rsplit("|", 1)[1]: u for u in EndToEnd.units}
            row = {"label": "stated_fact", "context_ids": [], "subject_scope": None, "annotation_note": None,
                   "is_risk_heading": False, "entity_mentions": []}
            if "repair_1" in payload:                                  # the targeted repair request
                return {"repairs": {key: row for key in payload}}
            results = [dict(row, id=s["id"], is_risk_heading=bool(units[s["id"]]["rule_heading"]))
                       for s in payload["sentences"]]
            if len(FakeOpenAI.calls) == 1:
                EndToEnd.dropped = results[-1]["id"]
                EndToEnd.bad_context = results[0]["id"]
                results[0]["context_ids"] = [results[1]["id"]]
                results = results[:-1]
            return results

        restore = use_fake_openai(reply)
        try:
            label.run()
            cls.requests = list(FakeOpenAI.calls)
            first = json.loads(cls.requests[0]["messages"][1]["content"])["sentences"][0]["id"]
            cls.requests_first_section = next(u["sentence_id"].rsplit("|", 1)[0] for u in cls.units
                                              if u["sentence_id"].endswith("|" + first)
                                              and u["prefilter"] is None)
            cls.calls = len(cls.requests)
            restore()
            restore = use_fake_openai(lambda *a: cls.fail("cache was not used"))
            label.run()                                     # second run: everything from the cache
            cls.rerun_calls = len(FakeOpenAI.calls)
        finally:
            restore()
        cls.errors = validate.run()
        risk_factors.run()
        build_claims.run()
        load.run(dry_run=True)
        cls.claims = list(read_jsonl(Paths.claims()))

    def test_clean_removes_headers_and_joins_page_breaks(self):
        sections = {s["section_key"]: s for s in read_jsonl(Paths.cleaned({"ticker": "TEST",
                                                                           "accession": "0000000000-25-000001"}))}
        risk = sections["part-i-item-1a"]
        self.assertNotIn("Form 10-K |", risk["text"])
        self.assertIn("Delays may follow and customers may cancel orders.", risk["text"])
        self.assertIn("• suppliers in Asia;", risk["text"])

    def test_every_unit_maps_back_to_the_source(self):
        raw = {r["section_key"]: r["content"] for r in read_jsonl(TMP / "sections" / "TEST_0000000000-25-000001.jsonl")}
        for u in self.units:
            rebuilt = " ".join(raw[u["section_key"]][s:e].translate(ONE_FOR_ONE) for s, e in u["raw_spans"])
            self.assertEqual(rebuilt, u["text"])

    def test_filter_keeps_text_sections_only(self):
        self.assertEqual({u["section_type"] for u in self.units}, {"risk_factors", "mda"})

    def test_segment_roles_and_prefilter(self):
        by_text = {u["text"]: u for u in self.units}
        self.assertEqual(by_text["Industry Risks"]["role"], "category_heading")
        self.assertEqual(by_text["The Company faces intense competition, which could harm its results."]
                         ["rule_heading"], "high")
        self.assertIsNone(by_text["Such pressures may continue for several years."]["rule_heading"])
        self.assertEqual(by_text["See Note 5 for more information."]["prefilter"], "cross_reference")
        groups = [u for u in self.units if u["role"] == "list_group"]
        self.assertEqual(len(groups), 1)
        self.assertIn("• suppliers in Europe.", groups[0]["text"])

    def test_unit_quarantine_and_repair(self):
        self.assertEqual(self.errors, 0)
        labels = {l["sentence_id"].rsplit("|", 1)[1]: l for l in read_jsonl(Paths.labels())
                  if l["sentence_id"].startswith(self.requests_first_section)}
        bad = labels[self.bad_context]                  # invalid context: needs_review, no Claim
        self.assertEqual((bad["label"], bad["source"]), ("needs_review", "code_quarantine"))
        fixed = labels[self.dropped]                    # missing id: answered by the repair request
        self.assertEqual((fixed["label"], fixed["source"]), ("stated_fact", "llm_repair"))
        self.assertIn("repair_1", self.requests[-1]["messages"][1]["content"])
        self.assertEqual(len(list((TMP / "targeted_repair").iterdir())), 1)

    def test_claims_have_ontology_fields(self):
        required = {"claim_id", "quote", "claim_type", "accession_number", "section_id", "extraction_status"}
        for c in self.claims:
            self.assertTrue(required <= set(c))
            self.assertEqual(c["extraction_status"], "needs_review")
        body = next(c for c in self.claims if c["quote"].startswith("Competitors may cut prices."))
        self.assertIsNone(body["risk_factor_id"])               # risk factors not linked in code yet
        self.assertEqual(body["quote"], body["source_fragments"][0]["quote"])
        self.assertEqual(body["provenance"]["section_path"], "Part I Item 1A > Industry Risks")

    def test_claim_ids_are_stable(self):
        first = [c["claim_id"] for c in self.claims]
        build_claims.run()
        self.assertEqual(first, [c["claim_id"] for c in read_jsonl(Paths.claims())])

    def test_load_preview_written(self):
        self.assertIn("MERGE (s)-[x:SUPPORTS", Paths.cypher().read_text(encoding="utf-8"))

    def test_list_group_held_not_a_claim(self):
        held = validate.held_ids()
        group = next(u for u in self.units if u["role"] == "list_group")
        self.assertIn(group["sentence_id"], held)
        self.assertNotIn(group["text"], {c["quote"] for c in self.claims})

    def test_usage_and_cost_logged(self):
        rows = list(read_jsonl(Paths.usage()))
        self.assertEqual(len(rows), self.calls)
        self.assertTrue(all(r["estimated_cost_usd"] is not None for r in rows))

    def test_rerun_uses_cache(self):
        self.assertEqual(self.rerun_calls, 0)
        folders = list((TMP / "checked_pilot").iterdir()) + list((TMP / "targeted_repair").iterdir())
        self.assertEqual(len(folders), self.calls)
        self.assertTrue(all((f / "raw_response.json").exists() for f in folders))


class FailedReply(unittest.TestCase):
    """A reply that breaks the JSON schema is rejected whole."""

    def run_label(self, max_retries):
        tmp = Path(tempfile.mkdtemp(prefix="pipeline-stop-"))
        shutil.copytree(FIXTURES / "sections", tmp / "sections")
        bad = [{"id": "s9999", "label": "maybe"}]                   # off the schema
        restore = use_fake_openai(lambda payload, request: bad)
        original_settings = label.settings
        cfg = dict(original_settings()["label"], max_retries=max_retries)
        label.settings = lambda: {**original_settings(), "label": cfg}
        os.environ["PIPELINE_DATA_DIR"] = str(tmp)
        try:
            clean.run()
            filter_sections.run()
            segment.run()
            label.run()
            return list(read_jsonl(Paths.labels())), list(FakeOpenAI.calls)
        finally:
            restore()
            label.settings = original_settings
            os.environ["PIPELINE_DATA_DIR"] = str(TMP)

    def test_default_stops(self):
        labels, calls = self.run_label(max_retries=0)
        self.assertEqual(len(calls), 1)                       # stopped after the first failed batch
        self.assertTrue(all(l["source"].startswith("rule:") for l in labels))

    def test_retry_sends_follow_up(self):
        labels, calls = self.run_label(max_retries=1)
        self.assertEqual(len(calls), 2)                       # one follow-up, then stop
        self.assertEqual(len(calls[1]["messages"]), 4)        # it lists the problems


if __name__ == "__main__":
    unittest.main()