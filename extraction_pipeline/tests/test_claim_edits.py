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
        change, = result["edit"]["changes"]
        self.assertEqual((change["operation"], change["left"], change["right"]), ("replace", ["may"], ["could"]))
        self.assertEqual((result["left"]["claim_id"], result["right"]["claim_id"]), ("one", "two"))

    def test_changed_words_at_both_ends(self):
        words = "Our operating costs may increase as a result of the changes described in the annual report for fiscal year 2025".split()
        changed = ["Their", *words[1:-1], "2026"]
        result = edit(words, changed)
        self.assertGreaterEqual(result["similarity"], 0.90)
        self.assertEqual([c["right"] for c in result["changes"]], [["Their"], ["2026"]])
