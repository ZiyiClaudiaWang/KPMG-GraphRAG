"""too_short is a residual (layer 2 of the not_a_claim review): each short unit is caught by the
rule that knows what it is (segment/prefilter.py, section.py, sentences.py)."""

from __future__ import annotations

import unittest

from src import clean, segment
from src.common import settings
from src.headings.leads import is_runin_heading
from src.segment.prefilter import prefilter
from src.segment.sentences import split_sentences


def units(text: str, section_type: str = "business") -> dict[str, dict]:
    sec = {"accession_number": "0000000000-26-000011", "ticker": "TEST", "form": "10-K",
           "section_id": "sec-1", "section_key": "part-i-item-1", "section_type": section_type,
           "part": "I", "item": "1", "processing_set_id": "set-1", "source_locator": {},
           "content": text, "offset_start": 0}
    return {u["text"]: u for u in segment.segment_section(clean.clean_section(sec, set()), settings()["segment"])}


class Prefilter(unittest.TestCase):
    def test_short_complete_sentences_go_to_the_llm(self):
        for text in ("No material items.", "SA is included.", "Revenue decreased slightly."):
            self.assertIsNone(prefilter(text, 4), text)

    def test_fragments_stay_too_short(self):
        for text in ("Number of", "During", "Avg.", "Proc.", "based considerations).",
                     "** Filed herewith.", "† Filed herewith.", "(b)Use of Proceeds.", "OR"):
            self.assertEqual(prefilter(text, 4), "too_short", text)

    def test_short_colon_intros_are_list_intro(self):
        for text in ("Solutions include:", "•TAC includes:", "Other Information:", "These levels are:"):
            self.assertEqual(prefilter(text, 4), "list_intro", text)


class RunInHeadings(unittest.TestCase):
    def test_short_styled_runins(self):
        for text in ("Player acquisition costs.", "Financial and savings programs.", "Merchandise."):
            self.assertTrue(is_runin_heading(text), text)
        for text in ("We may experience supply problems.", "Revenue is seasonal.", "Costs grew"):
            self.assertFalse(is_runin_heading(text), text)


class Sentences(unittest.TestCase):
    def test_quoted_roman_numeral_opens_a_title(self):
        text = ('This is described under the sub-caption "III. Environmental Matters." '
                "We have no other matters to report.")
        self.assertEqual([text[a:b] for a, b in split_sentences(text)],
                         ['This is described under the sub-caption "III. Environmental Matters."',
                          "We have no other matters to report."])

    def test_closing_quote_without_space(self):
        text = 'It is under "III. Environmental Matters."The foregoing matters may change.'
        self.assertEqual([text[a:b] for a, b in split_sentences(text)],
                         ['It is under "III. Environmental Matters."', "The foregoing matters may change."])

    def test_roman_numeral_at_a_real_end(self):
        text = "Our plans are in Phase II. The next phase starts in 2027."
        self.assertEqual(len(split_sentences(text)), 2)


class RunInHeadingInListIntro(unittest.TestCase):
    def test_list_intro_opening_with_a_run_in_heading(self):
        got = units("Aerospace Systems Segment. Our principal products include:\n\n"
                    "• flight control systems;\n\n"
                    "• engine components; and\n\n"
                    "• fuel systems.")
        head = got["Aerospace Systems Segment."]
        self.assertEqual((head["role"], head["prefilter"]), ("heading", "heading"))
        group = next(u for u in got.values() if u["role"] == "list_group")
        self.assertEqual(group["subheading"], "Aerospace Systems Segment.")

    def test_split_list_intro_is_list_intro(self):
        got = units("Merchandise. Solutions include:\n\n"
                    "• Central Fill – Prescription refill service that enables pharmacies to fill more "
                    "prescriptions every day and to spend more time with their patients, which matters.\n\n"
                    "• Professional and Advisory Services – Comprehensive suite of services that help "
                    "pharmacies run their business, improve profits and grow, which also matters a lot.\n\n"
                    "• Pharmacy Software – Systems that run the pharmacy counter and keep records of "
                    "every prescription the pharmacy fills, for many years after the fact.")
        self.assertEqual(got["Merchandise."]["role"], "heading")
        intro = got["Solutions include:"]
        self.assertEqual(intro["prefilter"], "list_intro")
        items = [u for u in got.values() if u.get("list_intro_id") == intro["unit_id"]]
        self.assertTrue(items and all(u["prefilter"] is None for u in items))


class PageNumbers(unittest.TestCase):
    def sec(self, content: str) -> dict:
        return {"accession_number": "0000000000-26-000012", "ticker": "TEST", "form": "10-K",
                "section_id": "sec-1a", "section_key": "part-i-item-1a", "section_type": "risk_factors",
                "part": "I", "item": "1A", "processing_set_id": "set-1", "source_locator": {},
                "content": content, "offset_start": 0}

    def test_dotted_page_number_before_page_header_is_dropped_and_the_sentence_joined(self):
        content = ("We have been unable to detect and prevent all such abuses or promote\n\n17.\n\n"
                   "Table of Contents | Alphabet Inc.\n\nuniformly high-quality content. Abuse may grow.")
        cleaned = clean.clean_section(self.sec(content), set())
        self.assertEqual([r["rule"] for r in cleaned["removed"]], ["page_number", "toc_link"])
        self.assertEqual(cleaned["paragraphs"][0]["text"],
                         "We have been unable to detect and prevent all such abuses or promote "
                         "uniformly high-quality content. Abuse may grow.")

    def test_numbered_item_on_its_own_line_stays(self):
        cleaned = clean.clean_section(self.sec("Our steps are:\n\n1.\n\nWe review the plan."), set())
        self.assertEqual(cleaned["removed"], [])

    def test_table_note(self):
        self.assertEqual(prefilter("* Not meaningful.", 4), "table_residue")


class Paragraphs(unittest.TestCase):
    def test_stray_bullet_is_never_too_short(self):
        got = units("Our revenues may vary because of many things.\n\n"
                    "•new product and service launches; and\n\n•seasonality.")
        self.assertIsNone(got["seasonality."]["prefilter"])

    def test_styled_runin_inside_a_paragraph(self):
        text = "Our themes are opportunity and community. Opportunity. Retail can be a powerful engine."
        sec = {"accession_number": "0000000000-26-000013", "ticker": "TEST", "form": "10-K",
               "section_id": "sec-1", "section_key": "part-i-item-1", "section_type": "business",
               "part": "I", "item": "1", "processing_set_id": "set-1", "source_locator": {},
               "content": text, "offset_start": 0}
        cleaned = clean.clean_section(sec, set())
        start = text.index("Opportunity.")
        runins = [{"start": start, "end": start + len("Opportunity."), "text": "Opportunity."}]
        got = {u["text"]: u for u in segment.segment_section(cleaned, settings()["segment"], None, None, runins)}
        self.assertEqual(got["Opportunity."]["role"], "heading")
        self.assertEqual(got["Retail can be a powerful engine."]["subheading"], "Opportunity.")
        # without the HTML, a title-case word inside a paragraph stays a sentence
        plain = {u["text"]: u for u in segment.segment_section(cleaned, settings()["segment"])}
        self.assertEqual(plain["Opportunity."]["role"], "body")


class GroupLists(unittest.TestCase):
    def test_label_row_and_names_are_one_list_group(self):
        got = units("Our groups serve these markets.\n\n"
                    "Engineered Materials Group: | • Aerospace\n\n•Agriculture\n\n•Chemical processing\n\n"
                    "Life sciences | • Microelectronics\n\n•Truck & bus\n\n"
                    "Filtration Group: | • Aerospace & defense\n\n•Agriculture\n\n•Food & beverage\n\n"
                    "We sell through distributors.")
        groups = [u for u in got.values() if u["role"] == "list_group"]
        self.assertEqual([g["text"] for g in groups],
                         ["Engineered Materials Group: | • Aerospace •Agriculture •Chemical processing "
                          "Life sciences | • Microelectronics •Truck & bus",
                          "Filtration Group: | • Aerospace & defense •Agriculture •Food & beverage"])
        self.assertTrue(all(g["prefilter"] is None for g in groups))
        self.assertNotIn("Agriculture", got)
        self.assertIsNone(got["We sell through distributors."]["prefilter"])

    def test_a_label_row_alone_is_not_a_group(self):
        got = units("Revenue: | • Services\n\nWe grew in every market this year.")
        self.assertFalse(any(u["role"] == "list_group" for u in got.values()))


class RunInLevels(unittest.TestCase):
    def test_run_in_nests_under_its_line_heading(self):
        got = units("Environmental, Social and Governance Priorities\n\n"
                    "We focus on four themes.\n\n"
                    "•Opportunity. Retail can be a powerful engine for inclusive economic opportunity.\n\n"
                    "•Community. Walmart aims to serve and strengthen communities.")
        line = "Environmental, Social and Governance Priorities"
        self.assertEqual(got["Retail can be a powerful engine for inclusive economic opportunity."]["heading_path"],
                         [line, "Opportunity."])
        # the second run-in replaces the first, not the line heading
        self.assertEqual(got["Walmart aims to serve and strengthen communities."]["heading_path"],
                         [line, "Community."])
        self.assertEqual(got["Community."]["heading_level"], 2)


    def test_a_level_from_the_html_stands(self):
        text = ("Trends and Factors Affecting our Business\n\nOur results vary from year to year.\n\n"
                "Recent Developments. We signed a merger agreement this year.")
        sec = {"accession_number": "0000000000-26-000014", "ticker": "TEST", "form": "10-K",
               "section_id": "sec-1", "section_key": "part-i-item-1", "section_type": "business",
               "part": "I", "item": "1", "processing_set_id": "set-1", "source_locator": {},
               "content": text, "offset_start": 0}
        cleaned = clean.clean_section(sec, set())
        p_line, p_run = cleaned["paragraphs"][0], cleaned["paragraphs"][2]
        levels = {segment.heading_id(cleaned, p_line, p_line["start"]): 1,
                  segment.heading_id(cleaned, p_run, p_run["start"]): 1}
        got = {u["text"]: u for u in segment.segment_section(cleaned, settings()["segment"], levels)}
        self.assertEqual(got["We signed a merger agreement this year."]["heading_path"], ["Recent Developments."])
        guessed = {segment.heading_id(cleaned, p_run, p_run["start"])}
        got = {u["text"]: u for u in segment.segment_section(cleaned, settings()["segment"], levels,
                                                             None, None, guessed)}
        self.assertEqual(got["We signed a merger agreement this year."]["heading_path"],
                         ["Trends and Factors Affecting our Business", "Recent Developments."])


class BulletRunIn(unittest.TestCase):
    def test_bullet_paragraph_opening_with_a_period_heading(self):
        got = units("Our themes are opportunity and community.\n\n"
                    "•Opportunity. Retail can be a powerful engine for inclusive economic opportunity.\n\n"
                    "•Community. Walmart aims to serve and strengthen communities.")
        self.assertEqual(got["Opportunity."]["role"], "heading")
        self.assertEqual(got["Retail can be a powerful engine for inclusive economic opportunity."]["subheading"],
                         "Opportunity.")
        self.assertEqual(got["Walmart aims to serve and strengthen communities."]["subheading"], "Community.")

    def test_bullet_items_stay_items(self):
        got = units("Our revenues may vary because of many things.\n\n•Pricing.\n\n•Seasonality.")
        self.assertEqual(got["Pricing."]["role"], "body")


if __name__ == "__main__":
    unittest.main()
