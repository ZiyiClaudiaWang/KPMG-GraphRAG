"""config/filings.yaml rows with `status: on_hold` are listed but skipped by the pipeline.

Run from the pipeline folder:  python -m pytest tests/test_filings_status.py
"""

from __future__ import annotations

from src.common import filings

YAML = """filings:
  - {ticker: AAPL, cik: "0000320193", form: 10-K, reporting_period_end: "2025-09-27", accession: "A", status: passed}
  - {ticker: INTC, cik: "0000050863", form: 10-K, reporting_period_end: "2025-12-27", accession: "B", status: on_hold}
  - {ticker: MSFT, cik: "0000789019", form: 10-K, reporting_period_end: "2025-06-30", accession: "C"}
"""


def test_on_hold_rows_are_skipped_unless_asked_for(tmp_path, monkeypatch):
    path = tmp_path / "filings.yaml"
    path.write_text(YAML, encoding="utf-8")
    monkeypatch.setenv("PIPELINE_FILINGS", str(path))

    assert [f["accession"] for f in filings()] == ["A", "C"]  # no status counts as passed
    assert [f["accession"] for f in filings(include_on_hold=True)] == ["A", "B", "C"]
