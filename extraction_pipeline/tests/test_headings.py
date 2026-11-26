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
from src.headings.candidates import short_unit
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
    ("<p>________________</p>", "________________"),
    ("<p>(1) Excludes fuel.</p>", "(1) Excludes fuel."),
    # CASY 10-Q: one heading over two HTML blocks, one line in the export; then a unit line.
    (f'<p style="{BOLD};text-decoration:underline;text-align:center">Nine Months Ended January 31, 2021 Compared to</p>'
     f'<p style="{BOLD};text-decoration:underline;text-align:center">Nine Months Ended January 31, 2020</p>',
     "Nine Months Ended January 31, 2021 Compared to Nine Months Ended January 31, 2020"),
    (f'<p style="{ITALIC};text-align:center">(Dollars and Amounts in Thousands)</p>',
     "(Dollars and Amounts in Thousands)"),
    ("<p>Revenue grew 8% in the nine months.</p>", "Revenue grew 8% in the nine months."),
    (f'<p style="{BIG}">INDEX TO NOTES</p>', "INDEX TO NOTES"),
    ('<p><a href="#n1">Revenue Recognition</a></p>', "Revenue Recognition"),
    ("<p>Leases and Commitments</p>", "Leases and Commitments"),
    (f'<p style="{BIG}">OPERATING EXPENSES</p>', "OPERATING EXPENSES"),
    ("<p>Expenses rose 3% in the year.</p>", "Expenses rose 3% in the year."),
    # For the HTML vs step 4 comparison: a bold full sentence and a bold short sentence (both body
    # in step 4), and a heading step 4 reads from the text that the HTML shows as plain.
    (f'<p style="{BOLD}">We expect revenue to keep growing in the next fiscal year.</p>',
     "We expect revenue to keep growing in the next fiscal year."),
    (f'<p style="{BOLD}">Revenue increased.</p>', "Revenue increased."),
    ("<p>Other Matters</p>", "Other Matters"),
    ("<p>Nothing else changed in the year.</p>", "Nothing else changed in the year."),
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
             "section_key": raw["section_key"], "section": "Part II Item 7", "headings": heads,
             "units": [short_unit(u) for u in units]}
    return raw, group, path


class Synthetic(unittest.TestCase):
    """The synthetic filing, run through filing_rows once per test class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        raw, group, path = filing(Path(cls.tmp.name))
        cls.group = group
        rows, cls.info, diff = filing_rows([group], {raw["section_key"]: raw}, path, False)
        cls.rows = {r["text"]: r for r in rows}
        cls.diff = {(r["kind"], r["text"]): r for r in diff}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class HtmlHeadings(Synthetic):
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

    def test_long_heading_outside_item_1a_is_not_flagged(self):
        row = self.rows["We may be adversely impacted by increases in the cost of food"]
        self.assertGreater(row["level"], 0)
        self.assertNotIn("risk_sentence", row["flags"])

    def test_line_without_letters_is_not_a_heading(self):
        # AMZN: the rule before footnotes is a candidate heading in step 4.
        self.assertEqual(self.level("________________"), (0, "no_letters"))

    def test_heading_split_over_two_html_blocks_is_matched(self):
        row = self.rows["Nine Months Ended January 31, 2021 Compared to Nine Months Ended January 31, 2020"]
        self.assertEqual((row["match"], row["joined_blocks"], row["reason"]), ("standalone", 2, "typography"))
        self.assertIn(" / ", row["block_text"])

    def test_unit_line_with_more_words(self):
        self.assertEqual(self.level("(Dollars and Amounts in Thousands)"), (0, "units_line"))

    def test_link_suffix_is_the_next_html_block(self):
        self.assertEqual(self.rows["Intelligent Cloud"]["link_suffix"], "Azure revenue grew 30%")

    def test_sha_differs_from_the_export(self):
        self.assertEqual((self.info["html"], self.info["sha_differs"]), ("ok", True))


class HtmlVsStep4(Synthetic):
    """compare.py: HTML headings step 4 missed, and step-4 headings the HTML shows as plain."""

    def test_styled_lines_step_4_reads_as_body_are_html_only(self):
        row = self.diff[("html_only", "We expect revenue to keep growing in the next fiscal year.")]
        self.assertEqual((row["html_style"], row["step4_role"], row["text_check"]), ("bold", "body", "sentence"))
        row = self.diff[("html_only", "Revenue increased.")]
        self.assertEqual((row["step4_role"], row["text_check"]), ("body", "short_line"))

    def test_headings_step_4_reads_are_not_html_only(self):
        html_only = {text for kind, text in self.diff if kind == "html_only"}
        for text in ("OVERVIEW", "Intelligent Cloud", "Server Products", "Nine Months Ended January 31, 2020",
                     "Item 7. Management Discussion", "(Dollars and Amounts in Thousands)", "Fiscal Year"):
            self.assertNotIn(text, html_only)

    def test_plain_heading_in_the_html_is_step4_plain(self):
        row = self.diff[("step4_plain", "Other Matters")]
        self.assertEqual(row["html_style"], "plain")
        self.assertGreater(row["level"], 0)
        plain = {text for kind, text in self.diff if kind == "step4_plain"}
        self.assertNotIn("Leases and Commitments", plain)       # level 0 (toc): not compared
        self.assertNotIn("OVERVIEW", plain)

    def test_counts_for_the_summary(self):
        html_only = sum(kind == "html_only" for kind, _ in self.diff)
        self.assertEqual(self.info["html_only"], html_only)
        self.assertGreater(self.info["html_headings"], html_only)

    def test_max_words_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, group, path = filing(Path(tmp))
            _, _, diff = filing_rows([group], {raw["section_key"]: raw}, path, False, {"max_words": 5})
        self.assertNotIn("We expect revenue to keep growing in the next fiscal year.",
                         {r["text"] for r in diff if r["kind"] == "html_only"})


class Step(unittest.TestCase):
    """run(): the levels file, and the review and summary files with their column guide."""

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
                step.run(fetch=False)
                levels = [json.loads(line) for line in levels_path().read_text().splitlines()]
                review_lines = step.review_path().read_text().splitlines()
                summary_lines = step.summary_path().read_text().splitlines()
                review = list(csv.DictReader(line for line in review_lines if not line.startswith("#")))
                summary = list(csv.DictReader(line for line in summary_lines if not line.startswith("#")))
                diff_lines = step.diff_path().read_text().splitlines()
                diff = list(csv.DictReader(line for line in diff_lines if not line.startswith("#")))
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
        self.assertEqual(summary[0]["ticker"], "TEST")
        self.assertEqual(summary[0]["found_in_html_pct"], "100.0")
        self.assertEqual({r["kind"] for r in diff}, {"html_only", "step4_plain"})
        self.assertEqual(summary[0]["html_only"], str(sum(r["kind"] == "html_only" for r in diff)))
        for lines, cols in ((review_lines, step.REVIEW_HELP), (summary_lines, step.SUMMARY_HELP),
                            (diff_lines, step.DIFF_HELP)):
            guide = [line for line in lines if line.startswith("# ")]
            self.assertEqual([line[2:].split(":")[0] for line in guide], list(cols))   # one line per column
            self.assertEqual(lines[len(guide)].split(","), list(cols))               # then the header


class RiskCategories(unittest.TestCase):
    """In Item 1A a heading is a risk category when its level is 1; without a level, by its look."""

    LINES = ["Item 1A. Risk Factors", "Risks Related to Our Business", "Supply Chain",
             "We depend on a small number of suppliers for our products and services.",
             "Risks Related to Our Industry", "Competition in our industry is intense and may grow."]

    def units(self, levels_by_text=None):
        content = "\n\n".join(self.LINES)
        raw = {"accession_number": "0000000000-26-000001", "ticker": "TEST", "form": "10-K",
               "section_id": "sec-1a", "section_key": "part-i-item-1a", "section_type": "risk_factors",
               "part": "I", "item": "1A", "processing_set_id": "set-1", "source_locator": {},
               "content": content, "offset_start": 0}
        cleaned = clean.clean_section(dict(raw), set())
        levels = None
        if levels_by_text is not None:
            levels = {}
            for u in segment.segment_section(cleaned, settings()["segment"]):
                if u["text"] in levels_by_text:
                    para = cleaned["paragraphs"][u["paragraph_index"]]
                    levels[segment.heading_id(cleaned, para, u["char_start"])] = levels_by_text[u["text"]]
        return {u["text"]: u for u in segment.segment_section(cleaned, settings()["segment"], levels)}

    def test_without_levels_a_title_case_sub_heading_looks_like_a_category(self):
        units = self.units()
        self.assertEqual(units["Supply Chain"]["role"], "category_heading")

    def test_with_levels_only_level_1_is_a_category(self):
        units = self.units({"Risks Related to Our Business": 1, "Supply Chain": 2,
                            "Risks Related to Our Industry": 1})
        self.assertEqual(units["Risks Related to Our Business"]["role"], "category_heading")
        self.assertEqual(units["Supply Chain"]["role"], "heading")
        sentence = units["We depend on a small number of suppliers for our products and services."]
        self.assertEqual((sentence["category"], sentence["subheading"]), ("Risks Related to Our Business", "Supply Chain"))
        self.assertEqual(sentence["section_path"], "Part I Item 1A > Risks Related to Our Business > Supply Chain")
        other = units["Competition in our industry is intense and may grow."]
        self.assertEqual((other["category"], other["heading_path"]), ("Risks Related to Our Industry", []))


class Flags(unittest.TestCase):
    def test_risk_sentence_only_in_item_1a(self):
        long = "We may be adversely impacted by increases in the cost of food ingredients"
        self.assertEqual(L.flags_for(long, True, False), ["risk_sentence"])
        self.assertEqual(L.flags_for(long, False, False), [])
        self.assertEqual(L.flags_for("Risks Related to Our Industry", True, False), [])


class Links(unittest.TestCase):
    def test_text_fragment_link(self):
        from src.headings.edgar_primary import text_link
        url = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/x.htm"
        self.assertEqual(text_link(url, "Intelligent Cloud", "Azure revenue grew 30%"),
                         url + "#:~:text=Intelligent%20Cloud,-Azure%20revenue%20grew%2030%25")
        self.assertEqual(text_link(url, "Non-GAAP Measures"), url + "#:~:text=Non%2DGAAP%20Measures")
        long = "Nine Months Ended January 31, 2021 Compared to Nine Months Ended January 31, 2020"
        self.assertEqual(text_link(url, long),
                         url + "#:~:text=Nine%20Months%20Ended%20January,Ended%20January%2031%2C%202020")
        self.assertEqual(text_link(url, "Three Months Ended July 31", split=True),
                         url + "#:~:text=Three%20Months,July%2031")
        self.assertEqual(text_link(None, "Intelligent Cloud"), "")

    def test_urls_are_looked_up_once_and_kept(self):
        from unittest import mock
        from src.headings import edgar_primary
        filing = {"accession": "0000000000-26-000001", "cik": "1", "form": "10-K", "ticker": "TEST"}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(edgar_primary, "primary_url", return_value="https://x/y.htm") as lookup:
                urls = edgar_primary.Urls(Path(tmp))
                self.assertIsNone(urls.get(filing, fetch=False))
                self.assertEqual(urls.get(filing, fetch=True), "https://x/y.htm")
                self.assertEqual(edgar_primary.Urls(Path(tmp)).get(filing, fetch=True), "https://x/y.htm")
            self.assertEqual(lookup.call_count, 1)


class Fallbacks(unittest.TestCase):
    def test_without_html_every_heading_is_a_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, group, _ = filing(Path(tmp))
            rows, info, diff = filing_rows([group], {raw["section_key"]: raw}, None, False)
        self.assertEqual(diff, [])
        self.assertEqual(info["html"], "missing")
        self.assertEqual({(r["level"], r["reason"]) for r in rows}, {(1, "missing_html"), (0, "no_letters")})


if __name__ == "__main__":
    unittest.main()
