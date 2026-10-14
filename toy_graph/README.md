# Toy graph — schema v1, tested against a question

A minimum graph in Neo4j built from one constructed filing, used to find out
whether the settled ontology can answer a question that needs more than one hop.

11 node instances across the 6 settled node types, 15 relationships.

It found two gaps. Both are reproducible by running `check_findings.py`.

## Run it

```bash
docker run -d --name kpmg-graph -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/sketchpass neo4j:5.26

pip install -r requirements.txt
cp .env.example .env          # edit if you are not on the default local setup

python3 toy_graph.py          # wipe, constraints, load twice, prove idempotent
python3 check_findings.py     # the two findings, each as a before/after pair
```

Browser at http://localhost:7474 — user `neo4j`, password `sketchpass`,
connect URL `bolt://localhost:7687`.

To point at Aura instead, put its URI, username and password in `.env`.
Nothing else changes. `.env` is gitignored; never commit credentials.

| File | Holds |
| --- | --- |
| `toy_graph.py` | Node and edge definitions, deterministic keys, provenance shapes, loader |
| `check_findings.py` | The two findings, each as the natural query and the corrected one |
| `.env.example` | Connection template |

## The source passage

Invented. Shaped like a 10-K and its amendment, because a clean filing would not
exercise the open parts of the schema.

> Northwind Industries, Inc. (CIK 0000123456), Form 10-K for fiscal year ended
> 31 Dec 2024, filed 18 Feb 2025, accession 0000123456-25-000012.
>
> *Item 7, MD&A:* "On 3 April 2024 the Company completed its acquisition of
> Harbor Logistics LLC. Harbor's results are consolidated within the Freight
> Services segment from the acquisition date. Freight Services revenue was
> $412.0 million for fiscal 2024, compared with $286.5 million in fiscal 2023."
>
> *Exhibit 21:* Harbor Logistics LLC — Delaware — wholly owned.
>
> Form 10-K/A, accession 0000123456-25-000031, filed 9 Sep 2025: "The Company is
> restating segment revenue for fiscal 2024. Freight Services revenue is restated
> to $388.4 million, reflecting correction of the timing of revenue recognised on
> Harbor Logistics contracts assumed at acquisition."
>
> EDGAR metadata: Northwind SIC 4731. Coastal Freight Corp (CIK 0000987654) also
> SIC 4731.

## The question

> Which segment absorbed Harbor Logistics, how did its FY2024 revenue move, and is
> the figure we would quote still the one the company stands behind?

Hybrid in the Layer 3 taxonomy. Graph resolves the acquisition and the amendment
chain, structured supplies the values, vector supplies the prose.

Four hops: subsidiary to segment, segment to facts, facts back to the filings that
reported them, filings out along `AMENDS`.

## Finding 1 — we cannot link a subsidiary to its segment

Nothing in the schema connects a subsidiary to the segment it reports under. The
only way across is up to the parent company and back down through
`REPORTS_SEGMENT`, which hands back every segment that company has — two here, a
dozen for a real company with several business lines.

```
schema v1 only:
  Freight Services
  Warehousing Solutions
2 rows. Ambiguous.
```

The graph is loaded with a proposed edge, flagged `in_schema_v1: false`:

```
CONSOLIDATED_INTO (SUBSIDIARY -> SEGMENT)
  valid_from, quote, text provenance
```

With it, hop 1 returns one row and carries the sentence it came from.

**Decision needed:** is this an edge of its own, or an attribute on the existing
`OWNS` edge? It is loaded as an edge here only to make the question runnable.

## Finding 2 — a correction applies to numbers, not to whole filings

The obvious query asks "was the filing that reported this number ever amended?"
That is true of the FY2023 figure — it was in the 10-K, and the 10-K was later
amended. But the amendment only corrected FY2024. It left FY2023 alone.

```
judged at filing level (wrong):
  2023-12-31
    $286,500,000  as-filed  in 10-K  — superseded by 10-K/A
```

So FY2023 is left with no current value at all, and the year-on-year comparison has
nothing to stand on. In an earlier version this showed up as a crash rather than a
wrong number, which is the only reason anyone noticed.

The rule we need: a number is out of date only when the amendment actually
**replaces that same concept for that same period**. Checking whether the filing
was amended is not enough.

```
judged by fact-level replacement (correct):
  2024-12-31   $388,400,000  restated   CURRENT
  2024-12-31   $412,000,000  as-filed   superseded by 10-K/A
  2023-12-31   $286,500,000  as-filed   CURRENT
```

**Decision needed:** this belongs in the temporal model, alongside valid-from /
valid-to and `basis` on FACT. `basis` on its own is not sufficient — it labels a
fact but does not say what replaces what.

## Why this matters

```
FY2023 $286,500,000 -> FY2024 $388,400,000    +35.6%
quoting the superseded figure instead          +43.8%
```

Both numbers can be defended from the text of the filing. Only one of them is
current. What tells them apart is a link between two documents, not a sentence
inside either one. That is the argument for the graph, put concretely.

## What is deliberately not here

- **No community detection.** Eleven nodes cannot support Leiden. That is a W9 question.
- **No entity resolution.** Keys are assigned directly, because the passage has no
  name variants. The real pipeline must extract *mentions* and resolve them to
  entities in a separate stage.
- **No real filings.** The passage is constructed. Checked against EDGAR: Apple has
  filed zero 10-K/A amendments in the last decade, so a naive AAPL corpus would not
  exercise finding 2 at all. Whichever companies the real toy graph uses, at least
  one needs an amendment.
- **No Claim or RiskFactor nodes.** See the ontology comparison below.

## This week — who does what

Three vocabularies currently describe the same domain: this one, the GraphRAG
ontology draft, and whatever Data Engineering has defined. The risk is extraction
starting before they reconcile. So this week is scoped to decisions, with the toy
graph as the test harness, not to volume.

**Pick the filings.** Choose the three real filings for the toy graph. One hard
constraint: at least one company must have filed a 10-K/A, or the restatement
finding cannot be exercised at all. Apple, the obvious default, has filed zero
amendments in ten years — checked. Deliverable: three accession numbers verified on
EDGAR, with the amendment identified.

**Settle FinancialMetric / MetricObservation vs FACT.** By experiment, not debate.
Write the Cypher for Use Case A under both models and compare. Whichever is simpler
to write and read wins. Half a day, and it ends a question that would otherwise run
for weeks.

**Define the Claim node.** Take 5–8 sentences from one real Item 1A. Hand-label each
as reported fact / forward-looking / hedged risk. From those labels, define the
minimal `Claim` node — the properties it actually needs — plus the `SUPPORTS` edge
from chunk to claim, and add it to `toy_graph.py` as an extension. Labelling first
means the node shape comes from real sentences rather than from imagination.

**Reconcile the names (shared, about an hour).** One table: concept, name here,
name in the draft, name in Data's definitions, agreed name. This has to exist before
anyone writes an extraction prompt. `FILED`/`FILES` and `HAS_SEGMENT`/
`REPORTS_SEGMENT` are the same relationships under different names, and two people
loading into one graph with different edge names get traversals that return nothing,
silently.

**Not this week:** volume extraction, entity resolution, community detection,
`Executive`. All W5 or later, or cut.

## Against the GraphRAG ontology draft

Both documents describe the same domain and agree on most of it. Three conflicts
and one gap, all worth settling before extraction starts.

**We use different names for the same links.** `FILED` and `FILES`, `HAS_SECTION`
and `CONTAINS`, `HAS_SEGMENT` and `REPORTS_SEGMENT`. Easy to fix and expensive to
leave. If two people load data into one graph using different names for the same
link, queries come back empty and nothing reports an error.

**FinancialMetric and MetricObservation, or one FACT.** The draft separates the
concept ("Revenue") from the value ("FY2025 revenue was X"). This version keeps one
FACT and stores the concept as a property.

There is a real argument for splitting them: a FinancialMetric node turns "every
company that reports Revenue" into following links, rather than scanning properties
on every node. The argument against is that the concept comes from the published
XBRL taxonomy, so it is a lookup list rather than anything we extract from filings,
and the fact table already carries it. Either is defensible. We need one of them,
not both.

**SourceChunk as a node, or as a property.** The draft makes the chunk a node. This
version stores `source_chunk_id` as a property. Making it a node lets you ask what
else a chunk supports. Keeping it a property is lighter and copes better when the
corpus is re-chunked. This also overlaps the section-first proposal — if sections
become first-class and chunks hang off them, a SourceChunk node partly repeats what
SECTION already does.

**The draft has no model of amendments.** No `AMENDS`, no `basis`, no rule for what
replaces what. Finding 2 shows the cost: a system that returns an out-of-date
number with a citation that looks perfectly correct. This version has `AMENDS` and
`basis` — but neither document yet has the rule about replacing a specific number
for a specific period.

**What to take from the draft:** the `Claim` node, with a type attached. There is
nowhere here to record that "supply constraints may affect production" is a hedged
statement rather than a fact, and the draft is right that it matters.
`extraction_status` does the same job for confidence.

**What the draft should drop, by its own rule:** `Executive`. It has no stable ID
and no question that needs it. `RiskFactor` is the opposite case — the draft's Use
Case B is exactly the question it needed, which is what was missing here.
