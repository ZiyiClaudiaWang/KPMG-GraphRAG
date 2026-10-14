# Merging the two ontologies — worksheet

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
| **Thu 16 Oct** | Five to eight sentences from a real Item 1A, each hand-labelled as a stated fact, a forward-looking statement, or a hedged risk | A file next to the toy graph | The labels exist before `Claim` is designed, not after |
| **Fri 17 Oct** | This file, with every `Answer:` and `Question it answers:` line filled in | A pull request | No blank answer lines in Part 2. Rows nobody could agree on say "unresolved" and name an owner |
| **Fri 17 Oct** | `toy_graph/toy_graph.py` updated to match what was agreed | Same pull request | `python3 toy_graph.py` runs and reports idempotent |
| **Mon 20 Oct** | Three accession numbers for the real toy graph, one of them a company that has filed a 10-K/A | A short list in the pull request, or a file in `docs/` | All three verified on EDGAR, with the amendment identified |

**Why these dates, and why they differ.**

The labelled sentences come first because they feed decision 2.3. Designing the
`Claim` node and then looking for sentences that fit it is how you end up modelling
an imaginary filing. An hour's work, and it has to happen before the merge rather
than after.

The merge and the loader are due Friday because the extraction prototype starts next
week, and every prompt written after that point assumes a settled ontology. Changing
it later means redoing that work.

The three filings can wait until Monday. Nothing is blocked by them until real
filings get loaded, and verifying amendments on EDGAR is fiddlier than it sounds.

---

## What this file should look like on Friday

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

**How we check it is real:** if it is not loaded into `toy_graph/` by Friday, it is
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

## A starting draft to argue with

Not decided. Six of these follow from evidence already sitting in the two documents.
Two are genuinely open and left blank.

| | Suggested | Why |
| --- | --- | --- |
| `Company`, `Filing`, `Section`, `Segment` | keep | both documents have them |
| `Fact`, including whether it was corrected | keep | the one we follow |
| `Subsidiary` | keep | the one we follow |
| `Claim`, with a type | **add** | the proposal |
| `extraction_status` on anything extracted | **add** | the proposal |
| `RiskFactor` | **keep** — Use Case B is the question it needed | the proposal |
| `Executive` | **cut** until someone names a question for it | — |
| `AMENDS`, plus a rule for what replaces what | keep | the one we follow, and the toy graph |
| `CONSOLIDATED_INTO` | ? | the toy graph |
| `FinancialMetric` and `MetricObservation` | ? | open |
| `SourceChunk` as its own node | ? | open |

Node names in PascalCase, link names in UPPER_SNAKE — see Part 0.

---

## Part 0 · Names — do this one first

Not a matter of taste. Neo4j has its own convention: **node names in PascalCase**,
**link names in UPPER_SNAKE**.

By that rule the proposal is right about node names and the other document is wrong.
Both are already right about link names. Following it means both documents change,
which is the point — nobody is conceding to the other person, we are both following
the database.

| | Now, in the one we follow | Now, in the proposal | Convention | Agreed |
| --- | --- | --- | --- | --- |
| Node names | `COMPANY` | `Company` | PascalCase | |
| Link names | `FILES` | `FILED` | UPPER_SNAKE | |

Then pick one name per link. These three are the same link twice:

| The link | One document | The other | Agreed |
| --- | --- | --- | --- |
| Company to filing | `FILES` | `FILED` | |
| Filing to section | `CONTAINS` | `HAS_SECTION` | |
| Company to segment | `REPORTS_SEGMENT` | `HAS_SEGMENT` | |

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

Answer:
Question it answers:

### 2.2 Is a chunk of text its own node, or just an ID on other things

**For a node:** you can ask what else came from the same chunk, and claims hang off
it naturally.

**Against:** it is heavier, and it does not survive the corpus being re-chunked,
which is happening between W6 and W9. It also overlaps `Section` if the section-first
proposal goes through, because then sections come first and chunks hang off them.

Answer:
Question it answers:

### 2.3 `Claim`, with a type — take it from the proposal

We have nowhere to record that "supply constraints may affect production" is a
company hedging rather than stating a fact. Given we have said the whole point is
evidence-grounded answers, that is a gap in the middle of the thing.

Suggested: **take it.** Then work out the smallest set of properties it needs.
Alina is hand-labelling real sentences first, so the shape comes from what actual
sentences need rather than from what we imagine they need.

Answer:
Properties:
Question it answers:

### 2.4 `RiskFactor` — its own node, or a label on a section

The other document called this doubtful, because no question needed it. Use Case B
in the proposal is that question: how a company's supply chain risk disclosures
changed between two years. That is the justification that was missing.

Answer:
Question it answers:

### 2.5 `Executive` — cut unless someone names a question

There is no reliable way to tell two people with the same name apart, so it needs
fuzzy matching, and neither document says what question it answers. By our own rule
it gets cut.

Answer:
Question it answers:

### 2.6 `Subsidiary` — take it from the other document

Exhibit 21 is where most of our cross-company questions come from, and the proposal
has no node for it.

Answer:
Question it answers:

### 2.7 Corrections and restatements — take them from the other document

The proposal has no way of recording that a filing was later corrected.
`toy_graph/check_findings.py` shows what that costs: the system gives you an
out-of-date number with a citation that looks perfectly correct.

It also needs a rule neither document has yet. A number is only out of date when the
correction **replaces that same figure for that same period**. If we treat the whole
filing as out of date, we also mark last year's number stale even though nobody
touched it, and then there is nothing to compare against.

Answer:
Question it answers:

### 2.8 `CONSOLIDATED_INTO` — new, from the toy graph

Nothing connects a subsidiary to the segment its results get folded into, so we
cannot answer a question about which part of the business an acquisition went into.
The toy graph loads a proposed link, marked as not agreed, purely so the rest of the
question can run.

Open: its own link, or a property on `OWNS`?

Answer:
Question it answers:

---

## Part 3 · Still open

Write these down as open, rather than letting them look settled.

| Item | Where it stands |
| --- | --- |
| `PEER_OF` | In both documents, and neither says where the data comes from. From 10-K filings the honest source is the SIC code in EDGAR. Companies' own lists of their peers are in proxy statements, which we are not reading. Either we source it or we drop it |
| Working out which mentions are the same company | Both documents assume this step happens somewhere. Neither says how. The rule that matters: pull out mentions, work out which entity each one refers to as a separate step, and never let the model pick the ID itself |
| `extraction_status` | In the proposal, nowhere in the other document. Related to `Claim` but applies to everything we extract |

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
