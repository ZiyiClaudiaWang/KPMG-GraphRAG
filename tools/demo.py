"""Run the whole pipeline in front of an audience, one station at a time.

    python3 tools/demo.py              # run every station
    python3 tools/demo.py --pause      # wait for Enter between stations
    python3 tools/demo.py --only 3 5   # just those stations

Each station prints what it ran and what came back. Nothing here reformats or
summarises the output: the point is that the numbers on the slides are the
numbers the code produces.

The graph arm needs Neo4j credentials. When they are missing or wrong the
station says so in one line and the demo carries on, because an unreachable
store is a normal state in this contract, not a crash.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "structured"))

# The store the structured arm reads. A wrong-year file is tracked on main and
# is the silent default, so the demo picks the path itself and prints it.
DEFAULT_STORE = Path.home() / "Downloads" / "corpus_facts.duckdb"

# Where the Neo4j credentials might be. The walkthrough worktree has no .env of
# its own, so the main checkout is the fallback.
ENV_CANDIDATES = (
    ROOT / "extraction_pipeline" / ".env",
    Path.home() / "Projects" / "KPMG-GraphRAG" / "extraction_pipeline" / ".env",
)

DEMO_QUESTION = "Intelligent Cloud revenue"


def heading(number: int, title: str) -> None:
    print(f"\n{'=' * 72}\n{number}. {title}\n{'=' * 72}")


def shell(command: str) -> None:
    print(f"\n$ {command}\n")


def resolve_store() -> str:
    """Decide which DuckDB file to read, and say so out loud."""
    chosen = os.environ.get("MSFT_FACT_DB_PATH") or str(DEFAULT_STORE)
    chosen = os.path.expanduser(chosen)
    if not Path(chosen).exists():
        sys.exit(
            f"No fact store at {chosen}.\n"
            "Build one: python3 tools/build_fact_store.py --output "
            f"{DEFAULT_STORE}"
        )
    os.environ["MSFT_FACT_DB_PATH"] = chosen
    return chosen


def load_env() -> Path | None:
    """Read Neo4j settings out of a .env into the environment.

    Values are never printed. Only the path they came from is.
    """
    for candidate in ENV_CANDIDATES:
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return candidate
    return None


def station_1_store(store: str) -> None:
    """What is loaded, and which build of it."""
    heading(1, "The corpus")
    import duckdb

    shell(f"duckdb {store}")
    conn = duckdb.connect(store, read_only=True)
    try:
        facts, companies, filings = conn.execute(
            "SELECT count(*), count(DISTINCT cik), count(DISTINCT accession_number) "
            "FROM financial_facts_v1"
        ).fetchone()
        print(f"  {filings} filings, {companies} companies, {facts:,} canonical facts")
        try:
            version = conn.execute(
                "SELECT value FROM store_meta_v1 WHERE key = 'corpus_version'"
            ).fetchone()
            if version:
                print(f"  corpus_version {version[0]}")
        except duckdb.CatalogException:
            print("  this store predates corpus_version")
        rows = conn.execute(
            "SELECT ticker, count(*) FROM financial_facts_v1 "
            "GROUP BY ticker ORDER BY 2 DESC"
        ).fetchall()
        print("  " + ", ".join(f"{t} {n:,}" for t, n in rows))
    finally:
        conn.close()


def station_2_envelope() -> None:
    """One shape for all three arms. Show the whole thing once."""
    heading(2, "The contract — one envelope, whoever answered")
    from graphrag import retrieve
    import structured_query_v1

    retrieve.configure_structured_backend(structured_query_v1.structured_search)
    shell(
        'retrieve.structured_search("Intelligent Cloud revenue", '
        'filters={"ticker": "MSFT"}, top_k=1)'
    )
    top = retrieve.structured_search(
        DEMO_QUESTION, filters={"ticker": "MSFT"}, top_k=1, trace_id="demo-001"
    )[0]
    print(json.dumps(top, indent=2, default=str))
    print(
        "\n  chunk_id and section_id are null and metadata.fact_id is set, so "
        "exactly\n  one provenance key identifies this evidence. RAG fills "
        "chunk_id instead."
    )


def station_3_question() -> None:
    """The arm reads the question. It used to ignore it."""
    heading(3, "Question matching")
    from graphrag import retrieve
    import structured_query_v1

    retrieve.configure_structured_backend(structured_query_v1.structured_search)
    for question, filters in (
        (DEMO_QUESTION, {"ticker": "MSFT"}),
        ("total revenue", {"ticker": "NKE"}),
        ("cash and cash equivalents", {"ticker": "AAPL"}),
    ):
        shell(f'retrieve.structured_search("{question}", filters={filters}, top_k=3)')
        for item in retrieve.structured_search(question, filters=filters, top_k=3):
            meta = item["metadata"]
            print(
                f'  {item["rank"]}  {item["ticker"]:5} {meta["concept"]:70} '
                f'{meta["value_num"]!s:>18}  {item["reporting_period_end"]}'
            )


def station_4_segments(store: str) -> None:
    """The segment names come out of the store, not a hardcoded list."""
    heading(4, "The segment dictionary")
    import duckdb
    import segments

    conn = duckdb.connect(store, read_only=True)
    try:
        index = segments.load_dictionary(conn, store)
        labels = segments.known_labels()
        print(f"  {len(labels)} segment members found in the store\n")
        print("  " + "\n  ".join(labels[:12]))
        if len(labels) > 12:
            print(f"  ... and {len(labels) - 12} more")

        # The old filter hardcoded three Microsoft members, so a question about
        # any other company's segments returned nothing.
        for wanted in ("Intelligent Cloud", "Cardiovascular"):
            shell(f'segments.resolve(conn, "{wanted}")')
            try:
                print(f"  {wanted} -> {segments.resolve(conn, wanted, store)}")
            except ValueError as exc:
                print(f"  {exc}")

        shell('segments.resolve(conn, "Cloud Shoes")')
        try:
            segments.resolve(conn, "Cloud Shoes", store)
        except ValueError as exc:
            print(f"  {exc}")
    finally:
        conn.close()
    print("\n  Unknown names answer with the vocabulary, instead of an empty result.")


def station_5_series(store: str) -> None:
    """Three numbers in three filings are one measure over time."""
    heading(5, "Fact series")
    import duckdb
    import fact_series

    conn = duckdb.connect(store, read_only=True)
    try:
        shell(
            "SELECT * FROM financial_facts_v1 WHERE ticker = 'MSFT' "
            "AND concept_name LIKE '%RevenueFromContract%'"
        )
        records = conn.execute(
            "SELECT * FROM financial_facts_v1 WHERE ticker = 'MSFT' "
            "AND concept_name LIKE '%RevenueFromContract%'"
        ).df().to_dict("records")
        tickers = dict(
            conn.execute(
                "SELECT DISTINCT cik, ticker FROM financial_facts_v1"
            ).fetchall()
        )
    finally:
        conn.close()

    built = fact_series.build_series(records, tickers)
    print(f"  {len(records)} facts -> {len(built)} series\n")
    for series in sorted(built, key=lambda s: -len(s["points"]))[:3]:
        print(f'  {series["label"]}')
        for point in series["points"]:
            flag = "  (restated)" if point["restated"] else ""
            print(
                f'    {point["period_start"] or "":10} -> {point["period_end"]:10} '
                f'{point["value"]!s:>18}  reported by {point["reported_by"]}{flag}'
            )
        print()


def station_6_graph() -> None:
    """The graph arm, or a clean account of why it cannot answer."""
    heading(6, "The graph arm")
    from graphrag import retrieve, contract, graph_backend

    retrieve.configure_graph_backend(graph_backend.search)
    shell('retrieve.graph_search("supply chain disruption", top_k=3)')
    try:
        graph_backend.ensure_index()
        results = retrieve.graph_search("supply chain disruption", top_k=3)
        if not results:
            print("  The graph is reachable and holds no matching Claims.")
        for item in results:
            print(f'  {item["rank"]}  {item["ticker"]:5} {item["section"]}')
            print(f'      {item["quote"][:100]}')
    except contract.RetrievalUnavailableError as exc:
        print(f"  unavailable: {exc}")
        print(
            "\n  The wrapper reports this as an outage, not as 'no evidence'. "
            "A caller\n  can tell a dead store from a question with no answer."
        )
    finally:
        graph_backend.close()


def station_7_evals() -> None:
    """The two numbers on the slides, recomputed live."""
    heading(7, "Scoring")
    for script in ("evaluate_concept_match.py", "evaluate_entity_resolution.py"):
        shell(f"python3 tools/{script}")
        # The child writes straight to the terminal, so flush first or its
        # output lands above ours when the demo is piped to a file.
        sys.stdout.flush()
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / script)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'structured'}"},
            check=False,
        )


STATIONS = {
    1: station_1_store,
    2: station_2_envelope,
    3: station_3_question,
    4: station_4_segments,
    5: station_5_series,
    6: station_6_graph,
    7: station_7_evals,
}

# Stations that need to know where the store is.
NEEDS_STORE = {1, 4, 5}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pause", action="store_true",
                        help="wait for Enter between stations")
    parser.add_argument("--only", nargs="+", type=int, metavar="N",
                        help="run only these stations")
    parser.add_argument("--delay", type=float, default=0.0, metavar="SECONDS",
                        help="hold each station on screen, for recording")
    args = parser.parse_args()

    store = resolve_store()
    env_file = load_env()

    print("KPMG GraphRAG — live walkthrough")
    print(f"  repo   {ROOT}")
    print(f"  store  {store}")
    print(f"  env    {env_file or 'none found — the graph arm will be unavailable'}")

    wanted = args.only or sorted(STATIONS)
    for number in wanted:
        station = STATIONS.get(number)
        if station is None:
            print(f"\nNo station {number}.")
            continue
        station(store) if number in NEEDS_STORE else station()
        if number == wanted[-1]:
            continue
        if args.pause:
            input("\n[Enter] ")
        elif args.delay:
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
