# Merging the two ontologies — worksheet

Status: First draft by CW, 2026-10-16; pending Alina's and team review.
The decisions below are proposals, not joint sign-off.

Summary: Propose eight node types — Company, Filing, Section, Segment, Fact, Subsidiary, Claim and RiskFactor. Exclude SourceChunk, FinancialMetric, MetricObservation and Executive/Officer as separate nodes in v1. Propose section-based Claim evidence and a separate CONSOLIDATED_INTO relationship. Document, Table and Cell remain unresolved. Loader alignment and validation are still pending.

We have two documents describing the same thing, and they disagree.

[schema-ids-operations.md](./schema-ids-operations.md) is the one we have been
following. It has been revised several times.
[ontology-proposal-claudia.md](./ontology-proposal-claudia.md) is newer, and has
things the first one is missing.

This file is for merging them. Fill in the blanks as you work through it, together or
separately. When it is done, it becomes the ontology and both of the other documents
point at it.

---

## The ask

**Who:** Claudia and Alina. Split it however you like — we are not assuming you work
through it in one sitting or in the same room.

**What we need back**, in date order — they are not all due at once.

| Due | Deliverable | Where it goes | Done when |
| --- | --- | --- | --- |
| **Fri 16 Oct** | Five to eight sentences from a real Item 1A, each hand-labelled as a stated fact, a forward-looking statement, or a hedged risk | A file next to the toy graph | The labels exist before `Claim` is designed, not after |
| **Sat 17 Oct** | This file, with every `Answer:` and `Question it answers:` line filled in | A pull request | No blank answer lines in Part 2. Rows nobody could agree on say "unresolved" and name an owner |
| **Sat 17 Oct** | `toy_graph/toy_graph.py` updated to match what was agreed | Same pull request | `python3 toy_graph.py` runs and reports idempotent |
| **Mon 19 Oct** | Accession numbers for **MSFT, JPM and AAPL** — start here. Plus a fourth company with a *recent* 10-K/A, see the note below | A short list in the pull request, or a file in `docs/` | All verified on EDGAR, with the amendment identified |

**The three companies, and why they are not enough on their own.**

MSFT, JPM and AAPL are the starting set, checked against EDGAR on 16 Oct. They give
us segments, subsidiaries and three different Exhibit 21 shapes, which is most of
what the graph needs.

None of them gives us a usable restatement. Microsoft has filed **no 10-K/A ever**,
across 33 annual reports since 1994. JPMorgan has ten, the most recent in **2006**.
Apple has two, the most recent in **2010**. Both of those predate or sit on the edge
of the XBRL phase-in, so they probably carry no tagged facts — and tagged facts for
the same concept and period are exactly what the restatement rule compares.

So a fourth company is needed, chosen for having amended an annual report recently
enough to have XBRL in both the original and the amendment. Until that exists, the
restatement finding can only be demonstrated on the constructed Northwind pair.

**Why these dates, and why they differ.**

The labelled sentences come first because they feed decision 2.3. Designing the
`Claim` node and then looking for sentences that fit it is how you end up modelling
an imaginary filing. An hour's work, and it has to happen before the merge rather
than after.

The merge and the loader are due Saturday 17 Oct because the extraction prototype starts next
week, and every prompt written after that point assumes a settled ontology. Changing
it later means redoing that work.

The three filings can wait until Monday. Nothing is blocked by them until real
filings get loaded, and verifying amendments on EDGAR is fiddlier than it sounds.

---

## Expected worksheet state

So it is clear what finished means.

**Part 0** — the `Agreed` columns filled in. One casing convention, one name per
link.

**Part 2** — all eight decisions answered, two lines each, initials and a date. Two
of the eight coming back `Unresolved` with an owner against them is a fine result,
not a failure. `SourceChunk` is the likeliest, because it depends on whether the
section-first corpus proposal lands, and that is not ours to decide. Forcing a
decision we do not have the standing to make produces an agreement that gets
reopened.

**Part 3** — still open, which is the correct state for it. The point is that it
says so out loud.

**The starting draft** — either deleted, or rewritten as the agreed list with the
question marks replaced.

**A short summary at the top** — the nodes and links we ended up with, so a reader
does not have to reconstruct them from eight separate answers.

**The real test is the loader, not this file.** If a decision cannot be loaded into
eleven nodes, it was not finished. That check takes half an hour and it is what
tells us whether we agreed something buildable or something that only reads well.

**If you get stuck:** a half-finished file with owners against the open rows is a
good outcome, and far better than a complete one nobody remembers agreeing to. Say
what is unresolved rather than guessing.

---

**The rule we agreed:** every node and every link needs a question it helps answer,
written down by whoever takes that row. Anything without one gets cut. It is easy to
keep adding to an ontology and hard to take things out, so the rule has to do that
work for us.

**How we check it is real:** if it is not loaded into `toy_graph/` by Saturday 17 Oct, it is
not in v1. The toy graph is small — adding a node type takes about ten minutes — so
this is a reasonable test, not a punishment.

---

## Where the two documents differ

Read this first. It saves working out the overlap from scratch.

### Nodes

| What it is | In the one we follow | In the proposal | |
| --- | --- | --- | --- |
| A company | `COMPANY`, with `cik` as its ID, and dates for when a name was valid | `Company`, no ID | same thing |
| A filing | `FILING`, with the accession number as its ID | `Filing`, no ID | same thing |
| A section of a filing | `SECTION`, with an ID, a path and character positions | `Section`, no ID | same thing |
| A chunk of text | A property on other things. We are told not to read anything into it | `SourceChunk`, its own node | **decision 2.2** |
| A reported number | `FACT` — the concept, value, unit, period, and whether it was later corrected | `MetricObservation` plus `FinancialMetric` | **decision 2.1** |
| A business segment | `SEGMENT`, and numbers are tagged by it | `Segment` | same thing |
| A subsidiary | `SUBSIDIARY`, from Exhibit 21 | not there | **decision 2.6** |
| A risk theme | Listed as doubtful, probably just a label on a section | `RiskFactor`, its own node | **decision 2.4** |
| Something a company said | not there | `Claim`, with a type attached | **decision 2.3** |
| An officer | Listed as doubtful, no reliable ID | `Executive` | **decision 2.5** |

### Links

Eight against eleven. Three pairs are the same link with different names, which
Part 0 settles. Five of the extra ones in the proposal exist only to connect chunks
and claims, and `OF_METRIC` only exists because metrics are split in two. So two
decisions account for most of the gap.

### Only in the one we follow

A way of generating a stable ID for every node. Three different shapes of
provenance, including the awkward one — an XBRL number does not sit in a chunk of
text, so it cannot use the same fields as a sentence. The split between the two
stores, where the graph holds a pointer and never a number. `AMENDS`, and whether a
number was later corrected. `SUBSIDIARY`. A list of operations, with the rule that
comparisons and growth rates get worked out when someone asks, never stored. A
mapping from question type to which tool answers it. A table of what is agreed with
which team and by when. A rule for what happens when two ways of pointing at a
location disagree. And a list of four things that are wrong with its own foundations.

### Only in the proposal

**`Claim`, with a type attached.** This is the one that matters. We currently have
nowhere to record that "supply constraints may affect production" is a company
hedging, not a company stating a fact. We have told KPMG the whole point is that
answers are grounded in evidence, so that gap is near the middle of the thing, not
at the edge.

**`extraction_status`.** How confident we are, and whether a human has checked it.
The other document assumes extraction gets things right.

**Two business questions, written out in full.** This is worth more than it looks.
The other document says "name the question this node answers" five times and never
names one. The proposal's Use Case B is exactly the question `RiskFactor` needed. So
by our own rule, the proposal keeps a node the other document was about to cut.

### Being fair about this

The document we have been following is further along. It could be built tomorrow;
the proposal could not, because nothing in it says how you tell one company from
another. But it is also on its fourth revision after a working session, and the
proposal is two days old and ends with five open questions. A fourth draft beating a
first draft does not tell us much.

A better way to put it: one of them is the engineering, the other is about what kind
of statement we are looking at and how much we trust it. Without the proposal, the
system repeats a company's hedge as though it were a fact. Without the other
document, nothing loads at all.

---

## Current proposal summary — pending review

| Item | Proposed decision | Reference |
| --- | --- | --- |
| Company, Filing, Section, Segment | Keep | Part 1 |
| Fact | Keep one node type with a concept property | 2.1 |
| FinancialMetric / MetricObservation | Do not introduce separate node types | 2.1 |
| SourceChunk | Do not introduce a separate node; retain optional chunk provenance | 2.2 |
| Claim | Add, with statement type and review status | 2.3 |
| RiskFactor | Keep as a specific risk theme | 2.4 |
| Executive / Officer | Exclude from v1 | 2.5 |
| Subsidiary | Keep | 2.6 |
| Amendments and restatements | Preserve observations; establish replacement at fact level | 2.7 |
| CONSOLIDATED_INTO | Add as a separate relationship | 2.8 |
| Document / Table / Cell | Unresolved; owner: CW to coordinate with Alina | Pending named questions and real-filing validation |

Naming: PascalCase for nodes and UPPER_SNAKE for relationships.

This summary does not replace the detailed decisions or imply that the loader has been updated.

Node names in PascalCase, link names in UPPER_SNAKE — see Part 0.

---

## Part 0 · Names — do this one first

Status: Draft by CW, 2026-10-16. Pending Alina's review.
The entries below are proposed naming choices, not joint sign-off.

Not a matter of taste. Neo4j has its own convention: **node names in PascalCase**,
**link names in UPPER_SNAKE**.

By that rule the proposal is right about node names and the other document is wrong.
Both are already right about link names. Following it means both documents change,
which is the point — nobody is conceding to the other person, we are both following
the database.

| | Now, in the one we follow | Now, in the proposal | Convention | Agreed |
| --- | --- | --- | --- | --- |
| Node names | `COMPANY` | `Company` | PascalCase | Proposed: PascalCase, e.g. `Company`, `Filing`. CW, 2026-10-16; pending review |
| Link names | `FILES` | `FILED` | UPPER_SNAKE | Proposed: UPPER_SNAKE. CW, 2026-10-16; pending review |

Then pick one name per link. These three are the same link twice:

| The link | One document | The other | Agreed |
| --- | --- | --- | --- |
| Company to filing | `FILES` | `FILED` | Proposed: `FILES`. CW, 2026-10-16; pending review |
| Filing to section | `CONTAINS` | `HAS_SECTION` | Proposed: `CONTAINS`. CW, 2026-10-16; pending review |
| Company to segment | `REPORTS_SEGMENT` | `HAS_SEGMENT` | Proposed: `HAS_SEGMENT`. CW, 2026-10-16; pending review |

These proposals standardize relationship names only. The final Filing–Document–Section structure will be addressed separately.

If we cannot agree, use present tense, because the graph describes how things are
rather than something that happened: `FILES`, `CONTAINS`, `HAS_SEGMENT`. A mixed
result is fine. This is worth a short conversation, not a long one.

---

## Part 1 · Nodes we already agree on

Confirm these and move on.

| Node | Its ID | In v1 |
| --- | --- | --- |
| `Company` | `cik` | yes |
| `Filing` | accession number | yes |
| `Section` | `section_id` | yes |
| `Segment` | built from the company ID and a tidied-up name | yes |

---

## Part 2 · The eight real decisions

Each one needs an answer and the question that justifies it. If the question line is
empty, the row is cut.

### What a filled-in row looks like

Two short lines. Not an essay — the reasoning above each decision is already written
down, so the answer only has to say what was chosen and what it is for.

> **2.5 `Executive` — cut unless someone names a question**
>
> Answer: **Cut.** AS/CW, 16 Oct. Revisit if a question turns up that needs it.
> Question it answers: none. Neither of us could name one.

And one that stays in:

> **2.6 `Subsidiary` — take it from the other document**
>
> Answer: **Keep**, with `sub_id` as its ID, built from the parent company ID and a
> tidied-up name. CW, 16 Oct.
> Question it answers: "Which companies does Northwind own, and what do their
> segments report?" Exhibit 21 is the only place this comes from.

A row you could not settle looks like this, and it is a perfectly good outcome:

> **2.2 Is a chunk of text its own node, or just an ID on other things**
>
> Answer: **Unresolved.** Depends on whether the section-first proposal lands, and
> that is not ours to decide. Owner: CW, carried to next week.
> Question it answers: pending.

Initials and a date on each row mean we know who to ask later, and which rows nobody
has touched yet.

### 2.1 Two nodes for metrics, or one

The proposal separates the concept ("Revenue") from the value ("revenue in FY2025
was X"). The other document keeps one `Fact` and stores the concept on it.

**For splitting them:** a `FinancialMetric` node turns "every company that reports
Revenue" into following links, instead of checking a property on every node in the
graph.

**Against:** the concept comes from a published list that XBRL already defines, so
it is a lookup table rather than anything we learn from a filing. The fact table
already has it.

**How to settle it without arguing:** write the query for Use Case A both ways and
see which one reads better. Fifteen minutes, and it never comes up again.

Answer: **Proposed: Use one `Fact` node with a `concept` property; do not add a separate `FinancialMetric` node in v1.** CW, 2026-10-16; pending review.

Query-writing comparison for Use Case A:

Option A — Fact with concept as a property:

```cypher
MATCH (c:Company)-[:FILES]->(filing:Filing)-[:REPORTS]->(f:Fact)
WHERE c.cik IN $company_ciks
  AND f.concept IN $concepts
RETURN c.cik AS cik,
       filing.accession_number AS accession_number,
       f.fact_id AS fact_id,
       f.concept AS concept
```

Option B — separate metric and observation nodes:

```cypher
MATCH (c:Company)-[:FILES]->(filing:Filing)
      -[:REPORTS_METRIC]->(o:MetricObservation)
      -[:OF_METRIC]->(m:FinancialMetric)
WHERE c.cik IN $company_ciks
  AND m.concept IN $concepts
RETURN c.cik AS cik,
       filing.accession_number AS accession_number,
       o.fact_id AS fact_id,
       m.concept AS concept
```

Both return candidate fact references. For this use case, Option B adds a node type and traversal without simplifying the required numeric work. Values, units, periods and dimensions remain in the fact table. The structured query layer must select comparable observations and the requested basis, then calculate growth and margin. Revenue growth also requires the prior-year baseline.

`$concepts` must contain the actual source concept identifiers selected for each company, not assumed universal labels.

Question it answers: “How do Company A and Company B compare in FY2025 revenue growth and operating margin?” A separate metric node is not required for this use case. Revisit if metric hierarchies or other concept-level relationships become necessary.

Validation status: Query-writing comparison only. These queries describe the candidate models and have not been executed against the current schema probe. No runtime or performance advantage is claimed.

### 2.2 Is a chunk of text its own node, or just an ID on other things

**For a node:** you can ask what else came from the same chunk, and claims hang off
it naturally.

**Against:** it is heavier, and it does not survive the corpus being re-chunked,
which is happening between W6 and W9. It also overlaps `Section` if the section-first
proposal goes through, because then sections come first and chunks hang off them.

Answer: **Proposed: Do not include `SourceChunk` as a separate node in v1.** CW, 2026-10-16; pending review. Anchor Claim evidence on `Section` through `SUPPORTS`. Retain `source_chunk_id` as optional provenance metadata when available, never as part of a Claim or relationship key. Revisit if a concrete query requires chunk-level traversal.

Question it answers: “Where in the filing is the evidence for this Claim?” A section reference, verbatim quote and supporting context answer this without a separate chunk node. No additional question currently justifies that node.

### 2.3 `Claim`, with a type — take it from the proposal

We have nowhere to record that "supply constraints may affect production" is a
company hedging rather than stating a fact. Given we have said the whole point is
evidence-grounded answers, that is a gap in the middle of the thing.

Suggested: **take it.** Then work out the smallest set of properties it needs.
Six real Item 1A sentences have been annotated by CW with AI assistance; see the examples below.

Answer: **Proposed: Keep `Claim`.** CW, 2026-10-16; pending review.
The six annotated examples in [claim_examples.md](../toy_graph/claim_examples.md) show why statement type, qualifiers and context must be preserved. For v1, retain one complete sentence per Claim, assign a primary type, and record mixed content in a note. Automatic splitting into multiple claims is deferred.

Properties:

| Property | Requirement | Purpose |
| --- | --- | --- |
| `claim_id` | Required | Assigned deterministically by the loader using stable source and statement identity; never generated by the LLM or keyed on a chunk ID |
| `quote` | Required | Verbatim source sentence, preserving qualifiers |
| `claim_type` | Required | `stated_fact`, `forward_looking`, or `hedged_risk`; a primary label rather than a guarantee that categories never overlap |
| `accession_number` | Required | Identifies the source filing |
| `section_id` | Required | Identifies the source section under the shared provenance rules |
| `extraction_status` | Required | `needs_review`, `reviewed`, or `rejected`; records review status, not independent factual verification |
| `context` | Optional | Supporting context needed to interpret references such as “Such disruptions” |
| `subject_scope` | Optional | Identifies what the statement concerns, such as global attacks rather than Apple-specific attacks |
| `annotation_note` | Optional | Records mixed content, ambiguity and interpretation limits |

Proposed relationship: `Section -[:SUPPORTS]-> Claim`, with provenance on the supporting relationship. This provides a section-based evidence link without requiring a decision on whether `SourceChunk` is a separate node.

Question it answers: “Which statements in Apple's risk disclosures describe existing conditions, which express future expectations, and which describe possible adverse outcomes, and what source wording supports each classification?”

Validation status: Supported by the annotated examples; not yet implemented or tested in the schema probe.

### 2.4 `RiskFactor` — its own node, or a label on a section

The other document called this doubtful, because no question needed it. Use Case B
in the proposal is that question: how a company's supply chain risk disclosures
changed between two years. That is the justification that was missing.

Answer: **Proposed: Keep `RiskFactor` as a specific risk theme, distinct from the section-level `risk_factors` label.** CW, 2026-10-16; pending review. Connect `Claim -[:CONCERNS_RISK]-> RiskFactor`. For the prototype, use a small, documented set of manually reviewed themes rather than creating a new theme for every wording variation. Retain the original claims and their provenance.

Identity: Assign each manually reviewed theme a stable `risk_id` from a documented theme list. Match wording variants to that ID rather than generating a new node from each extracted phrase. The initial theme list remains to be created.

Question it answers: “How did Apple's disclosures about supply-chain risk change between two fiscal years?” The section label identifies where risk disclosures occur; the theme groups relevant claims across filings. This comparison requires evidence from both years and has not yet been validated by the single-year examples.

### 2.5 `Executive` — cut unless someone names a question

There is no reliable way to tell two people with the same name apart, so it needs
fuzzy matching, and neither document says what question it answers. By our own rule
it gets cut.

Answer: **Proposed: Exclude `Executive` / `Officer` from v1.** CW, 2026-10-16; pending review. No agreed business question currently requires this node. Revisit when a concrete question, a suitable source and an identity-resolution approach are available.

Question it answers: None currently agreed; therefore excluded from v1.

### 2.6 `Subsidiary` — take it from the other document

Exhibit 21 is where most of our cross-company questions come from, and the proposal
has no node for it.

Answer: **Proposed: Keep `Subsidiary`.** CW, 2026-10-16; pending review. Use `Company -[:OWNS]-> Subsidiary`, supported by the filing's Exhibit 21 disclosures. Follow the existing `sub_id` scheme based on parent CIK and normalized subsidiary name, while retaining the original name and source provenance. This key is provisional identity, not proof that name variants have been resolved. Do not infer ownership percentages or segment membership unless explicitly supported.

Question it answers: “Which subsidiaries does a company list in Exhibit 21, and where are they incorporated?” Cross-year comparisons additionally require name-resolution and filing-specific evidence.

### 2.7 Corrections and restatements — take them from the other document

The proposal has no way of recording that a filing was later corrected.
`toy_graph/check_findings.py` shows what that costs: the system gives you an
out-of-date number with a citation that looks perfectly correct.

It also needs a rule neither document has yet. A number is only out of date when the
correction **replaces that same figure for that same period**. If we treat the whole
filing as out of date, we also mark last year's number stale even though nobody
touched it, and then there is nothing to compare against.

Answer: **Proposed: Keep filing-level `AMENDS`, preserve original and amended facts, and determine supersession at fact level.** CW, 2026-10-16; pending review.

`AMENDS` records that one filing amends another; it does not automatically invalidate every fact in the original. Treat a fact as superseded only when evidence establishes that the amendment replaces that specific observation. Match the reporting entity, concept, period and dimensions, with compatible units, or use an explicit replacement link supplied by Data.

Keep `basis` (`as_filed` / `as_restated`) separate from currentness. An `as_filed` fact can remain current. Do not label every fact in a 10-K/A as restated merely because of the filing form. If replacement cannot be established, retain both observations and flag the ambiguity.

Numeric values remain in the fact table; the graph retains fact references and filing relationships.

Question it answers: “What value was originally reported, what was subsequently corrected, and which observation should be used for the requested period and reporting scope?”

Validation status: The constructed schema probe demonstrates the filing-level supersession error. Validation against a real original/amendment pair and the Data replacement contract is still pending.

### 2.8 `CONSOLIDATED_INTO` — new, from the toy graph

Nothing connects a subsidiary to the segment its results get folded into, so we
cannot answer a question about which part of the business an acquisition went into.
The toy graph loads a proposed link, marked as not agreed, purely so the rest of the
question can run.

Open: its own link, or a property on `OWNS`?

Answer: **Proposed: Add `Subsidiary -[:CONSOLIDATED_INTO]-> Segment` as a separate relationship.** CW, 2026-10-16; pending review. Create it only when a filing explicitly supports the subsidiary-to-segment mapping. Retain the source accession, section reference and supporting quote. Record effective dates when disclosed; do not invent them or infer segment membership from parent ownership alone.

Question it answers: “Which reporting segment includes the results of a named subsidiary or acquired business?” Going through the parent company returns all its segments and does not establish this mapping.

Validation status: Demonstrated in the constructed schema probe; requires validation against a real filing.

---

## Part 3 · Still open

Write these down as open, rather than letting them look settled.

| Item | Where it stands |
| --- | --- |
| `PEER_OF` | In both documents, and neither says where the data comes from. From 10-K filings the honest source is the SIC code in EDGAR. Companies' own lists of their peers are in proxy statements, which we are not reading. Either we source it or we drop it |
| Working out which mentions are the same company | Both documents assume this step happens somewhere. Neither says how. The rule that matters: pull out mentions, work out which entity each one refers to as a separate step, and never let the model pick the ID itself |
| `extraction_status` | Proposed for Claim in 2.3. Whether the same review-status convention applies to other extracted nodes and relationships remains open. Owner: CW |
---

## How to work through it

However suits you. Split the rows between you, take a call, or do it in a pull
request — the file is in the repo, so comments on a diff work as well as talking.

Two things that make the async version work:

**Put your initials next to any row you answer.** Then it is obvious who to ask, and
obvious which rows nobody has touched.

**Part 0 first, whoever gets there.** The naming rule comes from Neo4j rather than
from either of us, so it does not need both people present. Everything else reads
more easily once the names are consistent.

If you do talk it through live, the eight decisions are about five minutes each, and
the whole thing is a bit over an hour. If any single row runs long, it is bigger
than it looks — pull it out as its own piece of work rather than solve it tired.

## What we should have at the end

1. This file, filled in. It becomes the ontology.
2. `schema-ids-operations.md` keeps everything that is not the ontology — IDs,
   provenance, operations, question routing — and points here for the ontology.
3. `ontology-proposal-claudia.md` gets replaced by a line pointing here.
4. `toy_graph/toy_graph.py` updated to match, which is how we find out whether what
   we agreed can actually be built.

Getting partway is fine. Write down which rows are unresolved and who owns each one.
Half a decision with a name on it beats a whole one nobody remembers making.
