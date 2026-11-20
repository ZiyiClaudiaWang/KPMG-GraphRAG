"""tools/build_fact_store.pick_primary: choosing the filing's own document from an EDGAR listing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_fact_store as b  # noqa: E402

FOLDER = "/Archives/edgar/data/789019/000156459021002316/"
SITE_LINKS = ('<a href="/index.htm">Home</a><a href="/edgar/search.htm">Search</a>'
              '<a href="/divisions/brokers.htm">Brokers</a>')


def listing(*names):
    return SITE_LINKS + "".join(f'<a href="{FOLDER}{n}">{n}</a>' for n in names)


class TestPickPrimary(unittest.TestCase):
    def test_agent_style_name_is_found_and_site_pages_ignored(self):
        html = listing("msft-10q_20201231.htm", "msft-ex311_10.htm", "R1.htm")
        self.assertEqual(b.pick_primary(html, "789019", "0001564590-21-002316"), "msft-10q_20201231.htm")

    def test_usual_name_still_wins(self):
        html = listing("msft-ex21.htm", "msft-20250630.htm")
        self.assertEqual(b.pick_primary(html, "789019", "0001564590-21-002316"), "msft-20250630.htm")

    def test_inline_viewer_link_counts(self):
        html = SITE_LINKS + f'<a href="/ix?doc={FOLDER}aapl-20220625.htm">doc</a>'
        self.assertEqual(b.pick_primary(html, "789019", "0001564590-21-002316"), "aapl-20220625.htm")

    def test_only_site_pages_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            b.pick_primary(SITE_LINKS, "789019", "0001564590-21-002316")


if __name__ == "__main__":
    unittest.main()
