"""Step 4b (src/headings/): heading levels from a filing's HTML. A small synthetic filing, no
network: the HTML is written to a temporary file and the export text is built from the same lines.

Run from the pipeline folder:  python -m pytest tests/test_headings.py
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import clean, segment
from src.common import settings
from src.headings import levels as L
from src.headings.step import filing_rows

BIG = "font-size:14pt;font-weight:bold"
BOLD = "font-weight:bold"
ITALIC = "font-style:italic"

# (html of the line, its text in the export). Tables are rows of cells.
LINES = [
    (f'<p style="{BOLD};text-align:center">Item 7. Management Discussion</p>', "Item 7. Management Discussion"),
    ('<p style="text-align:center">For the fiscal year ended June 30, 2026</p>',
     "For the fiscal year ended June 30, 2026"),
    (f'<p style="{BIG}">OVERVIEW</p>', "OVERVIEW"),
    ("<p>Revenue grew 10% in the year.</p>", "Revenue grew 10% in the year."),
    (f'<p style="{BIG}">SEGMENT RESULTS</p>', "SEGMENT RESULTS"),
    (f'<p style="{BOLD}">Intelligent Cloud</p>', "Intelligent Cloud"),
    ("<p>Azure revenue grew 30% in the year.</p>", "Azure revenue grew 30% in the year."),
    (f'<p style="{ITALIC}">Server Products</p>', "Server Products"),
    ("<p>Server revenue grew 5% in the year.</p>", "Server revenue grew 5% in the year."),
    (f'<p style="{BOLD}">More Personal Computing</p>', "More Personal Computing"),
    ("<p>Revenue decreased 2% in the year.</p>", "Revenue decreased 2% in the year."),
    ("<p>(In millions)</p>", "(In millions)"),
    ('<table><tr><td>Fiscal Year</td></tr><tr><td>Revenue</td><td>100</td></tr></table>',
     "Fiscal Year\n\nRevenue | 100"),
    (f'<p style="{BOLD}">We may be adversely impacted by increases in the cost of food</p>',
     "We may be adversely impacted by increases in the cost of food"),
    ("<p>Costs rose 4% in the year.</p>", "Costs rose 4% in the year."),
    (f'<p style="{BIG}">INDEX TO NOTES</p>', "INDEX TO NOTES"),
    ('<p><a href="#n1">Revenue Recognition</a></p>', "Revenue Recognition"),
    ("<p>Leases and Commitments</p>", "Leases and Commitments"),
    (f'<p style="{BIG}">OPERATING EXPENSES</p>', "OPERATING EXPENSES"),
    ("<p>Expenses rose 3% in the year.</p>", "Expenses rose 3% in the year."),
]


def filing(tmp: Path) -> tuple[dict, dict, Path]:
    content = "\n\n".join(text for _, text in LINES)
    raw = {"accession_number": "0000000000-26-000001", "ticker": "TEST", "form": "10-K",
           "section_id": "sec-1", "section_key": "part-ii-item-7", "section_type": "mda",
           "part": "II", "item": "7", "processing_set_id": "set-1", "source_locator": {},
           "content": content, "offset_start": 0, "source_sha256": "0" * 64}
    page = "<html><body>" + "".join(h for h, _ in LINES) + "</body></html>"
    path = tmp / "TEST.htm"
    path.write_text(page, encoding="utf-8")
    cleaned = clean.clean_section(dict(raw), set())
    units = segment.segment_section(cleaned, settings()["segment"])
    heads = [{"heading_id": segment.heading_id(cleaned, cleaned["paragraphs"][u["paragraph_index"]], u["char_start"]),
              "text": u["text"], "next_text": ""} for u in units if u["role"] == "heading"]
    group = {"ticker": "TEST", "form": "10-K", "accession": raw["accession_number"],
             "section_key": raw["section_key"], "section": "Part II Item 7", "headings": heads}
    return raw, group, path


class HtmlHeadings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        raw, group, path = filing(Path(cls.tmp.name))
        cls.group = group
        rows, cls.info = filing_rows([group], {raw["section_key"]: raw}, path, False)
        cls.rows = {r["text"]: r for r in rows}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def level(self, text):
        return self.rows[text]["level"], self.rows[text]["reason"]

    def test_candidates_are_the_lines_step_4_reads_as_headings(self):
        for text in ("OVERVIEW", "SEGMENT RESULTS", "Intelligent Cloud", "Server Products",
                     "More Personal Computing", "OPERATING EXPENSES"):
            self.assertIn(text, self.rows)

    def test_typography_nests_and_same_style_is_a_sibling(self):
        self.assertEqual(self.level("OVERVIEW"), (1, "typography"))
        self.assertEqual(self.level("SEGMENT RESULTS"), (1, "typography"))
        self.assertEqual(self.level("Intelligent Cloud"), (2, "typography"))
        self.assertEqual(self.level("Server Products"), (3, "typography"))
        self.assertEqual(self.level("More Personal Computing"), (2, "typography"))
        self.assertEqual(self.rows["More Personal Computing"]["heading_path"],
                         ["SEGMENT RESULTS", "More Personal Computing"])
        self.assertEqual(self.rows["Server Products"]["parent_text"], "Intelligent Cloud")

    def test_a_more_prominent_style_closes_the_levels_under_it(self):
        self.assertEqual(self.level("OPERATING EXPENSES"), (1, "typography"))

    def test_item_title_continuation_and_units_line_are_not_headings(self):
        if "For the fiscal year ended June 30, 2026" in self.rows:
            self.assertEqual(self.level("For the fiscal year ended June 30, 2026"), (0, "item_title_continuation"))
        self.assertEqual(self.level("(In millions)"), (0, "units_line"))

    def test_table_headers_are_level_0(self):
        if "Fiscal Year" in self.rows:
            self.assertEqual(self.level("Fiscal Year"), (0, "table"))

    def test_table_of_contents_is_level_0(self):
        self.assertEqual(self.level("INDEX TO NOTES"), (0, "toc"))
        self.assertEqual(self.level("Revenue Recognition"), (0, "toc"))       # a link
        self.assertEqual(self.level("Leases and Commitments"), (0, "toc"))    # less prominent, right after

    def test_possible_sentence_is_flagged_not_changed(self):
        row = self.rows["We may be adversely impacted by increases in the cost of food"]
        self.assertGreater(row["level"], 0)
        self.assertIn("maybe_sentence", row["flags"])

    def test_sha_differs_from_the_export(self):
        self.assertEqual(self.info, {"html": "ok", "sha_differs": True})


class Step(unittest.TestCase):
    """run(): the levels file, review and summary files, and the old LLM file kept aside."""

    def test_run_writes_levels_review_and_summary(self):
        import csv
        import json
        import os
        import yaml
        from src import clean as clean_step, filter_sections
        from src.headings import step
        from src.headings.candidates import levels_path
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw, group, html_path = filing(tmp)
            template = json.loads((Path(__file__).parent / "fixtures" / "sections"
                                   / "TEST_0000000000-25-000001.jsonl").read_text().splitlines()[0])
            row = dict(template, **{k: raw[k] for k in ("accession_number", "section_key", "section_type", "part",
                                                         "item", "content", "offset_start")},
                       original_title="Item 7. Management Discussion", n_chars=len(raw["content"]),
                       offset_end=len(raw["content"]))
            (tmp / "data" / "sections").mkdir(parents=True)
            (tmp / "data" / "sections" / "TEST_0000000000-26-000001.jsonl").write_text(json.dumps(row) + "\n")
            (tmp / "data" / "edgar" / "primary").mkdir(parents=True)
            html_path.rename(tmp / "data" / "edgar" / "primary" / "TEST_0000000000-26-000001.htm")
            (tmp / "filings.yaml").write_text(yaml.safe_dump({"filings": [
                {"ticker": "TEST", "cik": "0000000000", "form": "10-K", "accession": "0000000000-26-000001",
                 "reporting_period_end": "2026-06-30"}]}))
            saved = {k: os.environ.get(k) for k in ("PIPELINE_DATA_DIR", "PIPELINE_FILINGS")}
            os.environ["PIPELINE_DATA_DIR"] = str(tmp / "data")
            os.environ["PIPELINE_FILINGS"] = str(tmp / "filings.yaml")
            try:
                clean_step.run()
                filter_sections.run()
                levels_path().parent.mkdir(parents=True, exist_ok=True)
                old = [{"heading_id": h["heading_id"], "level": 1, "text": h["text"], "source": "llm:x"}
                       for h in group["headings"]]
                levels_path().write_text("".join(json.dumps(r) + "\n" for r in old))
                step.run(fetch=False)
                levels = [json.loads(line) for line in levels_path().read_text().splitlines()]
                review = list(csv.DictReader(step.review_path().open()))
                summary = list(csv.DictReader(step.summary_path().open()))
                backup = levels_path().with_name("heading_levels_llm_backup.jsonl").read_text()
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        self.assertEqual({r["source"] for r in levels}, {"html"})
        self.assertEqual(len(levels), len(group["headings"]))
        by_text = {r["text"]: r for r in review}
        self.assertEqual(by_text["Server Products"]["heading_path"], "SEGMENT RESULTS > Intelligent Cloud > Server Products")
        self.assertEqual(by_text["Server Products"]["level_old"], "1")
        self.assertEqual(by_text["Server Products"]["parent_old"], "")
        self.assertEqual(summary[0]["ticker"], "TEST")
        self.assertEqual(summary[0]["found_in_html_pct"], "100.0")
        self.assertIn("llm:x", backup)


class Fallbacks(unittest.TestCase):
    def test_without_html_every_heading_is_a_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, group, _ = filing(Path(tmp))
            rows, info = filing_rows([group], {raw["section_key"]: raw}, None, False)
        self.assertEqual(info["html"], "missing")
        self.assertEqual({(r["level"], r["reason"]) for r in rows}, {(1, "missing_html")})

    def test_old_levels_give_parents_like_step_4(self):
        heads = [{"heading_id": k, "text": k} for k in "ABCD"]
        self.assertEqual(L.paths_from_levels(heads, {"A": 1, "B": 2, "C": 0, "D": 2}),
                         {"A": None, "B": "A", "C": "B", "D": "A"})


if __name__ == "__main__":
    unittest.main()
