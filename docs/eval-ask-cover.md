# Covering note for the Eval ask

Drafted 2026-10-21, revised 2026-10-22. **Not sent.** To Yulei Ding, from the contracts seat.

[eval-ask.md](./eval-ask.md) was written on 13 October and [eval-reply.md](./eval-reply.md)
on the 14th. Neither went out. This note sends both, says what has changed since, and puts
a date on the part that cannot wait.

Send it from the co-lead contracts seat — nobody else writes to another team. It goes to
Eval rather than RAG: section 4 touches RAG's scorer, but the decisions in it are Eval's.

**Revised 22 Oct because Eval published Gold Set v1 — 100 questions — before this went
out.** The note as first drafted argued against two sets that v1 replaces, and offered to
label 42 questions that are now 100. Sending that version would have read as though we had
not opened their file. `eval-ask.md` was rewritten the same day to review v1 and to name
the filings each question runs against, so it is still two attachments, not three.

---

## The note

```
Subject: Traversal tranche for gold set v1 — we need a decision this week

Yulei — we read Gold Set v1 today. Two documents attached: the tranche ask,
rewritten today against what you shipped, and the contract reply from 14
October that never went out. Short version of each below, and one part that
is now urgent.

Before anything else: v1 is a clear step up on both earlier sets. The ten
unanswerable and false-premise questions go further than we were going to
ask for, and EV1-037 through 039 already join two filings to derive Q4, so
the multi-document mechanism is built and scored.

1 · The traversal tranche (eval-ask.md)

None of the 100 requires following a relationship to something the question
could not name. Your four cross-company questions and the 11 new ones are
fan-out — independent lookups with a ticker filter, compared afterwards. A
tuned vector index does that well and does not need a graph to do it. The
deepest question in the set crosses two filings of one company on a period
boundary, so nothing in it needs more than two hops.

So if round 1 runs at W8 against v1 as it stands, the graph arm scores on
work vector already does, and we finish with no evidence the graph earned its
place. That is a coverage gap, not a criticism.

Worth separating from the rest: all ten of your abstention questions test a
missing fact. None tests a fabricated relationship, which is the failure mode
specific to a graph. A graph that invents edges answers every other question
in v1 correctly.

We are not writing a second gold set. We want 15-20 questions added inside
yours, in your format. We propose the shapes and supply the graph path each
one should exercise; you write, validate and own the final wording. We should
not author the benchmark our own arm is graded on.

The attached document has 19 candidates. Three of your questions already meet
three of them. Of the rest, eleven can be written against filings we can name
today once we settle point 5, and four need the corpus to grow.

2 · One field we need, and it is not free for you

Every question needs a hops count or a tier: "multihop" label — including the
100 that already exist, not only the new ones. Your deep tier marks reasoning
effort rather than documents crossed: EV1-037 is deep and spans two filings,
GSv0-MSFT-030 is deep and spans none, so it cannot do this job.

This looks like a nicety. It is the x-axis for two of our experiments:
comparing retrievers by question type, and choosing a default for max_hops in
the tool contract. Without it there is one average, the multi-hop result
disappears into it, and labelling only the new tranche leaves the lookup
baseline unsliceable from the same field. It is the one ask here that costs
you work beyond writing questions, and v1 more than doubled that cost, so
tell us if it should be ours to do. We will take it if so.

3 · One requirement we can now satisfy for you

eval-ask asks for a 10-K/A beside the original it amends, so as_filed and
as_restated are testable. We have a verified candidate: ADM FY2023, accession
0000007084-24-000009 and its amendment 0000007084-24-000051. Note 17 corrects
Nutrition intersegment revenue from $114m as previously reported to $48m
restated, and both sides are tagged us-gaap:Revenues.

That pair comes with its gold answers already checked, so three questions
(M12, M13, M19) cost you almost nothing to add. One warning for the gold
note: the original encodes the elimination negative and the amendment
presents the restated figure positive, so matching on concept and period
alone pairs the wrong two numbers.

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

This is now the question that unblocks most of the above, and v1 sharpened
it rather than settling it. Three corpora are live: the six banks and
industrials in gold_v0.jsonl, our pilot manifest (MSFT, JPM, AAPL 10-Ks plus
the ADM pair), and v1's Microsoft-plus-peers. They overlap by ticker and not
by filing — v1 uses Microsoft's FY2026 10-K and Apple's Q2 FY2026 10-Q, our
manifest has both companies' FY2025 10-Ks.

Merged, that is better than either alone: two consecutive Microsoft 10-Ks,
three Exhibit 21s, and an amendment pair. Eleven of the nineteen become
writable immediately. Unmerged, no question that needs Exhibit 21 can be
written at all, because v1 reaches only one 10-K.

What we are asking for, and when

Gold set v1 freezes in W6. A question not agreed this week is one the
benchmark never asks.

- Fri 23 Oct: we send the graph path and the filings each of the 19 joins,
  whatever you decide, so you are reading something concrete rather than a
  shape. Eleven of them already name their accessions in the attached ask.
- By Mon 26 Oct: yes or no on the tranche going in, and who writes the
  questions. Separately, yes or no on merging the corpora, which the tranche
  depends on.
- During W6, before the freeze: the questions themselves.

— Adeline
```

## Two things the note commits us to

**The `hops` labelling is ours if Eval says no, and it is bigger than it was.** Section 2
offers to take it. That offer was costed against 42 existing questions and v1 makes it 100.
Decided on 22 Oct to keep the offer, because it is the x-axis for both the retriever
comparison and the `max_hops` default, and an ask that acknowledges no cost is how an ask
gets quietly dropped. If it comes back, labelling 100 questions is a card on the quality and
evaluation hat, not a favour squeezed in around other work.

**Friday is a promise to another team, and it is tomorrow.** The graph paths for all nineteen
questions are E0 on Extraction 2's W5 card, due Fri 23 Oct. Decided on 22 Oct to hold the
card rather than slip it, so the note promises Fri 23. Eleven of the nineteen already name
their filings in [eval-ask.md](./eval-ask.md), which leaves eight to write in a day.

The two dates are also now the right way round. The first draft asked for a decision on Thu
22 and promised the paths on Fri 23, and once the note missed the 21st the decision date had
passed before sending. We send the paths first, unconditionally, and ask for the decision by
Mon 26 — so Eval is deciding with the concrete material in front of them rather than
agreeing to a shape. It also means a late answer costs us nothing, because the paths go
either way.

Once this note goes, Friday is external. If it is going to slip, say so before the day
rather than on it.
