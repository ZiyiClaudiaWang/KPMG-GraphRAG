"""The loader skips what the extraction pipeline skips."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "structured"))

import fact_loader_data_api as loader  # noqa: E402

YAML = """
filings:
  - {accession: A-1, ticker: MSFT, status: passed}
  - {accession: A-2, ticker: CAH, status: on_hold}
  - {accession: A-3, ticker: AAPL}
"""


def test_on_hold_filings_are_left_out(tmp_path):
    """A company the Data team is still fixing is skipped by every extraction
    step. Loading it here would put a filer in the structured arm that the
    graph arm has no claims for at all."""
    path = tmp_path / "filings.yaml"
    path.write_text(YAML)
    assert loader.accessions_from(str(path)) == ["A-1", "A-3"]


def test_a_row_with_no_status_counts_as_passed(tmp_path):
    path = tmp_path / "filings.yaml"
    path.write_text(YAML)
    assert "A-3" in loader.accessions_from(str(path))


def test_on_hold_can_be_asked_for(tmp_path):
    path = tmp_path / "filings.yaml"
    path.write_text(YAML)
    assert loader.accessions_from(str(path), include_on_hold=True) == ["A-1", "A-2", "A-3"]
