"""too_short is a residual (layer 2 of the not_a_claim review): each short unit is caught by the
rule that knows what it is (segment/prefilter.py, section.py, sentences.py)."""

from __future__ import annotations

import unittest

from src import clean, segment
from src.common import settings
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


class Sentences(unittest.TestCase):
    def test_quoted_roman_numeral_opens_a_title(self):
        text = ('This is described under the sub-caption "III. Environmental Matters." '
                "We have no other matters to report.")
        self.assertEqual([text[a:b] for a, b in split_sentences(text)],
                         ['This is described under the sub-caption "III. Environmental Matters."',
                          "We have no other matters to report."])

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


if __name__ == "__main__":
    unittest.main()
