# What the Agents team needs to know

Draft, 2026-10-13.

## The short version

We have aligned `graph_search` and `structured_search` to RAG's Retrieval API v0. Same signature, same filters, same errors, same ordering rules, same evidence field names. If you have built against `retrieve()`, our two tools behave the same way.

Full shape in [architecture.md §7](./architecture.md#7--tool-signature-and-evidence-shape).

## Five things that change for you

**1 · The `answers` field is gone.** Our earlier envelope had `RetrievalResult.answers`. It should not have — retrieval returns evidence, you compose. Both our tools now return a plain `list[Evidence]`, matching RAG.

**2 · One error rule across all three tools.** `[]` means no evidence was found. An exception means retrieval is broken. Do not answer as though there is no evidence when an exception was raised. Unknown filter keys raise rather than being ignored, so a typo surfaces immediately instead of silently widening a search.

**3 · Do not threshold or compare scores.** Scores are raw and unnormalised. They are comparable only within a single call, and *never* between tools — a 0.03 from hybrid RRF and a 0.03 from a graph path score mean nothing to each other. Use order.

**4 · Filter names follow RAG's.** `form` not `form_type`, `reporting_period_end` not `period_end`, `accession_number` not `accession`. We add `max_hops` for the graph arm and `concept`, `segment`, `basis` for the structured arm.

**5 · Provenance differs by tool, and citations must branch.** Exactly one of `chunk_id`, `section_id`, `metadata.fact_id` is populated. RAG fills the first, we fill the second, the structured arm fills the third. Code that assumes `chunk_id` will null-pointer on two of three tools.

## Two things we need decided

**The structured tool has no agreed contract.** RAG's §8 puts XBRL and text-to-SQL out of scope for v0 — "expected in a separate tool or a v1 hybrid contract, and will not be added to `retrieve()` silently". So `structured_search` currently sits outside everything anyone has signed. It needs a review slot with you.

**Hybrid questions need a routing convention, not just a router.** "How did R&D spend change after the 2023 acquisition?" needs the graph to resolve the event, the structured arm to supply numbers, and RAG to supply prose. Three calls, one answer, three citation styles. Worth agreeing how the composed answer attributes each part before the W7 demo rather than after.

## One thing we would ask UI to reconsider

RAG's §13 decided citations render from `company_name, form, reporting_period_end, section, source_url`. That works for chunk evidence. It has no room for `section_id` or for a fact, so a graph-derived or XBRL-derived claim cites at document level only — the analyst cannot see *which* part of the filing supports it. Adding `section_id` and `metadata.fact_id` to what UI reads costs little and is the difference between an auditable answer and a plausible one.
