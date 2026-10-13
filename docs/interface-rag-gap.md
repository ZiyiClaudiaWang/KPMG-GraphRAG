# Our envelope vs RAG's Retrieval API v0 — the gap

Written 2026-10-13, after reading `Rachel-xs/KPMG_RAG` on branch `contract-v0`.

## What RAG has already shipped

`kpmg-rag-retrieval/docs/retrieval_api.md` — **"Final v0, proposed for team sign-off on 2026-10-08"**, owned by the RAG team, enforced by `rag/contract.py` and `tests/test_contract.py`, with conformance tests running against both the real retriever and an Agent mock. It lists its consumers as Agent, Evaluation, UI and Data Engineering.

It is not a draft. It already reconciled three earlier conflicting versions (the RAG draft, repo v0.1, and the Agent mock) and documents what changed for each.

This matters because our Architecture §7 says the envelope "must match RAG's exactly." It does not match.

## Where it diverges

| Concept | Ours (Architecture §7) | RAG's v0 |
|---|---|---|
| Return shape | `RetrievalResult` dict with `answers` + `evidence` | `list[RetrievedChunk]` — no wrapper |
| Accession | `accession` | `accession_number` |
| Chunk key | `source_chunk_id` | `chunk_id` |
| Section key | `source_section_id` | *absent* |
| Section label | `section_path` (`"Item 7 > Liquidity..."`) | `section` (canonical key, `"Part I Item 2"`) + `metadata.section_title` |
| Text | `quote` — verbatim span | `text` — the whole chunk, never rewritten |
| Offsets | `span` | reserved `metadata.char_start` / `char_end`, **not yet delivered** |
| Version | `corpus_version` | three: `chunking_version`, `extraction_version`, `index_version` |
| Period | — | `reporting_period_end`, `fiscal_year`, `fiscal_period` |
| XBRL | `fact_id`, `context_ref`, `unit_ref`, `basis` | *absent — explicitly out of scope for v0* |
| Company | `company_name`, `source_url` | `company_name`, `source_url` — **these two match** |

Three divergences are structural rather than cosmetic:

**1 · They return a list, we return an envelope with `answers`.** Their §1 states `retrieve()` does not generate answers. Ours has an `answers` field. Composition is the Agent's job, so our `answers` field is probably wrong and should be dropped rather than defended.

**2 · Structured retrieval is out of scope in their v0.** Their §8: "Structured XBRL facts and text-to-SQL are out of scope for v0. They are expected in a separate tool or a v1 hybrid contract, and will not be added to `retrieve()` silently." So our structured arm cannot ride their contract — it needs to be a sibling tool, which is what we assumed, but it also means "one envelope across all three tools" was never their plan.

**3 · They have already decided the UI citation contract.** Their §13, Agent-1: cite `company_name, form, reporting_period_end, section, source_url`. No `section_id`, no `quote`. If UI builds to that, our section-level citations have nowhere to render.

## Where they have already solved problems we flagged

Worth knowing before proposing anything — several of our open items are closed on their side, differently.

- **Quote-based scoring.** Their §10 grades relevance by `chunk_id` when gold chunk IDs exist, otherwise by *evidence matching* — metadata keys plus `contains` strings — and says "gold labels survive re-chunking only through evidence matching." That is the quote-first approach we were going to propose. Align, don't re-propose.
- **XPath.** Their §6 already reserves `source_locator` (XPath) as an optional metadata key to be filled once Data delivers it, alongside `char_start`, `char_end` and `source_sha256`. So the XPath coordinate system in the gold set is anticipated in their contract, not an oversight.
- **Leakage.** Their §10 already states gold questions and answers must never be indexed.
- **Determinism and versioning.** `index_version` is a hash of the chunk file; every evaluation result must record it.

## What to do

**Adopt their field names wherever they already have one.** `accession_number`, `chunk_id`, `section`, `text`, `company_name`, `source_url`, `reporting_period_end`. We lose nothing by conforming and we stop UI and Eval writing two of everything. Their contract is further along, has tests, and has three other consumers already building against it.

**Ask for exactly two additions, both additive and optional** — their §6 already has a mechanism for this, and they state additive metadata keys need no new contract version:

- `section_id` — stable across re-chunking, which `chunk_id` explicitly is not ("stable across rebuilds as long as `chunking_version` does not change"). This is the one thing we genuinely cannot get from their schema, and it is what our graph anchors on.
- `quote` — the matched span inside `text`, for UI highlighting. Lower priority: their `text` plus `contains` matching may be enough.

**Accept that the structured arm is a separate contract.** Write `structured_search` as a sibling tool returning facts with `fact_id`, `context_ref`, `unit_ref`, `basis`. Do not try to force XBRL into `RetrievedChunk`.

**Drop `answers` from our envelope.** Retrieval tools return evidence; the Agent composes.
