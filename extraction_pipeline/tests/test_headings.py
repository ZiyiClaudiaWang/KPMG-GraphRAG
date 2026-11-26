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


class LeadSentences(unittest.TestCase):
    """Item 1A: risk factors' lead sentences styled as headings (AMZN) get level -1 and are
    labelled as sentences; level-1 headings stay categories."""

    LEAD = f"{BOLD};{ITALIC}"
    LINES = [
        (f'<p style="{BIG}">Item 1A. Risk Factors</p>', "Item 1A. Risk Factors"),
        (f'<p style="{BIG}">Business Risks</p>', "Business Risks"),
        (f'<p style="{LEAD}">Our International Operations Expose Us to a Number of Risks in Many Countries</p>',
         "Our International Operations Expose Us to a Number of Risks in Many Countries"),
        ("<p>Our international activities are significant to our revenues and profits.</p>",
         "Our international activities are significant to our revenues and profits."),
        (f'<p style="{LEAD}">Our Expansion Places a Significant Strain on Our Management and Operations</p>',
         "Our Expansion Places a Significant Strain on Our Management and Operations"),
        ("<p>We are expanding into new products and services in many places.</p>",
         "We are expanding into new products and services in many places."),
        (f'<p style="{LEAD}">We Face Intense Competition</p>', "We Face Intense Competition"),
        ("<p>Our businesses are rapidly evolving and intensely competitive.</p>",
         "Our businesses are rapidly evolving and intensely competitive."),
        (f'<p style="{BIG}">Risks Relating to the Proposed Separation of Our Diabetes Business From Us</p>',
         "Risks Relating to the Proposed Separation of Our Diabetes Business From Us"),
        ("<p>The separation may not be completed on the terms we expect.</p>",
         "The separation may not be completed on the terms we expect."),
    ]

    @classmethod
    def setUpClass(cls):
        content = "\n\n".join(text for _, text in cls.LINES)
        raw = {"accession_number": "0000000000-26-000001", "ticker": "TEST", "form": "10-K",
               "section_id": "sec-1a", "section_key": "part-i-item-1a", "section_type": "risk_factors",
               "part": "I", "item": "1A", "processing_set_id": "set-1", "source_locator": {},
               "content": content, "offset_start": 0, "source_sha256": "0" * 64}
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "TEST.htm"
        path.write_text("<html><body>" + "".join(h for h, _ in cls.LINES) + "</body></html>", encoding="utf-8")
        cleaned = clean.clean_section(dict(raw), set())
        units = segment.segment_section(cleaned, settings()["segment"])
        heads = [{"heading_id": segment.heading_id(cleaned, cleaned["paragraphs"][u["paragraph_index"]], u["char_start"]),
                  "text": u["text"], "role": u["role"], "next_text": ""}
                 for u in units if u["role"] in ("heading", "category_heading")]
        group = {"ticker": "TEST", "form": "10-K", "accession": raw["accession_number"],
                 "section_key": raw["section_key"], "section": "Part I Item 1A", "headings": heads,
                 "units": [short_unit(u) for u in units]}
        rows, _, _ = filing_rows([group], {raw["section_key"]: raw}, path, False)
        cls.rows = {r["text"]: r for r in rows}
        levels = {r["heading_id"]: r["level"] for r in rows}
        cls.units = {u["text"]: u for u in segment.segment_section(cleaned, settings()["segment"], levels)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_long_lead_sentences_and_their_style_are_level_minus_1(self):
        for text in ("Our International Operations Expose Us to a Number of Risks in Many Countries",
                     "Our Expansion Places a Significant Strain on Our Management and Operations",
                     "We Face Intense Competition"):                      # short, same style
            self.assertEqual((self.rows[text]["level"], self.rows[text]["reason"]), (-1, "lead_sentence"))

    def test_level_1_headings_stay_categories(self):
        for text in ("Business Risks", "Risks Relating to the Proposed Separation of Our Diabetes Business From Us"):
            self.assertEqual(self.rows[text]["level"], 1)
            self.assertEqual(self.units[text]["role"], "category_heading")

    def test_step_4_labels_lead_sentences_like_text(self):
        unit = self.units["We Face Intense Competition"]
        self.assertEqual(unit["role"], "body")
        self.assertEqual(unit["category"], "Business Risks")
        nxt = self.units["Our businesses are rapidly evolving and intensely competitive."]
        self.assertEqual((nxt["category"], nxt["heading_path"]), ("Business Risks", []))


class RiskSummary(unittest.TestCase):
    """With levels the categories listed in the risk factor summary are level-2 headings under
    its title; the summary still ends where the first of them appears again (MU, TTWO)."""

    LINES = ["Item 1A. Risk Factors", "Risk Factor Summary", "Risks Related to Our Business",
             "We face competition.", "Risks Related to Laws", "We face regulation.",
             "Risks Related to Our Business", "Competition in our industry is intense and may grow over time.",
             "Risks Related to Laws", "Regulation in many countries could change and harm our business."]

    def test_summary_is_marked_with_levels(self):
        raw = {"accession_number": "a", "ticker": "T", "form": "10-K", "section_id": "s",
               "section_key": "part-i-item-1a", "section_type": "risk_factors", "part": "I", "item": "1A",
               "processing_set_id": "p", "source_locator": {}, "content": "\n\n".join(self.LINES),
               "offset_start": 0}
        cleaned = clean.clean_section(dict(raw), set())
        levels, seen = {}, 0
        for u in segment.segment_section(cleaned, settings()["segment"]):
            if u["role"] in ("heading", "category_heading"):
                hid = segment.heading_id(cleaned, cleaned["paragraphs"][u["paragraph_index"]], u["char_start"])
                levels[hid] = 1 if u["text"] == "Risk Factor Summary" or seen >= 2 else 2
                seen += u["text"] != "Risk Factor Summary"
        units = segment.segment_section(cleaned, settings()["segment"], levels)
        summary = {u["text"] for u in units if u["risk_summary"]}
        self.assertIn("We face competition.", summary)
        self.assertIn("We face regulation.", summary)
        self.assertNotIn("Competition in our industry is intense and may grow over time.", summary)


class Compare(unittest.TestCase):
    def test_removed_text_and_page_numbers(self):
        from src.headings.compare import html_heading, html_only
        from src.headings.html import Block, Run
        def block(idx, text, start):
            return Block(idx, [Run(text, True, False, False, 10.0, False)], start=start, end=start + len(text))
        header, number, kept = block(0, "PART II", 0), block(1, "30", 10), block(2, "Liquidity", 20)
        self.assertIsNone(html_heading(number, 10.0))                       # a page number
        group = {"accession": "a", "section_key": "k",
                 "units": [{"role": "body", "prefilter": None, "rule_heading": None, "start": 20, "end": 29}]}
        rows, found = html_only(group, {"offset_start": 0, "content": "x" * 40}, [header, number, kept], 10.0)
        self.assertEqual({r["text"]: r["step4_role"] for r in rows}, {"PART II": "none", "Liquidity": "body"})
        self.assertEqual(found, 1)                                          # the removed header is not counted

    def test_read_output_keeps_links(self):
        from src.headings.step import help_lines, read_output
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.csv"
            path.write_text(help_lines({"text": "a", "open_in_edgar": "b", "level": "c"})
                            + 'text,open_in_edgar,level\n"A, B",https://x/y.htm#:~:text=A%2C%20B,2\n',
                            encoding="utf-8")
            df = read_output(path)
        self.assertEqual(df.loc[0, "open_in_edgar"], "https://x/y.htm#:~:text=A%2C%20B")
        self.assertEqual(df.loc[0, "level"], 2)


def section_rows(lines, section_key="part-i-item-2", section_type="mda"):
    """filing_rows for one synthetic section: lines are (html, export text)."""
    content = "\n\n".join(text for _, text in lines)
    raw = {"accession_number": "0000000000-26-000001", "ticker": "TEST", "form": "10-Q",
           "section_id": "sec-x", "section_key": section_key, "section_type": section_type,
           "part": "I", "item": "2", "processing_set_id": "set-1", "source_locator": {},
           "content": content, "offset_start": 0, "source_sha256": "0" * 64}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "TEST.htm"
        path.write_text("<html><body>" + "".join(h for h, _ in lines) + "</body></html>", encoding="utf-8")
        cleaned = clean.clean_section(dict(raw), set())
        units = segment.segment_section(cleaned, settings()["segment"])
        heads = [{"heading_id": segment.heading_id(cleaned, cleaned["paragraphs"][u["paragraph_index"]], u["char_start"]),
                  "text": u["text"], "role": u["role"], "next_text": ""}
                 for u in units if u["role"] in ("heading", "category_heading")]
        group = {"ticker": "TEST", "form": "10-Q", "accession": raw["accession_number"],
                 "section_key": section_key, "section": "Part I Item 2", "headings": heads,
                 "units": [short_unit(u) for u in units]}
        rows, _, _ = filing_rows([group], {section_key: raw}, path, False)
    return {r["text"]: r for r in rows}


class TitleLinesAndPreambles(unittest.TestCase):
    """PH 10-Q: the MD&A title goes on over three centred lines after the Item title (level 0, so
    OVERVIEW is level 1). TTWO: a cautionary note in a larger style holds no headings."""

    CENTRED = f"{BOLD};text-transform:uppercase;text-align:center"

    def test_item_title_lines_are_level_0(self):
        rows = section_rows([
            (f'<p style="{self.CENTRED}">ITEM 2. MANAGEMENT\'S DISCUSSION AND ANALYSIS OF</p>',
             "ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS OF"),
            (f'<p style="{self.CENTRED}">FINANCIAL CONDITION AND RESULTS OF OPERATIONS</p>',
             "FINANCIAL CONDITION AND RESULTS OF OPERATIONS"),
            (f'<p style="{self.CENTRED}">FOR THE THREE AND SIX MONTHS ENDED DECEMBER 31, 2020</p>',
             "FOR THE THREE AND SIX MONTHS ENDED DECEMBER 31, 2020"),
            (f'<p style="{self.CENTRED}">AND COMPARABLE PERIODS ENDED DECEMBER 31, 2019</p>',
             "AND COMPARABLE PERIODS ENDED DECEMBER 31, 2019"),
            (f'<p style="{BOLD}">OVERVIEW</p>', "OVERVIEW"),
            ("<p>Sales grew 5% in the quarter.</p>", "Sales grew 5% in the quarter."),
            (f'<p style="{ITALIC}">Net Sales</p>', "Net Sales"),
            ("<p>Net sales were higher.</p>", "Net sales were higher."),
        ])
        for text in ("FINANCIAL CONDITION AND RESULTS OF OPERATIONS",
                     "FOR THE THREE AND SIX MONTHS ENDED DECEMBER 31, 2020",
                     "AND COMPARABLE PERIODS ENDED DECEMBER 31, 2019"):
            self.assertEqual((rows[text]["level"], rows[text]["reason"]), (0, "item_title_continuation"))
        self.assertEqual(rows["OVERVIEW"]["level"], 1)
        self.assertEqual(rows["Net Sales"]["heading_path"], ["OVERVIEW", "Net Sales"])

    def test_a_centred_heading_after_a_break_in_the_title_run_is_kept(self):
        rows = section_rows([
            (f'<p style="{self.CENTRED}">ITEM 2. MANAGEMENT\'S DISCUSSION AND ANALYSIS</p>',
             "ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS"),
            (f'<p style="{self.CENTRED}">OVERVIEW</p>', "OVERVIEW"),
            ("<p>Sales grew 5% in the quarter.</p>", "Sales grew 5% in the quarter."),
            (f'<p style="{self.CENTRED}">AND OTHER MATTERS</p>', "AND OTHER MATTERS"),
            ("<p>Nothing else changed.</p>", "Nothing else changed."),
        ])
        self.assertEqual(rows["OVERVIEW"]["level"], 1)
        self.assertEqual(rows["AND OTHER MATTERS"]["level"], 1)       # not right after the title

    def test_preamble_holds_no_headings(self):
        rows = section_rows([
            (f'<p style="{BIG}">CAUTIONARY NOTE ABOUT FORWARD-LOOKING STATEMENTS</p>',
             "CAUTIONARY NOTE ABOUT FORWARD-LOOKING STATEMENTS"),
            ("<p>Statements in this report are forward-looking.</p>", "Statements in this report are forward-looking."),
            (f'<p style="{BOLD}">Overview</p>', "Overview"),
            ("<p>We publish games.</p>", "We publish games."),
            (f'<p style="{ITALIC}">Our Business</p>', "Our Business"),
            ("<p>We sell games in many countries.</p>", "We sell games in many countries."),
            (f'<p style="{BOLD}">Results of Operations</p>', "Results of Operations"),
            ("<p>Revenue grew.</p>", "Revenue grew."),
        ], section_key="part-ii-item-7")
        self.assertEqual(rows["CAUTIONARY NOTE ABOUT FORWARD-LOOKING STATEMENTS"]["level"], 1)
        self.assertEqual(rows["Overview"]["level"], 1)
        self.assertEqual(rows["Our Business"]["heading_path"], ["Overview", "Our Business"])
        self.assertEqual(rows["Results of Operations"]["level"], 1)


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
