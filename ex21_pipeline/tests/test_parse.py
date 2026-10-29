"""Format tests on small made-up Exhibit 21 snippets (not real company data).

Run:  python -m pytest -q tests   (or: python tests/test_parse.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ex21.normalize import entity_key, normalize_jurisdiction  # noqa: E402
from ex21.parse import assign_parents, parse_html, parse_text  # noqa: E402

NESTED_HTML = """
<table>
<tr><td><b>Name of Subsidiary</b></td><td></td><td><b>Jurisdiction of Organization</b></td><td>% Owned</td></tr>
<tr><td>Example Holdings LLC</td><td></td><td>Delaware</td><td>100%</td></tr>
<tr><td style="padding-left:18pt">Example Europe B.V.</td><td></td><td>Netherlands</td><td>100%</td></tr>
<tr><td style="padding-left:36pt">Example GmbH (1)</td><td></td><td>Germany</td><td>51%</td></tr>
<tr><td>Example Services, Inc.*</td><td>&nbsp;</td><td>State of Texas</td><td></td></tr>
</table>"""

TEXT_LIST = """EXHIBIT 21
SUBSIDIARIES OF THE REGISTRANT
Sample Widgets Inc. (Delaware)
Sample Widgets Canada ULC (British Columbia, Canada)
Sample Japan K.K. ........ Japan
"""


def test_nested_html_table():
    rows = assign_parents(parse_html(NESTED_HTML), "Example Corp")
    names = [r["subsidiary_name"] for r in rows]
    assert names == ["Example Holdings LLC", "Example Europe B.V.", "Example GmbH", "Example Services, Inc."]
    assert rows[1]["immediate_parent"] == "Example Holdings LLC"
    assert rows[2]["immediate_parent"] == "Example Europe B.V."
    assert rows[3]["immediate_parent"] == "Example Corp"
    assert rows[2]["ownership_pct"] == 51.0
    assert "footnote_marker_removed" in rows[2]["flags"]
    assert normalize_jurisdiction(rows[3]["jurisdiction_raw"]) == ("United States", "Texas")


def test_plain_text_list():
    rows = parse_text(TEXT_LIST)
    assert [(r["subsidiary_name"], r["jurisdiction_raw"]) for r in rows] == [
        ("Sample Widgets Inc.", "Delaware"),
        ("Sample Widgets Canada ULC", "British Columbia, Canada"),
        ("Sample Japan K.K.", "Japan"),
    ]


def test_jurisdictions_and_keys():
    assert normalize_jurisdiction("Delaware, U.S.") == ("United States", "Delaware")
    assert normalize_jurisdiction("United States") == ("United States", "")
    assert normalize_jurisdiction("England and Wales") == ("United Kingdom", "")
    assert normalize_jurisdiction("British Columbia, Canada") == ("Canada", "British Columbia")
    assert normalize_jurisdiction("Cayman Islands") == ("Cayman Islands", "")
    assert entity_key("Microsoft Ireland Research Unlimited Company") == entity_key("Microsoft Ireland Research")
    assert entity_key("Apple Operations International Limited") != entity_key("Apple Operations Limited")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
