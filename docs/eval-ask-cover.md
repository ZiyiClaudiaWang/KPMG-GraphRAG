# Covering note for the Eval ask

Drafted 2026-10-21. **Not sent.** To Yulei Ding, from the contracts seat.

[eval-ask.md](./eval-ask.md) was written on 13 October and [eval-reply.md](./eval-reply.md)
on the 14th. Neither went out. This note sends both, says what has changed since, and puts
a date on the part that cannot wait.

Send it from the co-lead contracts seat — nobody else writes to another team. It goes to
Eval rather than RAG: section 4 touches RAG's scorer, but the decisions in it are Eval's.

**Before sending:** merge the pull request that adds M17–M19, or the attached `eval-ask.md`
still shows sixteen candidate questions and the note says nineteen.

---

## The note

```
Subject: Traversal tranche for gold set v1 — we need a decision this week

Yulei — two documents attached, drafted 13 and 14 October. We should have
sent them then. Short version of each below, and one part that is now urgent.

1 · The traversal tranche (eval-ask.md)

Neither gold set has a question that requires following a relationship.
gold_set_v0.md is MSFT-only with no cross-document coverage. The four
cross-company questions in gold_v0.jsonl are fan-out — three independent
lookups with a ticker filter, compared afterwards. A tuned vector index does
that well and does not need a graph to do it.

So if round 1 runs at W8 against either set, the graph arm scores on work
vector already does, and we finish with no evidence the graph earned its
place. That is a coverage gap, not a criticism — both sets are good at what
they cover.

We are not writing a second gold set. We want 15-20 questions added inside
yours, in your format. We propose the shapes and supply the graph path each
one should exercise; you write, validate and own the final wording. We should
not author the benchmark our own arm is graded on.

The attached document has 19 candidates. Three of them (M17-M19) were added
yesterday, after our ontology merge on the 17th left two settled structures
with no question to test them.

2 · One field we need, and it is not free for you

Every question needs a hops count or a tier: "multihop" label — including the
42 that already exist, not only the new ones.

This looks like a nicety. It is the x-axis for two of our experiments:
comparing retrievers by question type, and choosing a default for max_hops in
the tool contract. Without it there is one average, the multi-hop result
disappears into it, and labelling only the new tranche leaves the lookup
baseline unsliceable from the same field. It is the one ask here that costs
you work beyond writing questions, so tell us if it should be ours to do.

3 · One requirement we can now satisfy for you

eval-ask asks for a 10-K/A beside the original it amends, so as_filed and
as_restated are testable. We have a verified candidate: ADM FY2023, accession
0000007084-24-000009 and its amendment 0000007084-24-000051. Note 17 corrects
Nutrition intersegment revenue from $114m as previously reported to $48m
restated, and both sides are tagged us-gaap:Revenues.

4 · Three asks from the contract reply (eval-reply.md)

- Citation scoring has to join on the provenance key, not chunk_id. Ours is
  always null — the graph arm cites a section, the structured arm cites a
  fact. Keyed on chunk_id, a traversal answer scores as uncited rather than
  scoring badly.
- Confirm whether rerun scoring keys on index_version alone. Ours moves
  extraction_version, so our rebuilds would be invisible to it.
- In writing: scores are not comparable across tools. If an abstention
  threshold is fitted on RAG's distribution and applied across the router, it
  will abstain on the wrong arm.

5 · Which pilot corpus is real

The two gold sets imply two different corpora, and there is a
pilot-corpus-setup branch as well. The sharper version of the same question:
three different Microsoft 10-K accession numbers are in play across our
manifest, EDGAR's latest, and a mock docstring. Every number anyone reports
this term is only comparable once one of them wins.

What we are asking for, and when

Gold set v1 freezes in W6. A question not agreed this week is one the
benchmark never asks.

- By Thu 22 Oct: yes or no on the tranche going in, and who writes the
  questions.
- Fri 23 Oct: we send the graph path for each of the 19, so you are writing
  against something concrete rather than a shape.
- During W6, before the freeze: the questions themselves.

— Adeline
```

## Two things the note commits us to

**The `hops` labelling may come back to us.** Section 2 offers to take it. Labelling 42
existing questions is real work and it would land on the quality and evaluation hat. The
alternative was to ask for the field without acknowledging the cost, which is how an ask
gets quietly dropped. If it comes back, it is a card, not a favour.

**Friday is now a promise to another team.** The graph paths for all nineteen questions are
E0 on Extraction 2's W5 card, due Fri 23 Oct. Once this note is sent, that date is external.
If it is going to slip, say so before Friday rather than on it.
