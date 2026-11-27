"""HTML tables (step 4b headings/tables.py) and the prefilter that reads them (step 4).

A small synthetic filing, no network. Run from the pipeline folder:  python -m pytest tests/test_tables.py
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import clean, segment
from src.common import settings
from src.headings.html import align, export_text, html_blocks
from src.headings.tables import section_tables, table_kind
from src.segment.html_tables import TableRows
from src.segment.prefilter import prefilter

BIO = ("Mr. Allen, 58, joined us in September 2020 as Corporate Vice President of Accounting "
       "and leads our global accounting team.")
DEFINITION = "Revenue from Azure and other cloud services, Office 365 Commercial and LinkedIn commercial."

# (html, its text in the export)
LINES = [
    ("<p>Item 7. Management's Discussion and Analysis</p>", "Item 7. Management's Discussion and Analysis"),
    ("<p>Revenue grew strongly across all of our segments this year.</p>",
     "Revenue grew strongly across all of our segments this year."),
    # a data table: a row label, numbers, and a footnote with content
    ("<table><tr><td>Net sales by category:</td><td></td><td></td></tr>"
     "<tr><td>Products</td><td>$</td><td>36,512</td><td>$</td><td>35,100</td></tr>"
     "<tr><td>Services</td><td>$</td><td>12,001</td><td>$</td><td>10,900</td></tr>"
     "<tr><td>(1) On February 9, 2025, a fire damaged a portion of our Saegertown facility.</td></tr>"
     "<tr><td>(2) Refer to Note 3 to the consolidated financial statements for details.</td></tr></table>",
     "Net sales by category: | |\n\nProducts | $ | 36,512 | $ | 35,100\n\nServices | $ | 12,001 | $ | 10,900\n\n"
     "(1) On February 9, 2025, a fire damaged a portion of our Saegertown facility.\n\n"
     "(2) Refer to Note 3 to the consolidated financial statements for details."),
    # a bullet list laid out as a table (MSFT), right after it a one-cell bio table (MU)
    ("<table><tr><td>•</td><td>Gross margin percentage decreased.</td></tr>"
     "<tr><td>•</td><td>Dynamics revenue increased 26%, driven by Dynamics 365 growth of 45%.</td></tr></table>"
     f"<table><tr><td></td><td>{BIO}</td></tr></table>",
     "• | Gross margin percentage decreased.\n\n"
     "• | Dynamics revenue increased 26%, driven by Dynamics 365 growth of 45%.\n\n"
     f"| | {BIO}"),
    # a term | definition table (MSFT metric glossary)
    ("<table><tr><td>Microsoft Cloud revenue growth</td><td>" + DEFINITION + "</td></tr>"
     "<tr><td>Microsoft Cloud gross margin percentage</td><td>Gross margin percentage for our Microsoft Cloud "
     "business across all of its offerings.</td></tr></table>",
     f"Microsoft Cloud revenue growth | {DEFINITION}\n\n"
     "Microsoft Cloud gross margin percentage | Gross margin percentage for our Microsoft Cloud business "
     "across all of its offerings."),
    # a "|" in a sentence outside any table (TTWO)
    ("<p>We released Grand Theft Auto V for the PS5 and Xbox Series X|S consoles this year.</p>",
     "We released Grand Theft Auto V for the PS5 and Xbox Series X|S consoles this year."),
]


def build(tmp: Path) -> tuple[dict, list[dict]]:
    content = "\n\n".join(t for _, t in LINES)
    raw = {"accession_number": "0000000000-26-000002", "ticker": "TEST", "form": "10-K",
           "section_id": "sec-1", "section_key": "part-ii-item-7", "section_type": "mda",
           "part": "II", "item": "7", "processing_set_id": "set-1", "source_locator": {},
           "content": content, "offset_start": 0}
    path = tmp / "TEST.htm"
    path.write_text("<html><body>" + "".join(h for h, _ in LINES) + "</body></html>", encoding="utf-8")
    blocks = html_blocks(path)
    align(blocks, export_text([raw]))
    return raw, section_tables(raw, blocks)


class TableKinds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cls.raw, cls.rows = build(Path(tmp))
        content = cls.raw["content"]
        cls.kind_of = [(content[r["start"]:r["end"]], r["kind"]) for r in cls.rows]

    def kind(self, text: str) -> str:
        """The kind of the table row that holds text."""
        return next(k for row, k in self.kind_of if text in row)

    def test_each_table_gets_its_kind(self):
        self.assertEqual(self.kind("35,100"), "data")
        self.assertEqual(self.kind("Gross margin percentage decreased."), "layout")
        self.assertEqual(self.kind(BIO), "layout")
        self.assertEqual(self.kind(DEFINITION), "definition")

    def test_adjacent_tables_are_kept_apart(self):
        # the bullet table and the bio table touch; each is its own table
        tables = {r["table"] for r in self.rows}
        self.assertEqual(len(tables), 4)

    def test_rows_are_in_content_positions(self):
        content = self.raw["content"]
        texts = [content[r["start"]:r["end"]] for r in self.rows]
        self.assertIn("Services | $ | 12,001 | $ | 10,900", texts)

    def test_a_table_of_names_and_ratings_is_data(self):
        from src.headings.html import Block, Run
        rows = [Block(i, runs=[Run(t, False, False, False, 10.0, False, False)], in_table=True)
                for i, t in enumerate(["Fitch Ratings | | BBB+", "Moody's Investors Services, Inc. | | Baa1"])]
        self.assertEqual(table_kind(rows), "data")


class Prefilter(unittest.TestCase):
    def test_data_table(self):
        self.assertEqual(prefilter("Net sales by category: | |", 4, "data"), "table_residue")
        self.assertIsNone(prefilter("(1) On February 9, 2025, a fire damaged a portion of our facility.", 4, "data"))
        self.assertEqual(prefilter("Refer to Note 3 to the consolidated financial statements for details.", 4,
                                   "data"), "cross_reference")

    def test_layout_and_definition_tables(self):
        self.assertIsNone(prefilter("| | " + BIO, 4, "layout"))
        self.assertEqual(prefilter("Microsoft Cloud revenue growth | " + DEFINITION, 4, "definition"), "definition")

    def test_a_pipe_outside_a_table_is_text(self):
        text = "We released Grand Theft Auto V for the PS5 and Xbox Series X|S consoles."
        self.assertIsNone(prefilter(text, 4, "outside"))
        self.assertEqual(prefilter(text, 4, None), "table_residue")      # no HTML: the text rule as before

    def test_notes_and_page_numbers_stay_residue_outside_tables(self):
        self.assertEqual(prefilter("NM - Not Meaningful", 4, "outside"), "table_residue")
        self.assertEqual(prefilter("40.", 4, "outside"), "table_residue")


class Where(unittest.TestCase):
    def test_leading_pipes_and_overlap(self):
        rows = TableRows([{"start": 10, "end": 40, "kind": "layout"}])
        unit = {"raw_spans": [[6, 40]], "char_start": 0, "char_end": 0, "text": "| | Mr. Allen joined us in 2020."}
        self.assertEqual(rows.where(unit), "layout")
        self.assertEqual(rows.where({"raw_spans": [[50, 60]], "char_start": 0, "char_end": 0, "text": "x"}), "outside")
        self.assertIsNone(TableRows(None).where(unit))


class Segment(unittest.TestCase):
    """Step 4 with the table rows: prefilter and html_table on the units."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            raw, rows = build(Path(tmp))
        cleaned = clean.clean_section(dict(raw), set())
        cfg = settings()["segment"]
        cls.units = {u["text"]: u for u in segment.segment_section(cleaned, cfg, None, rows)}
        cls.plain = {u["text"]: u for u in segment.segment_section(cleaned, cfg)}

    def unit(self, start: str) -> dict:
        return next(u for t, u in self.units.items() if t.startswith(start))

    def test_data_table(self):
        self.assertEqual((self.unit("Net sales by category")["prefilter"],
                          self.unit("Net sales by category")["html_table"]), ("table_residue", "data"))
        fire = self.unit("(1) On February 9, 2025")
        self.assertEqual((fire["prefilter"], fire["html_table"]), (None, "data"))     # to the LLM

    def test_layout_tables_are_text(self):
        self.assertIsNone(self.unit("Gross margin percentage decreased.")["prefilter"])
        bio = next(u for t, u in self.units.items() if "Mr. Allen" in t)
        self.assertEqual((bio["prefilter"], bio["html_table"]), (None, "layout"))
        self.assertEqual(next(u for t, u in self.plain.items() if "Mr. Allen" in t)["prefilter"], "table_residue")

    def test_definition_table(self):
        unit = self.unit("Microsoft Cloud revenue growth")
        self.assertEqual((unit["prefilter"], unit["html_table"]), ("definition", "definition"))

    def test_pipe_in_a_sentence(self):
        unit = self.unit("We released Grand Theft Auto V")
        self.assertEqual((unit["prefilter"], unit["html_table"]), (None, None))
        self.assertEqual(self.plain[unit["text"]]["prefilter"], "table_residue")      # the old text rule

    def test_version(self):
        self.assertEqual(self.unit("Revenue grew strongly")["segmentation_version"], "kelly_sentence_v14")


if __name__ == "__main__":
    unittest.main()
