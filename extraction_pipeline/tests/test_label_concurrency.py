"""Step 5 with several requests in flight (--workers): same labels as one at a time, a 429 is
waited out and not left as an uncertain attempt, and the budget counts requests in flight."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "sections" / "TEST_0000000000-25-000001.jsonl"
FIRST, SECOND = "0000000000-25-000001", "0000000000-26-000002"

from src import clean, filter_sections, label, segment  # noqa: E402
from src.common import Paths, read_jsonl  # noqa: E402


class RateLimitError(Exception):
    def __init__(self, message="rate limited", code="rate_limit_exceeded"):
        super().__init__(message)
        self.code = code
        self.response = types.SimpleNamespace(headers={"retry-after": "0"})


class Fake:
    calls: list[dict] = []
    in_flight = peak = 0
    lock = threading.Lock()
    refuse_first = False                       # first request gets one 429

    def __init__(self, **kwargs):
        self.chat = self.completions = self

    def with_options(self, **kwargs):
        return self

    def create(self, **request):
        with Fake.lock:
            Fake.calls.append(request)
            if Fake.refuse_first:
                Fake.refuse_first = False
                raise RateLimitError()
            Fake.in_flight += 1
            Fake.peak = max(Fake.peak, Fake.in_flight)
        time.sleep(0.02)
        with Fake.lock:
            Fake.in_flight -= 1
        payload = json.loads(request["messages"][1]["content"])
        ids = [s["id"] for s in payload["sentences"]]
        rows = [{"id": i, "label": "stated_fact", "context_ids": ids[k - 1:k], "subject_scope": None,
                 "annotation_note": None, "is_risk_heading": False, "entity_mentions": []}
                for k, i in enumerate(ids)]
        raw = {"id": "f", "model": "fake", "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "refusal": None, "content": json.dumps({"results": rows})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                      "prompt_tokens_details": {"cached_tokens": 0}}}
        return types.SimpleNamespace(_request_id="x", model_dump=lambda mode="json": raw)


def setup_data(tmp: Path, reuse: bool) -> None:
    (tmp / "sections").mkdir(parents=True)
    shutil.copy(FIXTURE, tmp / "sections" / FIXTURE.name)
    (tmp / "sections" / f"TEST_{SECOND}.jsonl").write_text(
        FIXTURE.read_text(encoding="utf-8").replace(FIRST, SECOND), encoding="utf-8")
    base = {"ticker": "TEST", "cik": "0000000000", "form": "10-K"}
    (tmp / "filings.yaml").write_text(yaml.safe_dump({"filings": [
        dict(base, reporting_period_end="2025-09-30", accession=FIRST),
        dict(base, reporting_period_end="2026-09-30", accession=SECOND)]}), encoding="utf-8")
    os.environ["PIPELINE_DATA_DIR"] = str(tmp)
    os.environ["PIPELINE_FILINGS"] = str(tmp / "filings.yaml")
    clean.run()
    filter_sections.run()
    segment.run()


class Concurrency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_env = {k: os.environ.get(k) for k in ("PIPELINE_DATA_DIR", "PIPELINE_FILINGS")}
        os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
        cls.saved_openai = sys.modules.get("openai")
        sys.modules["openai"] = types.SimpleNamespace(OpenAI=Fake, RateLimitError=RateLimitError)
        cls.saved_settings = label.settings

        def without_reuse():
            s = dict(cls.saved_settings())                          # settings() is cached: copy it
            s["label"] = dict(s["label"], reuse_repeats=None)       # both filings sent: more batches
            return s
        label.settings = without_reuse

        def one_run(workers, refuse_first=False):
            setup_data(Path(tempfile.mkdtemp(prefix="label-conc-")), reuse=False)
            Fake.calls, Fake.peak, Fake.refuse_first = [], 0, refuse_first
            label.run(workers=workers)
            labels = {l["unit_id"]: (l["label"], tuple(l["context_unit_ids"])) for l in read_jsonl(Paths.labels())}
            folders = list((Paths.labels().parents[1] / "checked_pilot").iterdir())
            return labels, len(Fake.calls), Fake.peak, folders

        cls.sequential = one_run(1)
        cls.parallel = one_run(4)
        cls.limited = one_run(4, refuse_first=True)

    @classmethod
    def tearDownClass(cls):
        label.settings = cls.saved_settings
        for k, v in cls.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if cls.saved_openai is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = cls.saved_openai

    def test_same_labels_as_one_at_a_time(self):
        self.assertEqual(self.parallel[0], self.sequential[0])

    def test_requests_overlap(self):
        self.assertGreater(self.parallel[2], 1)
        self.assertEqual(self.sequential[2], 1)

    def test_identical_requests_sent_once(self):
        # Both filings have the same text and form, so each request comes twice; it is sent once.
        self.assertEqual(self.parallel[1], self.sequential[1])
        self.assertEqual(self.parallel[1], len(self.parallel[3]))

    def test_rate_limit_is_waited_out(self):
        labels, calls, _, folders = self.limited
        self.assertEqual(labels, self.sequential[0])
        self.assertEqual(calls, self.sequential[1] + 1)                 # the refused one, sent again
        self.assertFalse(any((f / "attempt.json").exists() and not (f / "raw_response.json").exists()
                             for f in folders))

    def test_budget_counts_requests_in_flight(self):
        labeller = label.Labeller({"budget_usd": 1.0, "price_input_per_1m": 1.0, "price_output_per_1m": 1.0,
                                   "base_url": "x"}, usage_path=Path(tempfile.mkdtemp()) / "u.jsonl")
        labeller.reserved = 0.999
        plan = {"request": {"max_completion_tokens": 4000, "messages": []}, "request_hash": "h" * 64,
                "batch": {"batch_id": "b"}, "identity": {}}
        with self.assertRaises(label.StopRun):
            labeller.send(plan)


if __name__ == "__main__":
    unittest.main()
