# Ontology reconciliation — working document

Two documents in this repo describe the same ontology and disagree.
[schema-ids-operations.md](./schema-ids-operations.md) is marked canonical and has
been through several revisions. [ontology-proposal-claudia.md](./ontology-proposal-claudia.md) is a newer draft
with things the canonical version lacks.

This file exists to merge them into one. Fill it in during the session, then it
becomes the ontology and both source documents point at it.

**Working rule, from the earlier session plan:** every node and every edge needs a
question it unlocks, named by a person in the room. Anything without one is cut.
Groups add and never subtract, and ontologies are made good by subtraction.

**Test for v1:** if it is not loaded in `toy_graph/` by Friday, it is not in v1. The
toy graph is small enough that adding a node type takes ten minutes, so this is a
real test rather than a threat.

---

## Where the two documents differ

Read this before the session so nobody spends the first twenty minutes discovering
the overlap.

### Nodes

| Concept | Standing schema | Proposal | Note |
| --- | --- | --- | --- |
| Company | `COMPANY`, key `cik`, valid-from / valid-to for renames | `Company`, no key | Same thing |
| Filing | `FILING`, key `accession`, `amends_accession` | `Filing`, no key | Same thing |
| Section | `SECTION`, key plus `section_path` and offsets | `Section`, no key | Same thing |
| Chunk | A property. Explicitly opaque — RAG §5 says do not parse it | `SourceChunk`, a node | **Decision 2.2** |
| A reported number | `FACT` — concept, value, unit, period, `basis`, `context_ref` | `MetricObservation` plus `FinancialMetric` | **Decision 2.1** |
| Segment | `SEGMENT`, facts dimensioned by it | `Segment` | Same thing |
| Subsidiary | `SUBSIDIARY`, from Exhibit 21 | absent | **Decision 2.6** |
| Risk | Contested, probably a tag on Section | `RiskFactor` | **Decision 2.4** |
| Claim | absent | `Claim` with `claim_type` | **Decision 2.3** |
| Officer | Contested, no key | `Executive` | **Decision 2.5** |

### Relationships

Eight against eleven. Three pairs are the same link named differently, covered in
Part 0. Five of the proposal's extra links exist only to wire the chunk-and-claim
model, and `OF_METRIC` exists only because of the metric split — so two decisions
account for most of the difference.

### In the standing schema only

Deterministic keys for every node type · three provenance shapes, including the one
for XBRL facts which have no chunk · the two-store split, with the graph holding a
pointer and never a number · `AMENDS` and `basis` · `SUBSIDIARY` · the operation
catalog, with comparisons computed at query time and never stored · question
routing · the contracts index · the XPath conflict rule · a list of four known
defects in its own Layer 0.

### In the proposal only

`Claim` with `claim_type` — the difference between a company's hedge and a company's
assertion. Nothing in the standing schema can record it, and the stated objective is
evidence-grounded retrieval.

`extraction_status` — confidence and review state. The standing schema assumes
extraction is correct.

Two business questions written out in full. The standing schema's contested table
says "name the question it unlocks" five times and names none. The proposal's Use
Case B is exactly the question `RiskFactor` was missing, so by the standing
schema's own rule the proposal earns a node the standing schema was about to cut.

### Fair reading

The standing schema is further along as an engineering specification; it could be
built tomorrow, and the proposal could not, because nothing in it says how an entity
is identified. It has also been through a charter reconciliation, a working session
and several revisions, while the proposal is two days old and ends with five open
questions. A fourth draft beating a first one is not evidence of much.

The useful split: one is the engineering, the other is the epistemics. Without the
proposal, the system states a company's hedge as a fact. Without the standing
schema, nothing loads.

---

## Strawman for v1

A starting position to react to, not a decision. Six rows below follow from evidence
already in the two documents; two are genuinely open and are left blank.

| | Proposed | Comes from |
| --- | --- | --- |
| `Company`, `Filing`, `Section`, `Segment` | keep | both |
| `Fact` | keep, with `basis` | standing schema |
| `Subsidiary` | keep | standing schema |
| `Claim` with `claim_type` | **add** | proposal |
| `extraction_status` on everything extracted | **add** | proposal |
| `RiskFactor` | **keep**, justified by Use Case B | proposal |
| `Executive` | **cut** until someone names the question | — |
| `AMENDS`, plus the fact-level replacement rule | keep | standing schema, toy graph |
| `CONSOLIDATED_INTO` | ? | toy graph |
| `FinancialMetric` / `MetricObservation` | ? | open |
| `SourceChunk` as a node | ? | open |

Node labels PascalCase, relationship types UPPER_SNAKE, per Part 0.

---

## Part 0 · Naming — settle this first, 10 minutes

Not a matter of taste. Neo4j's own convention is **PascalCase for node labels** and
**UPPER_SNAKE for relationship types**.

By that rule the draft is right about labels and the canonical document is wrong;
both are already right about relationship types. Adopting it means both documents
change, which is the point.

| | Canonical now | Draft now | Convention | Agreed |
| --- | --- | --- | --- | --- |
| Node labels | `COMPANY` | `Company` | PascalCase | |
| Relationship types | `FILES` | `FILED` | UPPER_SNAKE | |

Then one name per relationship. These are the same links under different names:

| Link | Canonical | Draft | Agreed |
| --- | --- | --- | --- |
| Company to filing | `FILES` | `FILED` | |
| Filing to section | `CONTAINS` | `HAS_SECTION` | |
| Company to segment | `REPORTS_SEGMENT` | `HAS_SEGMENT` | |

Suggested tiebreak if the room splits: present tense, because the graph describes a
state rather than an event. `FILES`, `CONTAINS`, `HAS_SEGMENT`. Mixed outcome is
fine — this is not worth a long discussion, it is worth a short one.

---

## Part 1 · Nodes both documents already agree on, 5 minutes

Confirm and move on. No discussion needed.

| Node | Key | In v1 |
| --- | --- | --- |
| `Company` | `cik` | yes |
| `Filing` | `accession` | yes |
| `Section` | `section_id` | yes |
| `Segment` | derived from cik and normalised name | yes |

---

## Part 2 · The eight real decisions, 5 minutes each

Each row needs an outcome and the question that justifies it. An empty question
column means the row is cut.

### 2.1 FinancialMetric plus MetricObservation, or one Fact

The draft separates the concept ("Revenue") from the value ("FY2025 revenue was X").
The canonical document keeps one `Fact` with `concept` as a property.

**For splitting:** a `FinancialMetric` node turns "every company reporting Revenue"
into following links rather than scanning a property on every node.

**Against:** the concept comes from the published XBRL taxonomy, so it is a lookup
list, not something extracted from filings. The fact table already carries it.

**How to decide, not debate:** write the Cypher for Use Case A both ways. Whichever
reads better wins. Fifteen minutes, and it ends the argument permanently.

Decision:
Question it unlocks:

### 2.2 SourceChunk as a node, or source_chunk_id as a property

**For node:** you can ask what else a chunk supports, and claims attach to it
naturally.

**Against:** heavier, and it does not survive re-chunking — the corpus gets
re-chunked in W6 to W9. It also overlaps `Section` if the section-first proposal
lands, since sections become first-class and chunks derive from them.

Decision:
Question it unlocks:

### 2.3 Claim, with claim_type — adopt from the draft

The canonical document has nowhere to record that "supply constraints may affect
production" is a hedge rather than an assertion. For a system whose stated objective
is evidence-grounded retrieval, that gap is central, not cosmetic.

Suggested outcome: **adopt.** Then decide the minimum properties. Hand-labelling
real sentences first is the task in this week's sprint file, so the shape comes from
what the labels need rather than from imagination.

Decision:
Properties:
Question it unlocks:

### 2.4 RiskFactor — node, or a topic tag on Section

The canonical document flagged this as probably a tag, because no question needed
it. The draft's Use Case B is that question — comparing supply chain risk
disclosures across two years. That is the justification that was missing.

Decision:
Question it unlocks:

### 2.5 Executive — cut unless someone names the question

No stable identifier, so it needs fuzzy resolution, and no question attached in
either document. By the working rule it is cut.

Decision:
Question it unlocks:

### 2.6 Subsidiary — adopt from the canonical document

Exhibit 21 is the main source of cross-company multi-hop questions, and the draft
has no node for it.

Decision:
Question it unlocks:

### 2.7 AMENDS and basis — adopt from the canonical document

The draft has no model of amendments or restatements. `toy_graph/check_findings.py`
shows what that costs: without it, a system returns an out-of-date number with a
citation that looks correct.

It also needs the rule neither document has yet — a number is out of date only when
the amendment replaces **that same concept for that same period**. Treating the
whole filing as superseded marks the prior-year comparator stale and leaves the
comparison with no baseline.

Decision:
Question it unlocks:

### 2.8 CONSOLIDATED_INTO — new, from the toy graph

Nothing links a subsidiary to the segment its results are folded into, so a question
about an acquired company's segment cannot be answered. The toy graph loads a
proposed edge flagged `in_schema_v1: false` purely to make the question runnable.

Open: its own edge, or an attribute on `OWNS`?

Decision:
Question it unlocks:

---

## Part 3 · Carried forward unresolved, 5 minutes

Say these are open rather than letting them look settled.

| Item | State |
| --- | --- |
| `PEER_OF` | In both documents, sourced in neither. Honest source from a 10-K corpus is the SIC code in EDGAR metadata. Self-declared peer groups live in DEF 14A proxies, which are out of scope. Source it or drop it |
| Entity resolution | Both documents assume a resolution stage exists. Neither specifies it. The load-bearing rule is extract *mentions*, resolve to *entities* separately, never let the model choose the canonical ID |
| `extraction_status` | In the draft, nowhere in the canonical document. Related to Claim but applies to everything extracted |

---

## Output of the session

1. This file, filled in, becomes the ontology.
2. `schema-ids-operations.md` keeps its non-ontology content — keys, provenance
   shapes, the operation catalog, question routing — and points here for the
   ontology.
3. `ontology-proposal-claudia.md` is replaced by a line pointing here.
4. `toy_graph/toy_graph.py` is updated to match, which is the test that the result
   is implementable.

If you do not reach the end, stop at Part 2 and write down which rows are unresolved
and who owns each. A half-finished reconciliation with named owners beats a full one
nobody remembers agreeing to.

## Timing

| Part | Minutes |
| --- | --- |
| 0 · Naming | 10 |
| 1 · Agreed nodes | 5 |
| 2 · Eight decisions | 40 |
| 3 · Carried forward | 5 |
| Writing it up | 15 |

Seventy-five minutes. If it runs past ninety, something in Part 2 is a bigger
question than it looks and should be pulled out as its own item rather than solved
tired.
