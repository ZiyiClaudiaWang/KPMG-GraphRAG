"""Lead sentences: found in the HTML by step 4b (headings/leads.py), linked by step 4 (segment/link_leads.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import clean, segment
from src.common import settings
from src.headings.candidates import short_unit
from src.headings.step import filing_rows

BIG = "font-size:14pt;font-weight:bold"
BOLD = "font-weight:bold"

LINES = [
    (f'<p style="{BIG}">LIQUIDITY</p>', "LIQUIDITY"),
    # a styled sentence of its own, then plain text: a lead (html)
    (f'<p style="{BOLD}">We are subject to counterparty default risks.</p>',
     "We are subject to counterparty default risks."),
    ("<p>12</p>", "12"),               # a page number between a lead and its text
    ("<p>We have numerous arrangements with banks. They may fail to pay.</p>",
     "We have numerous arrangements with banks. They may fail to pay."),
    ("<p>Such failures could hurt our results of operations.</p>",
     "Such failures could hurt our results of operations."),
    # a styled first sentence, the rest of the paragraph plain: a lead (html_runin)
    (f'<p><span style="{BOLD}">Competition in the technology sector may harm our business.</span> '
     'Our competitors are many and well funded.</p>',
     "Competition in the technology sector may harm our business. Our competitors are many and well funded."),
    ("<p>Prices may fall as a result of this competition.</p>",
     "Prices may fall as a result of this competition."),
    # a heading ends the scope of the lead above it
    (f'<p style="{BIG}">CAPITAL RESOURCES</p>', "CAPITAL RESOURCES"),
    ("<p>We hold cash and short-term investments of $10 billion.</p>",
     "We hold cash and short-term investments of $10 billion."),
    # a run-in heading ("Flexible Work.") is a heading, not a lead
    (f'<p><span style="{BOLD}">Flexible Work.</span> We let many employees work from home.</p>',
     "Flexible Work. We let many employees work from home."),
    # two styled sentences in a row: neither is a lead
    (f'<p style="{BOLD}">We expect revenue to keep growing next year.</p>',
     "We expect revenue to keep growing next year."),
    (f'<p style="{BOLD}">We expect costs to keep growing next year.</p>',
     "We expect costs to keep growing next year."),
    ("<p>Nothing else changed in the year.</p>", "Nothing else changed in the year."),
]


class Leads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        content = "\n\n".join(text for _, text in LINES)
        raw = {"accession_number": "0000000000-26-000009", "ticker": "TEST", "form": "10-K",
               "section_id": "sec-7", "section_key": "part-ii-item-7", "section_type": "mda",
               "part": "II", "item": "7", "processing_set_id": "set-1", "source_locator": {},
               "content": content, "offset_start": 0, "source_sha256": "0" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TEST.htm"
            path.write_text("<html><body>" + "".join(h for h, _ in LINES) + "</body></html>", encoding="utf-8")
            cleaned = clean.clean_section(dict(raw), set())
            units = segment.segment_section(cleaned, settings()["segment"])
            heads = [{"heading_id": segment.heading_id(cleaned, cleaned["paragraphs"][u["paragraph_index"]],
                                                       u["char_start"]),
                      "text": u["text"], "role": u["role"], "next_text": ""}
                     for u in units if u["role"] in ("heading", "category_heading")]
            group = {"ticker": "TEST", "form": "10-K", "accession": raw["accession_number"],
                     "section_key": raw["section_key"], "section": "Part II Item 7", "headings": heads,
                     "units": [short_unit(u) for u in units]}
            rows, cls.info, cls.diff = filing_rows([group], {raw["section_key"]: raw}, path, False)
        cls.leads = cls.info["leads"][raw["section_key"]]
        cls.cleaned, cls.levels = cleaned, {r["heading_id"]: r["level"] for r in rows}
        cls.units = segment.segment_section(cleaned, settings()["segment"], cls.levels)
        cls.unmatched = segment.mark_leads(cls.units, cls.leads, use_rule=False)
        cls.by_text = {u["text"]: u for u in cls.units}

    def test_leads_found_in_the_html(self):
        self.assertEqual([(x["source"], x["text"]) for x in self.leads],
                         [("html", "We are subject to counterparty default risks."),
                          ("html_runin", "Competition in the technology sector may harm our business.")])
        self.assertEqual(self.unmatched, 0)

    def test_lead_units_and_the_units_under_them(self):
        lead = self.by_text["We are subject to counterparty default risks."]
        self.assertEqual((lead["lead_source"], lead["lead_unit_id"]), ("html", None))
        for text in ("We have numerous arrangements with banks.", "They may fail to pay.",
                     "Such failures could hurt our results of operations."):
            self.assertEqual(self.by_text[text]["lead_unit_id"], lead["unit_id"])
        runin = self.by_text["Competition in the technology sector may harm our business."]
        self.assertEqual(runin["lead_source"], "html_runin")
        for text in ("Our competitors are many and well funded.", "Prices may fall as a result of this competition."):
            self.assertEqual(self.by_text[text]["lead_unit_id"], runin["unit_id"])

    def test_a_heading_ends_the_scope(self):
        unit = self.by_text["We hold cash and short-term investments of $10 billion."]
        self.assertEqual((unit["lead_source"], unit["lead_unit_id"]), (None, None))

    def test_run_in_heading_and_styled_runs_are_not_leads(self):
        self.assertEqual(self.by_text["Flexible Work."]["role"], "heading")
        for text in ("We let many employees work from home.", "We expect revenue to keep growing next year.",
                     "We expect costs to keep growing next year.", "Nothing else changed in the year."):
            self.assertEqual((self.by_text[text]["lead_source"], self.by_text[text]["lead_unit_id"]), (None, None))

    def test_leads_change_no_path_and_no_id(self):
        again = segment.segment_section(self.cleaned, settings()["segment"], self.levels)
        self.assertEqual([(u["unit_id"], u["section_path"], u["role"]) for u in again],
                         [(u["unit_id"], u["section_path"], u["role"]) for u in self.units])

    def test_a_cross_reference_is_never_a_lead(self):
        units = [{"role": "body", "raw_spans": [[0, 40]], "prefilter": "cross_reference", "unit_id": "a"},
                 {"role": "body", "raw_spans": [[41, 80]], "prefilter": None, "unit_id": "b"}]
        missed = segment.mark_leads(units, [{"start": 0, "end": 40, "source": "html"}], use_rule=False)
        self.assertEqual((missed, units[0]["lead_source"], units[1]["lead_unit_id"]), (0, None, None))

    def test_html_only_leads_leave_the_coverage(self):
        lead_rows = [r for r in self.diff if r["kind"] == "html_only" and r.get("lead") == "yes"]
        self.assertEqual([r["text"] for r in lead_rows], ["We are subject to counterparty default risks."])
        self.assertEqual(self.info["html_only_lead"], 1)


class RuleFallback(unittest.TestCase):
    """Without HTML leads, Item 1A units with rule_heading are the leads (source rule)."""

    def units(self):
        text = ("Our suppliers may fail us.\n\n"
                "We depend on a few suppliers for our parts. A failure would hurt us.\n\n"
                "Competition is intense.\n\n"
                "Our competitors are large. They cut prices.")
        sec = {"accession_number": "0000000000-26-000010", "ticker": "TEST", "form": "10-K",
               "section_id": "sec-1a", "section_key": "part-i-item-1a", "section_type": "risk_factors",
               "part": "I", "item": "1A", "processing_set_id": "set-1", "source_locator": {},
               "content": text, "offset_start": 0}
        return segment.segment_section(clean.clean_section(sec, set()), settings()["segment"])

    def test_rule_heading_is_the_lead_without_html(self):
        units = self.units()
        segment.mark_leads(units, None, use_rule=True)
        by_text = {u["text"]: u for u in units}
        lead = by_text["Our suppliers may fail us."]
        self.assertEqual(lead["lead_source"], "rule")
        self.assertEqual(by_text["A failure would hurt us."]["lead_unit_id"], lead["unit_id"])
        self.assertEqual(by_text["They cut prices."]["lead_unit_id"], by_text["Competition is intense."]["unit_id"])

    def test_lead_rule_modes(self):
        lead = [{"start": 0, "end": 10, "source": "html"}]
        self.assertEqual([segment.uses_rule("fallback", True, []), segment.uses_rule("fallback", False, None)],
                         [False, True])
        self.assertEqual([segment.uses_rule("section", True, []), segment.uses_rule("section", True, lead)],
                         [True, False])
        self.assertTrue(segment.uses_rule("union", True, lead))

    def test_rule_heading_alone_is_not_a_lead_with_html(self):
        units = self.units()
        segment.mark_leads(units, [], use_rule=False)
        self.assertFalse(any(u["lead_source"] or u["lead_unit_id"] for u in units))
        summary, review = segment.lead_report(units, set(), {})
        self.assertEqual((summary[0]["rule_heading"], summary[0]["rule_only"]), (2, 2))
        self.assertEqual({r["kind"] for r in review}, {"rule_only"})


if __name__ == "__main__":
    unittest.main()
