"""Step 5 review: are the risk categories complete (category_checks), and why Item 1A units
have no heading group (no_group_reasons). No network, no API."""

import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.risk import factors as risk_factors

FILINGS = """filings:
  - {ticker: TST, form: 10-K, reporting_period_end: "2022-12-31", accession: A1}
  - {ticker: TST, form: 10-K, reporting_period_end: "2023-12-31", accession: A2}
  - {ticker: TST, form: 10-K, reporting_period_end: "2024-12-31", accession: A3}
  - {ticker: TST, form: 10-Q, reporting_period_end: "2025-03-31", accession: Q1}
  - {ticker: TST, form: 10-Q, reporting_period_end: "2025-06-30", accession: Q2}
  - {ticker: TST, form: 10-Q, reporting_period_end: "2025-09-30", accession: Q3}
"""
MAP = {"business risks": {"groups": [], "status": "broad"},
       "legal risks": {"groups": ["legal_regulatory"], "status": "group"},
       "risk factor summary": {"groups": [], "status": "not_a_category"}}


class Units:
    def __init__(self):
        self.units, self.n = [], 0

    def add(self, acc, text, role="body", level=None, **kw):
        self.n += 1
        u = {"unit_id": f"u{self.n}", "accession_number": acc, "ticker": "TST", "form": "10-K",
             "section_key": "part-i-item-1a", "section_type": "risk_factors", "role": role, "text": text,
             "prefilter": "heading" if role == "heading" else None, "risk_summary": False, **kw}
        if level is not None:
            u["heading_level"] = level
        self.units.append(u)
        return u

    def category(self, acc, text, n_body=3, **kw):
        self.add(acc, text, "heading", 1, **kw)
        for i in range(n_body):
            self.add(acc, f"{text} sentence {i}.", **kw)


class TestCategoryChecks(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("PIPELINE_FILINGS")
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(FILINGS)
        f.close()
        self.path = Path(f.name)
        os.environ["PIPELINE_FILINGS"] = str(self.path)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("PIPELINE_FILINGS", None)
        else:
            os.environ["PIPELINE_FILINGS"] = self.saved
        self.path.unlink()

    def run_checks(self, u):
        categories, assignments = risk_factors.assign(u.units, MAP)
        return categories, assignments, risk_factors.category_checks(u.units, categories)

    def test_year_gap(self):
        """Legal Risks in 2022 and 2024 but not 2023: flagged on 2023 only."""
        u = Units()
        for acc in ("A1", "A2", "A3"):
            u.category(acc, "Business Risks")
            if acc != "A2":
                u.category(acc, "Legal Risks")
        for acc in ("Q1", "Q3"):                                              # a 10-Q gap (Q2) is not flagged
            u.category(acc, "Quarterly Risks", form="10-Q", section_key="part-ii-item-1a")
        u.category("Q2", "Other Risks", form="10-Q", section_key="part-ii-item-1a")
        _, _, rows = self.run_checks(u)
        gaps = [r for r in rows if r["check"] == "year_gap"]
        self.assertEqual([(r["accession_number"], r["text"], r["reporting_period_end"]) for r in gaps],
                         [("A2", "Legal Risks", "2023-12-31")])

    def test_summary_heading_below_1_and_suspect(self):
        u = Units()
        u.add("A1", "Risk Factor Summary", "heading", 1, risk_summary=True)
        u.add("A1", "Business Risks", "heading", 2, risk_summary=True)
        u.add("A1", "Legal Risks", "heading", 2, risk_summary=True)       # not in the full text
        u.add("A1", "Short summary item.", risk_summary=True)
        u.category("A1", "Business Risks")
        u.add("A1", "Risks Related to Our Stock", "heading", 2)              # a category at level 2?
        u.add("A1", "Product Quality", "heading", 2)                         # an ordinary sub-heading
        u.category("A1", "Competition", n_body=1)                            # no 'risk', 1 unit
        _, _, rows = self.run_checks(u)
        got = Counter(r["check"] for r in rows)
        self.assertEqual(got, {"summary_mismatch": 1, "risk_heading_below_1": 1, "suspect_category": 1})
        by_check = {r["check"]: r for r in rows}
        self.assertEqual(by_check["summary_mismatch"]["text"], "Legal Risks")
        self.assertEqual(by_check["risk_heading_below_1"]["text"], "Risks Related to Our Stock")
        self.assertEqual(by_check["suspect_category"]["detail"], "1 units; no 'risk'")

    def test_no_group_reasons(self):
        u = Units()
        u.add("A1", "Investing in our stock is risky.")                      # before the first category
        u.add("A1", "Risk Factor Summary", "heading", 1, risk_summary=True)
        u.add("A1", "We face many risks.", risk_summary=True)
        u.category("A1", "Business Risks", n_body=2)                         # broad: no group
        u.category("A1", "Legal Risks", n_body=2)
        u.category("A1", "Operating Risks", n_body=2)                        # unmapped
        u.add("A2", "No material changes.", section_key="part-ii-item-1a")   # no category at all
        categories, assignments = risk_factors.assign(u.units, MAP)
        reasons = risk_factors.no_group_reasons(u.units, categories, assignments)
        self.assertEqual(reasons, {"before_first_category": 1, "risk_summary": 1, "unmapped": 2,
                                   "broad": 2, "section_without_category": 1})
        self.assertEqual(sum(bool(a["heading_risk_group"]) for a in assignments), 2)


if __name__ == "__main__":
    unittest.main()
