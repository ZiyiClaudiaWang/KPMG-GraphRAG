"""Tests for step 1 (fetch): the export checks. No database needed.

Run from the pipeline folder:  python -m unittest tests.test_fetch
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src import fetch

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STEM = "TEST_0000000000-25-000001"


class TestCheckExports(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fetch-test-"))
        self.saved = {k: os.environ.get(k) for k in ("PIPELINE_DATA_DIR", "PIPELINE_FILINGS")}
        os.environ["PIPELINE_DATA_DIR"] = str(self.tmp)
        os.environ["PIPELINE_FILINGS"] = str(FIXTURES / "filings.yaml")
        shutil.copytree(FIXTURES / "sections", self.tmp / "sections")
        self.path = self.tmp / "sections" / f"{STEM}.jsonl"

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_status(self, value, only_first=False):
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        for i, r in enumerate(rows):
            if not only_first or i == 0:
                r["section_validation_status"] = value
        self.path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_fixture_passes(self):
        self.assertTrue(fetch.check_exports())

    def test_missing_file_fails(self):
        self.path.unlink()
        self.assertFalse(fetch.check_exports())

    def test_empty_file_fails_without_crashing(self):
        self.path.write_text("", encoding="utf-8")
        self.assertFalse(fetch.check_exports())

    def test_unverified_section_is_only_a_note(self):
        self.set_status("unverified", only_first=True)
        self.assertTrue(fetch.check_exports())

    def test_unknown_status_fails(self):
        self.set_status("failed", only_first=True)
        self.assertFalse(fetch.check_exports())


if __name__ == "__main__":
    unittest.main()