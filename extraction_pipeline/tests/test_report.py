"""The review report (src/report.py): stale steps, settings blocks, changes, unit checks, and a
whole run on the Exhibit 21 fixture. Reads files only, so no network and no API."""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from src import report, subsidiaries
from src.common import Paths, write_jsonl

FIXTURES = Path(__file__).parent / "fixtures" / "ex21"


def unit(i, text, role="body", prefilter=None, section_type="mda", **kw):
    return {"unit_id": f"u{i}", "accession_number": "A", "ticker": "TST", "form": "10-K",
            "section_key": kw.pop("section_key", "s"), "section_type": section_type, "role": role,
            "prefilter": prefilter, "text": text, **kw}


class TempData(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("PIPELINE_DATA_DIR", "PIPELINE_FILINGS",
                                                     "SEC_USER_AGENT", "PIPELINE_SUBSIDIARY_ALIASES")}
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        os.environ["PIPELINE_DATA_DIR"] = str(self.tmp)
        os.environ["PIPELINE_FILINGS"] = str(FIXTURES / "filings.yaml")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def touch(path: Path, when: float) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        os.utime(path, (when, when))
        return path


class TestFreshness(TempData):
    def step(self, settings=None):
        return {"id": "x", "results": lambda: [self.tmp / "out.jsonl"], "inputs": lambda: [self.tmp / "in.jsonl"],
                "settings": settings, "checks": []}

    def test_missing_stale_ok(self):
        now = time.time()
        self.assertEqual(report.freshness(self.step(), None)["status"], "missing")
        self.touch(self.tmp / "in.jsonl", now - 100)
        self.touch(self.tmp / "out.jsonl", now - 50)
        self.assertEqual(report.freshness(self.step(), None)["status"], "ok")
        self.touch(self.tmp / "in.jsonl", now)
        f = report.freshness(self.step(), None)
        self.assertEqual(f["status"], "stale")
        self.assertIn("in.jsonl", f["reason"])

    def test_settings_block_not_the_whole_file(self):
        """Editing another step's block of settings.yaml leaves the step up to date; editing its
        own block makes it stale, and it stays stale until it runs again."""
        now = time.time()
        config = self.tmp / "config"
        blocks = {"segment": {"min_words": 4}, "label": {"model": "a"}}
        real_config, real_settings = report.CONFIG, report.settings
        report.CONFIG, report.settings = config, lambda: blocks
        try:
            self.touch(self.tmp / "in.jsonl", now - 300)
            settings_file = self.touch(config / "settings.yaml", now - 200)
            self.touch(self.tmp / "out.jsonl", now - 100)            # ran after the last edit
            first = report.freshness(self.step(["segment"]), None)
            self.assertEqual(first["status"], "ok")
            last = {"fresh": {"x": first}}

            blocks["label"]["model"] = "b"                          # another step's block
            os.utime(settings_file, (now, now))
            self.assertEqual(report.freshness(self.step(["segment"]), last)["status"], "ok")

            blocks["segment"]["min_words"] = 6                      # its own block
            second = report.freshness(self.step(["segment"]), last)
            self.assertEqual(second["status"], "stale")
            self.assertIn("`segment:`", second["reason"])
            again = report.freshness(self.step(["segment"]), {"fresh": {"x": second}})
            self.assertEqual(again["status"], "stale")              # not forgotten by the next report

            self.touch(self.tmp / "out.jsonl", now + 10)             # the step ran again
            self.assertEqual(report.freshness(self.step(["segment"]), {"fresh": {"x": again}})["status"], "ok")
        finally:
            report.CONFIG, report.settings = real_config, real_settings


class TestChecksAndChanges(unittest.TestCase):
    def test_unit_checks(self):
        units = [unit(1, "and then the sentence goes on."),                   # lowercase_start
                 unit(2, "A sentence cut by a page"),                         # no_end_punct
                 unit(3, "Acquisitions | 47"),                                # single_pipe
                 unit(4, "x" * 120, role="heading", prefilter="heading"),     # long_heading
                 unit(5, "iPhone sales grew."),                               # a brand, not a check
                 unit(6, "the item of a list;", list_intro_id="u0"),          # a list item, not a check
                 unit(7, "A complete sentence.")]
        risk = [unit(10 + i, f"Risk sentence {i}.", section_type="risk_factors", section_key="r")
                for i in range(20)]
        m, _, _, samples = report.unit_checks(units + risk)
        counts = {c: m[c] for c, _ in report.UNIT_CHECKS if m[c]}
        self.assertEqual(counts, {"lowercase_start": 1, "no_end_punct": 1, "single_pipe": 1, "long_heading": 1,
                                  "risk_section_no_category": 1})
        self.assertEqual(m["single_pipe_per_1k"], round(1000 / 26, 2))     # 26 units go to the LLM
        self.assertEqual({s["check"] for s in samples}, set(counts))

    def test_limits_and_big_changes(self):
        """A number over its max fails at its level, one under its min too, a missing one is
        'not run', and a key number that moves more than big_change_pct warns."""
        cfg = {"report": {"big_change_pct": 5, "big_change": ["segment.units"],
                          "checks": {"segment.to_llm": {"max": 50, "level": "error"},
                                     "risk_factors.item_1a_with_theme_pct": {"min": 90},
                                     "headings.filings_without_html": {"max": 0, "level": "error"},
                                     "fetch.exports_missing": {"max": 0, "level": "error"}}}}
        fresh = {s["id"]: {"status": "ok", "written": "", "reason": ""} for s in report.STEPS}
        fresh["clean"] = {"status": "stale", "written": "", "reason": "x is newer"}
        now = {"fresh": fresh, "metrics": {"segment": {"units": 120, "to_llm": 80},
                                           "risk_factors": {"item_1a_with_theme_pct": 79.6},
                                           "fetch": {"exports_missing": 0}}}
        before = {"metrics": {"segment": {"units": 100}}}
        real = report.settings
        report.settings = lambda: cfg
        try:
            got = {c["number"]: c["status"] for c in report.evaluate(now, before)}
        finally:
            report.settings = real
        self.assertEqual(got, {"stale": "error", "segment.units": "warn", "segment.to_llm": "error",
                               "risk_factors.item_1a_with_theme_pct": "warn",
                               "headings.filings_without_html": "not run", "fetch.exports_missing": "pass"})

    def test_changes(self):
        before = {"metrics": {"segment": {"units": 100, "to_llm": 80}, "fetch": {"exports": 3}}}
        now = {"metrics": {"segment": {"units": 110, "to_llm": 80, "new": 2}, "fetch": {"exports": 3}}}
        rows = report.changes(now, before)
        self.assertEqual([r[0] for r in rows], ["`segment.new`", "`segment.units`"])
        self.assertEqual(rows[1][3], "+10.0%")


class TestRun(TempData):
    def test_report_on_the_ex21_fixture(self):
        shutil.copytree(FIXTURES / "raw", self.tmp / "ex21" / "raw")
        shutil.copy(FIXTURES / "manifest.csv", self.tmp / "ex21" / "manifest.csv")
        os.environ.pop("SEC_USER_AGENT", None)
        os.environ["PIPELINE_SUBSIDIARY_ALIASES"] = str(self.tmp / "no_aliases.yaml")
        real_env = subsidiaries.load_env
        subsidiaries.load_env = lambda: None
        try:
            subsidiaries.run()
        finally:
            subsidiaries.load_env = real_env
        self.assertTrue(Paths.subsidiary_qa().exists())
        self.assertEqual(Paths.subsidiary_qa().parent, self.tmp / "checks" / "subsidiaries")

        report.run()
        out = self.tmp / "checks" / "report"
        first = json.loads((out / "report.json").read_text())
        self.assertEqual(first["metrics"]["subsidiaries"]["rows"], 10)
        self.assertEqual(first["metrics"]["fetch"]["exports_missing"], 3)     # no sections in the fixture
        self.assertEqual(first["fresh"]["subsidiaries"]["status"], "ok")
        text = (out / "report.md").read_text()
        self.assertIn("No earlier report", text)
        self.assertIn("[`data/checks/subsidiaries/subsidiary_qa.csv`](../subsidiaries/subsidiary_qa.csv)", text)
        self.assertIn('<a id="step-subsidiaries"></a>', text)
        self.assertIn("(#step-subsidiaries)", text)                          # the contents link to it
        self.assertTrue(first["checks"])

        write_jsonl(Paths.sentences(), [unit(1, "A sentence.", segmentation_version="v")])
        report.run()
        self.assertTrue((out / "report_previous.json").exists())
        text = (out / "report.md").read_text()
        self.assertIn("`segment.units`", text)                                # a change since the last report
        self.assertFalse(any(p.name in ("report.md", "report.json") for p in (self.tmp / "output").iterdir()))


if __name__ == "__main__":
    unittest.main()
