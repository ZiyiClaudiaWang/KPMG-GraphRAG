"""src/ex21/: the Exhibit 21 library (parse, normalize), tested on small made-up snippets,
with no data folder. Moved from ex21_pipeline/tests/test_parse.py when that folder was retired.
"""
import ast
from pathlib import Path

from src.ex21.normalize import (entity_key, is_registrant, normalised_name, normalize_jurisdiction,
                                sub_id)
from src.ex21.parse import assign_parents, parse_html, parse_text

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


DESCRIBED_TEXT = """SUBSIDIARIES OF SAMPLE STORES, INC.
1. Sample’s Marketing Company, an Iowa corporation
2. Sample Stores, LLC, an Iowa limited liability company
3. Sample Wholesale, LLC, a Texas limited liability company
Each of the subsidiaries listed above is wholly owned, directly or indirectly, by Sample Stores, Inc.
"""

DESCRIBED_HTML = (
    "<html><body><p>SUBSIDIARIES OF SAMPLE STORES, INC.</p>"
    "<p>1. <span>Sample’s</span> Marketing Company, an <font>Iowa</font> corporation</p>"
    "<p>2. Sample Holdings, LLC, a Delaware limited liability company</p></body></html>"
)


def test_described_lines():
    rows = parse_text(DESCRIBED_TEXT)
    assert [(r["subsidiary_name"], r["jurisdiction_raw"]) for r in rows] == [
        ("Sample’s Marketing Company", "Iowa"),
        ("Sample Stores, LLC", "Iowa"),
        ("Sample Wholesale, LLC", "Texas"),
    ]


def test_described_lines_in_html_with_inline_tags():
    rows = parse_html(DESCRIBED_HTML)
    assert [(r["subsidiary_name"], r["jurisdiction_raw"]) for r in rows] == [
        ("Sample’s Marketing Company", "Iowa"),
        ("Sample Holdings, LLC", "Delaware"),
    ]


def test_span_inside_a_name_keeps_the_whole_name():
    # Both readings find one entry; only the block reading keeps "Sample’s".
    html = "<p>1. <span>Sample’s</span> Marketing Company, an Iowa corporation</p>"
    rows = parse_html(html)
    assert [r["subsidiary_name"] for r in rows] == ["Sample’s Marketing Company"]


def test_title_and_group_heading_rows_are_dropped():
    html = ("<table>"
            "<tr><td>Example plc and Subsidiaries</td><td></td></tr>"
            "<tr><td>At April 24, 2026</td><td></td></tr>"
            "<tr><td>UNITED STATES</td><td></td></tr>"
            "<tr><td>Example Holdings, Inc.</td><td>Delaware</td></tr>"
            "<tr><td>INTERNATIONAL</td><td></td></tr>"
            "<tr><td>Example (Australia) Pty. Limited</td><td>Australia</td></tr>"
            "<tr><td>United States Example Corp.</td><td>Ohio</td></tr>"
            "</table>")
    assert [r["subsidiary_name"] for r in parse_html(html)] == [
        "Example Holdings, Inc.", "Example (Australia) Pty. Limited", "United States Example Corp."]


def test_jurisdictions_and_keys():
    assert normalize_jurisdiction("Delaware, U.S.") == ("United States", "Delaware")
    assert normalize_jurisdiction("United States") == ("United States", "")
    assert normalize_jurisdiction("England and Wales") == ("United Kingdom", "")
    assert normalize_jurisdiction("British Columbia, Canada") == ("Canada", "British Columbia")
    assert normalize_jurisdiction("Cayman Islands") == ("Cayman Islands", "")
    assert entity_key("Microsoft Ireland Research Unlimited Company") == entity_key("Microsoft Ireland Research")
    assert entity_key("Apple Operations International Limited") != entity_key("Apple Operations Limited")


def test_stacked_row_is_split_into_one_subsidiary_per_paragraph():
    # NetApp: two subsidiaries in one table row, each cell holding two paragraphs.
    html = ("<table><tr><td><p>Example Saudi Arabia LLC</p><p>Example Regional Headquarter Company</p></td>"
            "<td></td><td><p>Saudi Arabia</p><p>Saudi Arabia</p></td></tr>"
            "<tr><td>Example Japan K.K.</td><td></td><td>Japan</td></tr></table>")
    rows = parse_html(html)
    assert [(r["subsidiary_name"], r["jurisdiction_raw"]) for r in rows] == [
        ("Example Saudi Arabia LLC", "Saudi Arabia"),
        ("Example Regional Headquarter Company", "Saudi Arabia"),
        ("Example Japan K.K.", "Japan"),
    ]
    assert rows[0]["parse_method"] == "html_table:stacked"


def test_stacked_row_with_br_is_split():
    html = ("<table><tr><td>Example One Ltd.<br/>Example Two Ltd.</td>"
            "<td>Ireland<br/>Malta</td></tr></table>")
    assert [(r["subsidiary_name"], r["jurisdiction_raw"]) for r in parse_html(html)] == [
        ("Example One Ltd.", "Ireland"), ("Example Two Ltd.", "Malta")]


def test_name_over_two_lines_is_not_split():
    # Two paragraphs in the name cell but one jurisdiction: one long name, not two entities.
    html = ("<table><tr><td><p>Example Very Long Holdings</p><p>International Limited</p></td>"
            "<td><p>Ireland</p></td></tr></table>")
    assert len(parse_html(html)) == 1


def test_canadian_provinces_map_to_canada():
    assert normalize_jurisdiction("Ontario") == ("Canada", "Ontario")
    assert normalize_jurisdiction("Quebec") == ("Canada", "Quebec")
    assert normalize_jurisdiction("Province of Quebec") == ("Canada", "Quebec")
    assert normalize_jurisdiction("British Columbia") == ("Canada", "British Columbia")
    assert normalize_jurisdiction("British Columbia, Canada") == ("Canada", "British Columbia")


def test_bwi_and_other_spellings():
    assert normalize_jurisdiction("Cayman Islands, B.W.I.") == ("Cayman Islands", "")
    assert normalize_jurisdiction("Honduras") == ("Honduras", "")
    assert normalize_jurisdiction("Republic of Korea") == ("South Korea", "")


def test_unmapped_value_leaves_country_empty():
    assert normalize_jurisdiction("Atlantis") == ("", "")
    assert normalize_jurisdiction("") == ("", "")


def test_registrant_row_detection_keeps_legal_forms():
    assert is_registrant("The Coca-Cola Company", "COCA COLA CO")
    assert is_registrant("McKesson Corporation", "MCKESSON CORP")
    assert is_registrant("Take-Two Interactive Software, Inc.", "TAKE TWO INTERACTIVE SOFTWARE INC")
    assert not is_registrant("Medtronic, Inc.", "MEDTRONIC PLC")       # a real subsidiary
    assert not is_registrant("Coca-Cola Refreshments USA, Inc.", "COCA COLA CO")
    assert not is_registrant("Anything Inc.", "")


def test_name_keys_differ_on_legal_form():
    assert normalised_name("Apple Asia LLC") != normalised_name("Apple Asia Limited")
    assert entity_key("Apple Asia LLC") == entity_key("Apple Asia Limited")


def test_sub_id_is_parent_cik_and_normalised_name():
    a = sub_id("0000000001", "Table Example Asia LLC")
    assert a == sub_id("1", "TABLE EXAMPLE ASIA LLC")              # CIK padded, case ignored
    assert a == sub_id("0000000001", "Table  Example Asia LLC.")
    assert a != sub_id("0000000001", "Table Example Asia Limited")
    assert a != sub_id("0000000002", "Table Example Asia LLC")


def test_library_imports_nothing_from_the_pipeline():
    """src/ex21/ may import its own modules (from .parse) but not the pipeline (from ..common)."""
    folder = Path(__file__).resolve().parents[1] / "src" / "ex21"
    for path in folder.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level <= 1, f"{path.name} imports from the pipeline package"
                assert not (node.module or "").startswith("src"), f"{path.name} imports src"
