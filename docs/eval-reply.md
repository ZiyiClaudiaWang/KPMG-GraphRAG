# Reply to Eval on the retrieval contract

Written on 2026-10-22. Answers Eval's six requests on RAG's Retrieval
API v0.

The requests are addressed to RAG — `retrieve()`, the log files, `config.yaml` and the
hybrid scores are all theirs. But we conformed to RAG's vocabulary on 13 Oct, so four of
the six land on us too.

**Eval has approved v0.** One consumer approving is not the four-way team sign-off we were
chasing, but it means the field names we adopted are the names Eval scores against.
Conforming was the right call. It does not answer [eval-ask.md](./eval-ask.md) — nothing in
the reply mentions a traversal tranche or the abstention controls.

## 1 · `trace_id` — done

Both tools take it as of 2026-10-14, optional, validated as a non-empty string or null. We
have no log to write it to yet, so we echo it at `metadata.trace_id` on every result, always
present and null when unset. Eval can join on it today. It moves to the `search()` envelope
when that exists — see §5.

## 2 · Cited `chunk_ids` will not find our evidence

**This is the one we need changed.**

Every result from both our tools has `chunk_id = None`. The graph arm cites a section, the
structured arm cites an XBRL fact, and `graphrag/contract.py` enforces that exactly one of
`chunk_id`, `section_id` or `metadata.fact_id` is populated — never the first, for us.

So if faithfulness and citation scoring joins on `chunk_ids`, every piece of graph and
structured evidence is invisible to it. Not scored low, absent. An answer built entirely on
traversal would look uncited.

**The ask:** log the provenance key, not the chunk key. One field holding whichever of the
three is set, or three nullable fields. Either works; keying on `chunk_id` alone does not.
Worth deciding before W8, because it also decides whether a graph-only answer can score at
all.

## 3 · Snapshots — the fallback already holds

Eval's alternative to snapshotting `chunks.jsonl` is logging ticker, form, period and
section per result. All four are required fields on every evidence object we return,
alongside all three version fields. Nothing to do. Two flags:

- We have no chunk file. Our analogue is the graph and the fact table, and the version that
  moves when we rebuild is **`extraction_version`**, not `index_version`. Keyed on
  `index_version` alone, our rebuilds are invisible.
- `chunking_version` is null on our results because we do not chunk. Deliberate, and in the
  contract.

## 4 · Component scores — fine, with one rule

`bm25_score` and `dense_score` are RAG's to add; we do not run hybrid search.

The rule that needs stating before anyone uses them: **scores are raw, unnormalised, and
comparable only within a single call to a single tool.** Our graph score is keyword or path
relevance, the structured arm's is a match over facts, and neither is on a BM25 scale. If an
abstention threshold is fitted on RAG's distribution and applied across the router, it will
abstain on the wrong arm. A cross-tool abstention signal is real work — scope it, don't
infer it from whichever scores happen to be logged.

## 5 · `request_id` on empty results — blocked on a decision we owe

We return a bare `[]` when nothing matches, so there is nowhere to put a `request_id`. This
is the `search()` envelope parked in [interface-rag-gap.md](./interface-rag-gap.md) §1,
where `latency_ms` and `tokens` were also going to live. Eval's request makes three reasons
to build it, so it should stop being deferred — and it needs agreeing with RAG so both sides
return the same shape. Until then `trace_id` covers the non-empty case and nothing covers
the empty one.

## 6 · Cost — ours is extraction, not retrieval

`config.yaml` price fields are RAG's. Our cost is OpenAI structured-output calls during
extraction, which nobody is logging. Worth logging tokens and cost per filing from the first
run rather than reconstructing it in W11.

## What we are asking Eval for

1. Join on the provenance key, not `chunk_id` — §2
2. Confirm whether rerun scoring keys on `index_version` or on all three version fields — §3
3. Agree in writing that scores are not comparable across tools — §4
4. Still open from [eval-ask.md](./eval-ask.md): the traversal tranche, the M14 abstention
   control, and which pilot corpus is real
