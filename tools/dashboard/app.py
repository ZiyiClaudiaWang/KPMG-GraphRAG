"""The corpus dashboard: what is loaded, whether it holds together, and how it ranks.

    pip install streamlit
    streamlit run tools/dashboard/app.py

Team-local. Everyone runs it against their own environment; there is no hosting
and no shared credentials. If it ever needs to face a client that is a separate
decision with a separate answer on credentials.

Three tabs:

    Corpus   what is in each arm, per filing, with the provenance of where it
             came from
    Checks   the things that should be true and whether they are, each one
             drillable to the rows that failed
    Ask      run a question through both arms and watch the ranking, which is
             how the period-word bug and the street-address bug were both found

Everything it displays comes from tools/corpus_report.py, so the figures here,
the ones pytest asserts on and the ones the deck prints are the same figures.
This file holds the rendering and nothing else.

Refresh is a button, never page load. Aura cold-starts, and a page that queries
on open reports retrieval as down every morning.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import corpus_report as cr  # noqa: E402

QUERY_LOG = ROOT / "data" / "dashboard" / "query_log.jsonl"

st.set_page_config(page_title="KPMG GraphRAG corpus", layout="wide")


# --------------------------------------------------------------------------
# data


@st.cache_data(show_spinner="Reading both arms…")
def load_report(instance: str, scope: str | None, nonce: int) -> dict:
    """The report, cached until the refresh button changes the nonce.

    The nonce is the whole reason this is cached by argument rather than by
    time: nothing re-queries Aura unless someone asks it to.
    """
    return cr.build(instance, None, scope)


def env_for(instance: str) -> dict[str, str]:
    return cr.read_env_file(cr.INSTANCES[instance])


def configure_arms(instance: str) -> dict[str, str]:
    """Point the retrieval arms at the selected instance.

    backends.py reads os.environ when it registers, so the environment has to
    be set before asking it anything. configure_from_env() is safe to call
    again, which is what makes switching instances in the sidebar work.
    """
    env = env_for(instance)
    for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE",
                "MSFT_FACT_DB_PATH"):
        if env.get(key):
            os.environ[key] = env[key]
    from graphrag import backends

    return backends.configure_from_env()


def append_query_log(row: dict) -> None:
    QUERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with QUERY_LOG.open("a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


def read_query_log(limit: int = 200) -> list[dict]:
    if not QUERY_LOG.exists():
        return []
    lines = QUERY_LOG.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# --------------------------------------------------------------------------
# sidebar


st.sidebar.title("Corpus dashboard")

instance = st.sidebar.radio(
    "Graph instance",
    options=sorted(cr.INSTANCES),
    index=sorted(cr.INSTANCES).index("internal"),
    help="internal is the team's own graph. canonical is what Eval and Agents are given.",
)

if not cr.INSTANCES[instance].exists():
    st.sidebar.error(f"No credentials at {cr.INSTANCES[instance].name}")

scope_override = st.sidebar.text_input("pipeline_scope", value="",
                                       help="Leave empty for the only scope present.")

if "nonce" not in st.session_state:
    st.session_state.nonce = 0
if st.sidebar.button("Refresh", type="primary", use_container_width=True):
    st.session_state.nonce += 1

st.sidebar.caption(
    "Nothing re-queries the graph until you press Refresh. Aura cold-starts, so "
    "the first read after an idle period takes a few seconds."
)

try:
    report = load_report(instance, scope_override or None, st.session_state.nonce)
except SystemExit as exc:
    st.error(str(exc))
    st.stop()

graph = report.get("graph") or {}
store = report.get("store") or {}


# --------------------------------------------------------------------------
# header


st.title("What is loaded, and whether it holds together")

for warning in graph.get("warnings", []):
    st.warning(warning, icon="⚠️")

with st.container(border=True):
    for line in report["provenance"]:
        st.caption(line)

corpus_tab, checks_tab, ask_tab = st.tabs(["Corpus", "Checks", "Ask"])


# --------------------------------------------------------------------------
# corpus


with corpus_tab:
    left, right = st.columns(2)

    with left:
        st.subheader("Graph arm")
        if graph.get("scope"):
            nodes = graph["nodes_by_label"]
            edges = graph["relationships_by_type"]
            st.dataframe(
                pd.DataFrame({"label": list(nodes), "nodes": list(nodes.values())}),
                hide_index=True, use_container_width=True)
            st.dataframe(
                pd.DataFrame({"relationship": list(edges), "edges": list(edges.values())}),
                hide_index=True, use_container_width=True)

            subs = graph["subsidiaries"]
            st.metric(
                "Subsidiary coverage",
                f"{subs['parents_with_subsidiaries']} / {subs['ten_k_filings']}",
                help="Parents with subsidiaries, against 10-K filings. Only a 10-K "
                     "carries an Exhibit 21, so a 10-Q with none is not a gap.")

            spread = graph["spread"]["claims_per_section"]
            if spread.get("n"):
                st.caption(
                    f"Claims per section: min {spread['min']} · median "
                    f"{spread['median']} · max {spread['max']} over {spread['n']} "
                    "sections. A wide spread means the extractor treated sections "
                    "differently, which is where recall dies as the corpus grows.")
        else:
            st.info("No graph configured for this instance.")

    with right:
        st.subheader("Structured arm")
        if store:
            totals = store["totals"]
            a, b, c, d = st.columns(4)
            a.metric("Filings", f"{totals['filings']:,}")
            b.metric("Companies", f"{totals['companies']:,}")
            c.metric("Facts", f"{totals['facts']:,}")
            d.metric("Concepts", f"{totals['concepts']:,}")

            st.caption("Per filing. Totals hide a filing that loaded thin or broke.")
            st.dataframe(pd.DataFrame(store["per_filing"]), hide_index=True,
                         use_container_width=True)
        else:
            st.info("No store configured. Set MSFT_FACT_DB_PATH.")

    if store:
        st.subheader("Key concepts")
        st.caption(
            "Does every filing carry the figures a client asks for first. One red "
            "cell at two hundred filings is a bug that otherwise surfaces as a "
            "failed eval question weeks later.")
        matrix = pd.DataFrame(store["key_concepts"]["present"]).T
        st.dataframe(matrix, use_container_width=True)

        st.subheader("Segments")
        st.caption(
            "Consolidated against dimensioned, per concept. Both live ranking bugs "
            "are segment-shaped: slices outscoring the consolidated figure and "
            "pushing it past top_k, and a slice standing in for a company total.")
        mix = store["dimension_mix"]
        st.dataframe(pd.DataFrame(mix["by_concept"]), hide_index=True,
                     use_container_width=True)
        st.caption("Axes in play")
        st.dataframe(
            pd.DataFrame({"axis": list(mix["axes"]), "facts": list(mix["axes"].values())}),
            hide_index=True, use_container_width=True)

    rows = report.get("cross_arm") or []
    if rows:
        st.subheader("Cross arm")
        st.caption(
            "A filing present in one arm and missing from another makes eval "
            "scores incomparable. RAG's index is in another repo, so that column "
            "is empty by choice rather than by failure.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------
# checks


def check_row(name: str, value, ok: bool, note: str) -> dict:
    # Value is rendered as text because the column holds counts and the odd list
    # of column names. Mixing the two makes Arrow coerce the column and the
    # table comes out looking wrong.
    shown = f"{value:,}" if isinstance(value, int) else str(value)
    return {"check": name, "value": shown, "status": "pass" if ok else "look",
            "what it catches": note}


with checks_tab:
    if not store and not graph:
        st.info("Nothing configured.")

    checks: list[dict] = []

    if graph.get("scope"):
        for name, count in graph["orphans"].items():
            checks.append(check_row(
                f"graph · {name.replace('_', ' ')}", count, count == 0,
                "An unattached node is unreachable by every query the arm makes."))
        identity = graph["identity"]
        checks.append(check_row(
            "graph · companies with no ticker", identity["no_ticker"],
            identity["no_ticker"] == 0,
            "No ticker means the arm can answer about the wrong company."))
        checks.append(check_row(
            "graph · CIKs with more than one name",
            len(identity["ciks_with_multiple_names"]),
            not identity["ciks_with_multiple_names"],
            "Name variants for one company. Entity resolution recall is 0.789."))

    if store:
        dups = store["duplicates"]
        checks.append(check_row(
            "store · repeated fact_id", dups["repeated_fact_id"],
            dups["repeated_fact_id"] == 0,
            "The same identity twice is a loader bug. Should be zero."))
        checks.append(check_row(
            "store · statement duplicates", dups["statement_duplicates"], True,
            "Same concept, period and dimensions under more than one context in "
            "one filing. Usually benign."))
        checks.append(check_row(
            "store · comparative groups", dups["comparative_groups"], True,
            "Last year's figure restated inside this year's report. Expected, and "
            "must not be deduped away."))
        checks.append(check_row(
            "store · comparatives that disagree on value", dups["restated_groups"],
            dups["restated_groups"] == 0,
            "A restatement, or two loaders disagreeing. Nobody has looked at these."))

        for name, count in store["dates"].items():
            checks.append(check_row(
                f"store · {name.replace('_', ' ')}", count, count == 0,
                "A period that cannot be right for the form it sits on."))

        columns = store["column_population"]
        empty = [c for c, v in columns.items() if c != "__numeric__" and v.get("all_null")]
        checks.append(check_row(
            "store · columns entirely NULL", ", ".join(empty) or "none", not empty,
            "An all-null column gets no type, which is the same shape as the bug "
            "where an empty first filing defined the schema for every filing after."))
        numeric = columns.get("__numeric__", {})
        if numeric:
            checks.append(check_row(
                "store · parseable numbers left empty", numeric["unparsed_numbers"],
                numeric["unparsed_numbers"] == 0,
                "raw_value holds a number and numeric_value does not. The other "
                f"{numeric['no_numeric_value'] - numeric['unparsed_numbers']:,} are "
                "text blocks and document metadata, which correctly have none."))

        missing = store["key_concepts"]["missing"]
        checks.append(check_row(
            "store · filings missing a key concept",
            sum(len(v) for v in missing.values()) or 0, not missing,
            "Revenue, net income, assets, cash, operating income."))

    frame = pd.DataFrame(checks)
    if not frame.empty:
        look = frame[frame["status"] == "look"]
        st.metric("Checks wanting a look", f"{len(look)} of {len(frame)}")
        st.dataframe(frame, hide_index=True, use_container_width=True)

    if store and store["key_concepts"]["missing"]:
        st.subheader("Filings missing a key concept")
        for concept, accessions in store["key_concepts"]["missing"].items():
            tickers = {
                row["accession"]: row["ticker"] for row in store["per_filing"]}
            named = ", ".join(f"{tickers.get(a, '?')} ({a})" for a in accessions)
            st.write(f"**{concept}** — {named}")

    st.caption(
        "The oracle checks - accounting identity, segment sums, quarterly "
        "roll-up - are not here yet. They are the next change, and all three "
        "fail today against known bugs, so they are worth watching go green.")


# --------------------------------------------------------------------------
# ask


with ask_tab:
    st.caption(
        "Run a question through both arms and compare the ranking. Every run is "
        "appended to a log so a fix can be shown to have changed the ranking, "
        "rather than asserted to have.")

    question = st.text_input("Question", value="total revenue for the three months ended March 31, 2026")
    a, b, c = st.columns(3)
    ticker = a.text_input("ticker filter", value="MSFT")
    form = b.text_input("form filter", value="10-Q")
    top_k = c.number_input("top_k", min_value=1, max_value=50, value=8)

    if st.button("Ask both arms", type="primary"):
        status = configure_arms(instance)
        st.caption(" · ".join(f"{arm}: {state}" for arm, state in status.items()))

        from graphrag import retrieve

        filters = {k: v for k, v in (("ticker", ticker), ("form", form)) if v}
        trace_id = str(uuid.uuid4())

        for arm in ("structured", "graph"):
            st.subheader(arm)
            started = time.perf_counter()
            try:
                envelope = retrieve.search(question, tool=arm, filters=filters,
                                           top_k=int(top_k), trace_id=trace_id)
                elapsed = time.perf_counter() - started
                st.caption(
                    f"status **{envelope['status']}**"
                    + (f" — {envelope['reason']}" if envelope.get("reason") else "")
                    + f" · {elapsed * 1000:.0f} ms · trace {envelope['trace_id']}")
                results = envelope["results"]
                if results:
                    flat = []
                    for row in results:
                        metadata = row.get("metadata") or {}
                        flat.append({
                            "rank": row.get("rank"),
                            "score": row.get("score"),
                            "text": str(row.get("text") or row.get("quote") or "")[:160],
                            "provenance": metadata.get("fact_id")
                            or metadata.get("section_id")
                            or metadata.get("chunk_id"),
                        })
                    st.dataframe(pd.DataFrame(flat), hide_index=True,
                                 use_container_width=True)
                else:
                    st.info("No evidence returned.")
                append_query_log({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "instance": instance,
                    "scope": graph.get("scope"),
                    "provenance": report["provenance"],
                    "arm": arm,
                    "question": question,
                    "filters": filters,
                    "top_k": int(top_k),
                    "status": envelope["status"],
                    "reason": envelope.get("reason"),
                    "latency_ms": round(elapsed * 1000),
                    "trace_id": envelope["trace_id"],
                    "results": [
                        {"rank": r.get("rank"), "score": r.get("score"),
                         "provenance": (r.get("metadata") or {}).get("fact_id")
                         or (r.get("metadata") or {}).get("section_id")}
                        for r in results
                    ],
                })
            except Exception as exc:  # the arm being down is a result, not a crash
                st.error(f"{type(exc).__name__}: {exc}")
                append_query_log({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "instance": instance, "arm": arm, "question": question,
                    "filters": filters, "top_k": int(top_k),
                    "error": f"{type(exc).__name__}: {exc}",
                })

    log = read_query_log()
    if log:
        st.subheader("Query log")
        st.caption(f"{len(log)} recent runs · {QUERY_LOG}")
        st.dataframe(
            pd.DataFrame([
                {k: row.get(k) for k in
                 ("at", "arm", "question", "status", "latency_ms", "instance")}
                for row in reversed(log)
            ]),
            hide_index=True, use_container_width=True)
