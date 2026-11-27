"""Rules added for the 233-filing run (diagnostics of 18 Nov 2026). Each case is cut from a real
filing (samples.csv); the text is shortened. No data folder, API key or database needed.

Run from the pipeline folder:  python -m pytest tests/test_scale_rules.py
"""

from __future__ import annotations

import unittest

from src import clean, segment
from src.common import settings


def section(paragraphs: list[str], section_type: str = "mda", section_key: str = "part-ii-item-7") -> dict:
    """A Data-team section whose content is the given paragraphs, one per blank line."""
    return {"accession_number": "0000000000-26-000001", "ticker": "TEST", "form": "10-K",
            "section_id": "sec-1", "section_key": section_key, "section_type": section_type,
            "part": "II", "item": "7", "processing_set_id": "set-1", "source_locator": {},
            "content": "\n\n".join(paragraphs)}


def units(paragraphs: list[str], **kw) -> list[dict]:
    cleaned = clean.clean_section(section(paragraphs, **kw), set())
    return segment.segment_section(cleaned, settings()["segment"])


def by_text(us: list[dict]) -> dict[str, dict]:
    return {u["text"]: u for u in us}


def listed(us: list[dict]) -> list[str]:
    """Texts read as list items: split items (list_intro_id) and the items inside a list_group."""
    out = [u["text"] for u in us if u.get("list_intro_id")]
    for g in (u for u in us if u["role"] == "list_group"):
        out += [g["text"]]
    return out


class Abbreviations(unittest.TestCase):
    def test_currency_pairs_and_dashes_are_not_sentence_ends(self):
        # Nike: "Euro/U.S. Dollar"; Micron: "two asserted patents—U.S. Patent No. 7,619,912".
        for text in ["Hedges are primarily for Euro/U.S. Dollar and Japanese Yen/U.S. Dollar currency pairs.",
                     "The jury found Micron infringed two asserted patents—U.S. Patent No. 7,619,912."]:
            self.assertEqual(len(segment.split_sentences(text)), 1, text)


class Bullets(unittest.TestCase):
    def test_symbol_font_bullet_on_its_own_line(self):
        # NetApp 10-Q 2021-10-29: "" alone, then the item. Before, the items became headings.
        us = by_text(units(["Supply chain risks include:", "",
                            "Impacts on our supply chain from adverse public health developments;",
                            "", "No guarantees of supply and limited ability to control quality."]))
        self.assertNotIn("heading", {u["role"] for u in us.values()})
        self.assertTrue(any("Impacts on our supply chain" in t for t in listed(list(us.values()))))

    def test_bullet_exported_as_a_table_cell(self):
        # Microsoft 10-K 2022: "• | Dynamics products ... revenue increased 25% ..."
        us = units(["Revenue increased $7.5 billion or 16% driven by the following:",
                    "• | Server products and cloud services revenue increased 28% driven by Azure.",
                    "• | Dynamics products and cloud services revenue increased 25% driven by Dynamics 365."])
        items = [u for u in us if u.get("list_intro_id")]
        self.assertEqual([u["text"][:6] for u in items], ["Server", "Dynami"])
        self.assertTrue(all(u["prefilter"] is None for u in items))
        self.assertFalse(clean.has_table_cells("• | Dynamics revenue increased 25%."))
        self.assertTrue(clean.has_table_cells("• | Dynamics | 25%"))


class ListsWithoutColon(unittest.TestCase):
    def test_bullets_under_a_heading_point_to_the_heading(self):
        # Micron's risk factor summary: a category heading, then fragment bullets.
        us = units(["Risk Factor Summary",
                    "Risks Related to Laws and Regulations",
                    "•impacts of government actions and compliance with tariffs and trade regulations;",
                    "•tax expense and tax laws in key jurisdictions; and",
                    "•compliance with laws and regulations."], section_type="risk_factors",
                   section_key="part-i-item-1a")
        heading = by_text(us)["Risks Related to Laws and Regulations"]
        items = [u for u in us if u.get("list_intro_id")]
        self.assertEqual(len(items), 3)
        self.assertTrue(all(u["list_intro_id"] == heading["unit_id"] for u in items))
        self.assertEqual(items[0]["text"][:7], "impacts")

    def test_sentence_announcing_the_following(self):
        # McKesson 10-Q: "The following summary provides highlights ... ." then bullets.
        intro = ("The following summary provides highlights and key factors that impacted our "
                 "business for the three months ended June 30, 2021.")
        us = by_text(units([intro, "•Revenues increased 7%.", "•Gross profit was flat."]))
        self.assertEqual(us["Revenues increased 7%."]["list_intro_id"], us[intro]["unit_id"])

    def test_announcing_sentence_before_a_last_sentence(self):
        # Medtronic: "...included the following. Unless otherwise stated, amounts apply to both."
        intro = "Our income tax expense included the following."
        us = by_text(units([intro + " Unless otherwise stated, amounts apply to both periods.",
                            "•A net benefit of $106 million associated with the resolution of an audit."]))
        item = us["A net benefit of $106 million associated with the resolution of an audit."]
        self.assertEqual(item["list_intro_id"], us[intro]["unit_id"])

    def test_plain_sentence_before_bullets_is_not_an_intro(self):
        us = by_text(units(["Revenue grew in every segment.", "•Cloud revenue increased 29%."]))
        self.assertNotIn("list_intro_id", us["Cloud revenue increased 29%."])


class LetteredItems(unittest.TestCase):
    def test_lettered_items_after_a_colon(self):
        # Take-Two Item 1A: "...including:" then "a.our operations ...;" "b.the proposed ...;"
        intro = "Our business is subject to various risks related to the Zynga acquisition, including:"
        us = units([intro, "a.our operations will be restricted by the terms of the merger agreement;",
                    "b.the proposed transaction may disrupt our current business plans; and",
                    "c.legal proceedings may be instituted against Take-Two."], section_type="risk_factors",
                   section_key="part-i-item-1a")
        self.assertTrue(any("b.the proposed transaction" in t for t in listed(us)))
        self.assertFalse(any(u["role"] == "heading" for u in us))

    def test_numbered_items_after_a_colon(self):
        # NetApp: "Our cost of revenues consists of:" "(1) cost of product revenues ..." "(2) ..."
        us = units(["Our cost of revenues consists of:",
                    "(1) cost of product revenues, which includes the costs of manufacturing, and;",
                    "(2) cost of services revenues, which includes the costs of providing support."])
        self.assertTrue(any("(2) cost of services" in t for t in listed(us)))

    def test_table_footnotes_are_not_list_items(self):
        # Apple: footnotes under a table are ordinary paragraphs, not a list.
        us = units(["Total net sales | $ | 117,154 | $ | 123,945",
                    "(1)Products net sales include amortization of the deferred value of upgrade rights.",
                    "(2)Services net sales include sales from advertising, AppleCare and cloud services."])
        self.assertFalse(any("list_intro_id" in u for u in us))


class LostBullets(unittest.TestCase):
    def test_item_without_its_bullet_stays_in_the_list(self):
        # An item whose bullet the export dropped: after "...; and", lower case, no bullet.
        us = units(["Factors that could affect our results include:",
                    "•volatility in average selling prices of our products;",
                    "•volatility in the trading price of our common stock; and",
                    "fluctuations in the amount and frequency of our common stock repurchases."])
        self.assertTrue(any("fluctuations in the amount" in t for t in listed(us)))

    def test_short_name_between_bullets(self):
        # Parker-Hannifin: "• Power generation" / "Renewable energy" / "• Water purification".
        us = units(["Key markets include:", "• Power generation", "Renewable energy", "• Water purification"])
        self.assertFalse(any(u["role"] == "heading" for u in us))
        self.assertTrue(any("Renewable energy" in t for t in listed(us)))

    def test_item_ending_in_and_is_not_joined_to_the_next(self):
        cleaned = clean.clean_section(section(["•volatility in the trading price of our common stock; and",
                                               "fluctuations in the amount of our common stock repurchases."]),
                                      set())
        self.assertEqual(len(cleaned["paragraphs"]), 2)


if __name__ == "__main__":
    unittest.main()


def boundary(a: str, b: str) -> str:
    raw = a + "\n\n" + b
    return clean.boundary_class(raw, [[0, len(a)]], b)


class PageBreaks(unittest.TestCase):
    def test_new_joins(self):
        # Micron "Through February 27," / "2025, we had repurchased ..."
        self.assertEqual(boundary("Our ongoing determination of the best use of available cash. Through February 27,",
                                  "2025, we had repurchased an aggregate of $7.19 billion."), "join_comma")
        # Take-Two "proceeds from the issuance of 2026 Notes and" / "2028 Notes offset by ..."
        self.assertEqual(boundary("The debt activity included proceeds from the issuance of 2026 Notes and",
                                  "2028 Notes offset by our repayment of 2024 Notes."), "join_dangling_digit")
        # Micron: a quote opened and closed across the break
        self.assertEqual(boundary("Restructure and Asset Impairments: See “Item 1. Financial Statements – Notes to "
                                  "Consolidated Financial", "Statements – Restructure and Asset Impairments”."),
                         "join_open_quote")

    def test_never_joined(self):
        self.assertEqual(boundary("Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations",
                                  "This Item contains forward-looking statements."), "heading_then_text")
        self.assertEqual(boundary("Three Months Ended December 31, 2021 Compared with Three Months Ended December 31, 2020",
                                  "Revenue increased $8.7 billion or 20%."), "heading_then_text")
        self.assertEqual(boundary("•Grew store count by 154 stores through new store construction and acquisitions",
                                  "•Entered into our 17th state of Texas"), "next_is_bullet")
        self.assertEqual(boundary("•Offset share dilution through 10b5-1 share repurchase program and more", "Cash Flows"),
                         "bullet_item_end")
        self.assertEqual(boundary("Indicate by check mark whether the registrant is a shell company. Yes ☐ No ☒",
                                  "Indicate the number of shares outstanding."), "checkbox")

    def test_gray_is_joined_only_with_a_yes(self):
        a = "Our business could be subject to fines under laws including, but not limited to, the European Union General Data"
        b = "Protection Regulation, which provides for penalties of up to 20 million Euros."
        self.assertEqual(boundary(a, b), "C9_gray_no_punct_capital")
        sec = section([a, b])
        self.assertEqual(len(clean.clean_section(sec, set())["paragraphs"]), 2)
        bid = clean.boundary_id(sec, len(a) + 2)
        self.assertEqual(len(clean.clean_section(sec, set(), {bid: True})["paragraphs"]), 1)
        self.assertEqual(len(clean.clean_section(sec, set(), {bid: False})["paragraphs"]), 2)

    def test_reply_is_read_by_pair_number(self):
        from src import page_breaks
        plan = {"batch": {"boundary_ids": ["x|s|10", "x|s|20", "x|s|30"]}}

        def record(rows):
            import json
            return {"raw_response": {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"results": rows})}}]}}
        problems, got = page_breaks.answers(plan, record([{"id": "p1", "same_sentence": True},
                                                          {"id": "p3", "same_sentence": False},
                                                          {"id": "p9", "same_sentence": True}]))
        self.assertEqual(got, {"x|s|10": True, "x|s|30": False})
        self.assertTrue(any("p2" in p for p in problems))       # unanswered: left unjoined


class HeadingLevels(unittest.TestCase):
    paragraphs = ["SEGMENT RESULTS OF OPERATIONS", "Reportable Segments", "Intelligent Cloud",
                  "Revenue increased $7.5 billion or 16%.", "(In millions)", "Azure revenue grew 30%.",
                  "More Personal Computing", "Revenue decreased 2%.", "OPERATING EXPENSES", "Costs rose 4%."]

    def levels(self, by_text: dict[str, int]) -> dict[str, int]:
        cleaned = clean.clean_section(section(self.paragraphs), set())
        out = {}
        for k, p in enumerate(cleaned["paragraphs"]):
            if p["text"] in by_text:
                out[segment.heading_id(cleaned, p, p["start"])] = by_text[p["text"]]
        return out

    def test_without_levels_a_heading_replaces_the_one_before(self):
        us = by_text(units(self.paragraphs))
        self.assertEqual(us["Azure revenue grew 30%."]["section_path"], "Part II Item 7 > (In millions)")

    def test_levels_keep_the_headings_above(self):
        lv = self.levels({"SEGMENT RESULTS OF OPERATIONS": 1, "Reportable Segments": 2, "Intelligent Cloud": 3,
                          "(In millions)": 0, "More Personal Computing": 3, "OPERATING EXPENSES": 1})
        cleaned = clean.clean_section(section(self.paragraphs), set())
        us = by_text(segment.segment_section(cleaned, settings()["segment"], lv))
        self.assertEqual(us["Azure revenue grew 30%."]["section_path"],
                         "Part II Item 7 > SEGMENT RESULTS OF OPERATIONS > Reportable Segments > Intelligent Cloud")
        self.assertEqual(us["Revenue decreased 2%."]["heading_path"],
                         ["SEGMENT RESULTS OF OPERATIONS", "Reportable Segments", "More Personal Computing"])
        self.assertEqual(us["Costs rose 4%."]["section_path"], "Part II Item 7 > OPERATING EXPENSES")
        self.assertEqual(us["(In millions)"]["heading_level"], 0)


class TableNotes(unittest.TestCase):
    def test_basis_points_legend_singular_and_plural(self):
        for text in ["bp - basis point", "bp - basis points"]:
            self.assertEqual(segment.prefilter(text, 4), "table_residue", text)


class PageHeaders(unittest.TestCase):
    def sections(self, line: str, company: str) -> list[dict]:
        out = []
        for k in range(3):
            sec = section([line, f"Revenue in part {k} increased.", line, "Costs fell.", "Margins rose."],
                          section_key=f"s{k}")
            sec["company_name"] = company
            out.append(sec)
        return out

    def test_segment_heading_repeated_across_items_is_kept(self):
        # Microsoft: "More Personal Computing" in Items 1, 7 and 8 is a heading, not a page header.
        secs = self.sections("More Personal Computing", "Microsoft Corporation")
        self.assertEqual(clean.repeated_headers(secs, settings()["clean"]), set())

    def test_company_name_header_is_removed(self):
        secs = self.sections("McKESSON CORPORATION", "McKESSON CORPORATION")
        self.assertEqual(clean.repeated_headers(secs, settings()["clean"]), {"McKESSON CORPORATION"})


class SentenceLevel(unittest.TestCase):
    def test_level_minus_one_makes_a_heading_a_sentence(self):
        line = "We may be adversely affected by the effects of inflation"
        paras = ["General Risk Factors", line, "Inflation raised our costs in fiscal 2023."]
        cleaned = clean.clean_section(section(paras, section_type="risk_factors", section_key="part-i-item-1a"), set())
        p = cleaned["paragraphs"][1]
        before = by_text(segment.segment_section(cleaned, settings()["segment"]))
        self.assertEqual(before[line]["role"], "heading")
        lv = {segment.heading_id(cleaned, p, p["start"]): segment.SENTENCE_LEVEL}
        after = by_text(segment.segment_section(cleaned, settings()["segment"], lv))
        self.assertEqual((after[line]["role"], after[line]["prefilter"]), ("body", None))
