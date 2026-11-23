import unittest

from src.report_claim_edits import edit, pairs


class ClaimEdits(unittest.TestCase):
    def test_single_edits(self):
        for a, b in [("may change", "could change"), ("in 2025", "in 2026"),
                     ("may change", "may not change"), ("may not change", "may change")]:
            self.assertIsNotNone(edit(a.split(), b.split()))
        self.assertIsNone(edit(["same"], ["same"]))
        self.assertIsNone(edit(["both", "changed"], ["all", "different"]))

    def test_only_cross_filing_same_issuer_and_section(self):
        def claim(uid, acc, quote, cik="1", section="risk_factors"):
            return dict(claim_id=uid, accession_number=acc, quote=quote, section_id=uid,
                        section_type=section, provenance={"cik": cik})
        a = "Our costs may increase during the period."
        b = a.replace("may", "could")
        rows = [claim("one", "A", a), claim("two", "B", b)]
        for other in [claim("three", "A", b), claim("four", "B", b, cik="2"),
                      claim("five", "B", b, section="mda"), claim("six", "B", a)]:
            self.assertEqual(list(pairs([rows[0], other])), [])
        result, = pairs(rows)
        self.assertEqual(result["edit"], {"left": ["may"], "right": ["could"]})
        self.assertEqual((result["left"]["claim_id"], result["right"]["claim_id"]), ("one", "two"))
