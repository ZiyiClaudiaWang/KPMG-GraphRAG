# Reply to Eval on the retrieval contract

Drafted 2026-10-14, after Eval approved RAG's Retrieval API v0 with six requests.

Their requests are addressed to RAG — `retrieve()`, `retrieval.jsonl`, `generation.jsonl`,
`config.yaml`, and BM25-plus-dense hybrid scores are all theirs. But we conformed to RAG's
vocabulary on 13 Oct, so four of the six land on us too. This is what we send back.

## First, what this settles

**Eval has approved v0.** That is not the same as the team sign-off we were chasing in
[action-items.md](./action-items.md) §3 — one consumer approving is not four — but it closes
the question for one of them, and it means the field names we adopted are now the names Eval
is scoring against. Conforming was the right call.

It does not answer [eval-ask.md](./eval-ask.md). Nothing in the reply mentions a traversal
tranche or the M14–M16 abstention controls. Those are still open and still the thing that
decides whether W8 can show the graph earned its place.

## 1 · `trace_id` — done

Both our tools take it as of 2026-10-14. `graph_search(query, filters, top_k, trace_id)` and
`structured_search` likewise, optional, validated as a non-empty string or null.

We have no log to write it to yet, so we echo it at `metadata.trace_id` on every result,
always present and null when unset. Eval can join on it today. It moves to the `search()`
envelope when that exists — see §5.

## 2 · Cited `chunk_ids` will not find our evidence

**This is the one we need changed.**

Every result from both our tools has `chunk_id = None`. The graph arm cites a section, the
structured arm cites an XBRL fact. `graphrag/contract.py` enforces that exactly one of
`chunk_id`, `section_id` or `metadata.fact_id` is populated, and for us it is never the first.

So if faithfulness and citation scoring joins `generation.jsonl` on `chunk_ids`, every piece
of graph and structured evidence is invisible to it. Not scored low — absent. An answer built
entirely on traversal would look uncited.

**The ask:** log the provenance key, not the chunk key. One field holding whichever of the
three is set, or three nullable fields. Either works; keying on `chunk_id` alone does not.

This is worth deciding before W8 rather than during it, because it also decides whether a
graph-only answer can score at all.

## 3 · Snapshots — the fallback already holds for us

Eval's alternative to snapshotting `chunks.jsonl` is logging ticker, form, period and section
per result. Every one of those is already a required field on every evidence object we return,
alongside all three version fields. Nothing to do.

Two things to flag:

- We have no chunk file. Our analogue of a snapshot is the graph and the fact table, and the
  version that moves when we rebuild is **`extraction_version`**, not `index_version`. If
  reruns are keyed on `index_version` alone, our rebuilds are invisible to it.
- `chunking_version` is null on our results, because we do not chunk. That is deliberate and
  in the contract, not a gap.

## 4 · Component scores — fine, with one rule

`bm25_score` and `dense_score` are RAG's to add; we do not run hybrid search.

The rule that needs stating before anyone uses them: **scores are raw, unnormalised, and
comparable only within a single call to a single tool.** Our graph score is keyword or path
relevance. The structured arm's is a match over facts. Neither is on the same scale as a BM25
score. If an abstention threshold gets fitted on RAG's distribution and then applied across
the router, it will abstain on the wrong arm.

If Eval wants a cross-tool abstention signal, that is a real piece of work and we should scope
it, not infer it from whichever scores happen to be logged.

## 5 · `request_id` on empty results — blocked on a decision we owe

We return a bare `[]` when nothing matches. No wrapper, so there is nowhere to put a
`request_id`.

This is the `search()` envelope parked in [interface-rag-gap.md](./interface-rag-gap.md) §1,
where `latency_ms` and `tokens` were also going to live. Eval's request makes three reasons to
build it, so it should stop being deferred. Needs agreeing with RAG so both sides return the
same shape rather than two envelopes.

Until then, `trace_id` in metadata covers the non-empty case and nothing covers the empty one.

## 6 · Cost — ours is extraction, not retrieval

`config.yaml` price fields are RAG's. Our cost is OpenAI structured-output calls during
extraction, which nobody is logging and which the toy graph will make concrete this week.
Worth logging tokens and cost per filing from the first run rather than reconstructing it in
W11.

## What we are asking Eval for

1. Join on the provenance key, not `chunk_id` — §2
2. Confirm whether rerun scoring keys on `index_version` or on all three version fields — §3
3. Agree that scores are not comparable across tools, in writing — §4
4. Still open from [eval-ask.md](./eval-ask.md): the traversal tranche, M14–M16, and which
   pilot corpus is real
