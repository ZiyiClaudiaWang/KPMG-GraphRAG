"""Which filers each arm can answer about, and in which periods.

A caller deciding whether to send us a question needs to know whether we hold
the company at all. Today that list is kept by hand on the other side —
the Agent team's `COMPANY_TICKERS` carries the comment *"keep this in sync
with the ticker universe the RAG team actually retrieves"*, which is a
promise someone has to remember to keep. This reads it from the arms instead.

Each arm answers for itself, the way `vocabulary.py` does. The two can differ:
they are loaded from different sources, and at 313 filings they will. An arm
that is not configured is reported as such rather than guessed at.

The fiscal labels come from each arm's own calendar, so they are the labels
that arm will accept in a filter — not a calendar year range that looks right
and matches nothing.

    from graphrag import corpus
    corpus.corpus("structured")["filers"][0]
    # {'ticker': 'AAPL', 'cik': '0000320193', ... 'fiscal_years': [2025, 2026]}
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_STRUCTURED_DIR = Path(__file__).resolve().parent.parent / "structured"


# A reader per arm, so a server can serve a corpus that matches the arms it
# registered. retrieve.py takes backends the same way. Without this the tools
# can answer from the mock while this module describes the real graph, and a
# consumer integrating against the mock reads "we hold nothing" beside rows
# that plainly exist.
_READERS: dict[str, object] = {}


def configure_reader(arm: str, reader) -> None:
    """Read one arm's corpus from `reader` instead of from the live store."""
    if reader is None:
        _READERS.pop(arm, None)
    else:
        _READERS[arm] = reader


def _structured_module():
    if str(_STRUCTURED_DIR) not in sys.path:
        sys.path.insert(0, str(_STRUCTURED_DIR))
    return importlib.import_module("structured_query_v1")


def _years(calendar: dict, ticker: str) -> list[int]:
    """The fiscal years an arm will accept for one filer.

    A label is `FY 2026` or `Q3 2026`; the year is what a caller sends as
    `fiscal_year`. A year in progress has quarters and no `FY` entry, and it
    is still filterable, so both count.
    """
    years = set()
    for label in calendar.get(ticker, {}):
        tail = label.rsplit(" ", 1)[-1]
        if tail.isdigit():
            years.add(int(tail))
    return sorted(years)


# What a caller needs to know about where an answer came from. `source`
# distinguishes Data's validated view from their unvalidated sets, and on an
# audit-flavoured project that caveat has to travel with the corpus rather
# than live in a handover note. The EDGAR loader writes `corpus_version`
# alone, so absence of `source` identifies it.
_STORE_META_KEYS = ("corpus_version", "source", "loader_version")


def _graph_corpus() -> dict:
    graph_backend = importlib.import_module("graphrag.graph_backend")
    driver = graph_backend.connect()
    with driver.session(database=graph_backend._database) as session:
        rows = session.run(
            "MATCH (co:Company)-[:FILES]->(fl:Filing) "
            "WHERE co.ticker IS NOT NULL "
            "RETURN co.ticker AS ticker, co.cik AS cik, co.name AS company_name, "
            "collect(DISTINCT fl.form) AS forms, count(fl) AS filings, "
            "min(fl.reporting_period_end) AS earliest, "
            "max(fl.reporting_period_end) AS latest "
            "ORDER BY ticker"
        ).data()
    calendar = graph_backend._calendar(driver)
    report = _assemble(rows, calendar)
    # The graph is loaded by the extraction pipeline, which stamps its scope
    # on the nodes rather than a meta table.
    with driver.session(database=graph_backend._database) as session:
        scopes = session.run(
            "MATCH (c:Claim) WHERE c.pipeline_scope IS NOT NULL "
            "RETURN DISTINCT c.pipeline_scope AS scope"
        ).value()
    report["corpus_version"] = ", ".join(sorted(s for s in scopes if s)) or None
    return report


def _structured_corpus() -> dict:
    import duckdb

    structured_query = _structured_module()
    conn = duckdb.connect(structured_query._store_path(), read_only=True)
    try:
        # filings_v1 records when a filing was submitted, not what period it
        # covers; the period lives on the facts. A caller filtering on
        # reporting_period_end is filtering on the latter, so report that.
        rows = conn.execute(
            "SELECT g.ticker, g.cik, any_value(g.company_name) AS company_name, "
            "list(DISTINCT g.form) AS forms, "
            "count(DISTINCT g.accession_number) AS filings, "
            "min(f.period_end) AS earliest, max(f.period_end) AS latest "
            "FROM filings_v1 g JOIN financial_facts_v1 f USING (accession_number) "
            "WHERE g.ticker IS NOT NULL "
            "GROUP BY g.ticker, g.cik ORDER BY g.ticker"
        ).fetchall()
        columns = ["ticker", "cik", "company_name", "forms", "filings",
                   "earliest", "latest"]
        records = [dict(zip(columns, row)) for row in rows]
        calendar = structured_query._calendar(conn)
        meta = dict(conn.execute("SELECT key, value FROM store_meta_v1").fetchall())
    finally:
        conn.close()
    report = _assemble(records, calendar)
    report.update({k: meta.get(k) for k in _STORE_META_KEYS})
    return report


def _assemble(rows, calendar: dict) -> dict:
    filers = []
    for row in rows:
        filers.append({
            "ticker": row["ticker"],
            # The two arms load from different sources and spell a name
            # differently — `Microsoft Corporation` and `MICROSOFT CORP` are
            # the same filer. The cik is what joins them.
            "cik": str(row["cik"]) if row["cik"] is not None else None,
            "company_name": row["company_name"],
            "forms": sorted(f for f in (row["forms"] or []) if f),
            "filings": int(row["filings"]),
            "fiscal_years": _years(calendar, row["ticker"]),
            "reporting_period_end": {"earliest": str(row["earliest"]),
                                     "latest": str(row["latest"])},
        })
    return {
        "filers": filers,
        "tickers": [f["ticker"] for f in filers],
        "filings": sum(f["filings"] for f in filers),
    }


def _why(exc: Exception) -> str:
    """Enough to act on, without saying where the arm lives.

    The exception text names the Neo4j URI or the store path: a driver error
    carries the host, and on Aura the instance id in the host is also the
    username. This report is read by other teams at startup and logged by
    whoever runs it, so the detail goes to stderr and the caller gets the
    kind of failure.
    """
    print(f"corpus: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return (f"{type(exc).__name__}: the arm could not be read. This is a "
            "configuration or connection problem, not an empty corpus. The "
            "reason is in the server log.")


def corpus(arm: str | None = None) -> dict:
    """What each arm holds. Read from the arms, so it cannot go stale."""
    report: dict[str, dict] = {}

    for name, default in (("graph", _graph_corpus), ("structured", _structured_corpus)):
        read = _READERS.get(name, default)
        try:
            report[name] = read()
        except Exception as exc:
            # One arm being unreachable is not a reason to answer nothing
            # about the other. Name the kind of failure rather than return an
            # empty list, which would read as "we hold nothing".
            report[name] = {"unavailable": _why(exc)}

    if arm is not None:
        if arm not in report:
            raise ValueError(f"no such arm: {arm}. Try one of: {', '.join(sorted(report))}")
        return report[arm]
    return report


def summary() -> str:
    """The same report as text, for pasting into a reply to another team."""
    lines = ["Corpus, by arm:"]
    for arm, detail in corpus().items():
        if "unavailable" in detail:
            lines.append(f"  {arm}: {detail['unavailable']}")
            continue
        lines.append(f"  {arm}: {len(detail['filers'])} filers, {detail['filings']} filings")
        for filer in detail["filers"]:
            years = ", ".join(str(y) for y in filer["fiscal_years"]) or "none"
            lines.append(f"    {filer['ticker']:<6} {'/'.join(filer['forms']):<12} "
                         f"FY {years}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
