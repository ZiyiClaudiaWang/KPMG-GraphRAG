# Merge notes with Claudia's code

Moved from the pipeline README (26 Nov 2026). Step numbers below are those of the time of the merge (step 5 = label, 6 = validate, 7b = build Claims, 8 = load); see the [README](../README.md) for today's.

Claudia ran steps 5 to 8 on the five filings with her own version of this pipeline (handoff
package `GraphRAG_Five_Filing_Handoff`). Her changes were merged into this code as below.
The merged step 5 built the same 173 requests as her run, byte for byte. Since then the
step 2 and 4 fixes changed some of the text sent, the short numbers count within each request
(`c1` / `s1`, not her `s0042`), the context adds missing list intros, and the request hash
covers only the request (Cost tracking), so the requests and hashes no longer match hers. These notes describe
that merge; steps 2 to 8 have changed since (README, How it works), among them the
ids (#45: `sentence_id` and `source_unit_key` removed, `claim_id` and every reference by
`unit_id`; one line of `claim_review_flags()` in her `source_review.py` now reads context by
`unit_id`, its rules unchanged) and the hold for non-continuous text (removed).

## Taken into this code

- Step 4 (`segment.py`): her version, as it was at the merge ("Restrictions" sentence split,
  "no material changes" exception, `source_unit_key`).
- Steps 5 to 7b (`entity_annotation.py`): her module, kept; its prompt text moved into
  `label_v2.txt`.
- Steps 4, 6 and 7b (`tests/test_sentence_entities.py`): her test file (sentence boundaries,
  the step 4 prefilter, entity matching).
- Step 5 (`label.py`), set up so outputs equal her run:
  - **Prompt**
    - Her four prompt pieces (`label_v1.txt`, text in `checked_pilot.py`, text in
      `entity_annotation.py`, `batch_review_v3.txt`) joined into one file,
      `prompts/label_v2.txt`; `label_v1.txt` is kept for the record.
    - `prompt_id` (`kelly_label_v1_entity_mentions_batch_review_v3`) recorded as the prompt
      version, as in her records; the prompt text is the same.
  - **Requests**
    - Batches of 15 sentences, halved when a request is over 35,000 bytes.
    - US endpoint as the default `base_url`, configurable.
    - Same request identity and cache folder layout (`data/checked_pilot/`). The identity is
      still saved in full in `request.json`; since 4 November 2026 `request_hash` covers only
      the request and the runner version, not the whole identity
      (README, Cost tracking).
    - `store: false`, `max_completion_tokens`, temperature only for gpt-4.1 and gpt-4o models.
  - **Cost and safety**
    - No automatic retry by default; the run stops at a rejected reply (`max_retries: 0`,
      `stop_on_failure: true`).
    - No automatic resend: the OpenAI client does not retry; a request with no saved reply
      stops the run (`attempt.json`).
    - Source check before sending; request and raw reply saved before parsing.
    - Fuller usage records, cost estimate with cached-token price, `budget_usd`.
  - **Reply checks and recovery**
    - Her reply checks: one complete answer (`finish_reason` "stop"), no refusal, exactly
      `{"results": [...]}`, JSON schema check, then id and context checks.
    - `needs_review` label, pipeline-only like `not_a_claim`; `entity_mentions` in the reply.
    - Her fixes for faulty replies, from `Resume_Five_Filing_Batch_v2.py` and
      `Run_Three_Target_Repair.py`: unit quarantine (problem sentences become `needs_review`,
      the rest of the reply is kept) and one targeted repair request for sentences whose number
      was missing or repeated. Her repair was written for her 3 sentences; here it works for
      any number. The repair prompt (`prompts/repair_v1.txt`) is her text. Two names are
      general instead of hers: the schema name `target_gap_repair` (hers
      `three_target_gap_repair`) and the version `target_repair_v1` (hers
      `three_target_repair_v1`). Her earlier recovery script (`Resume_Five_Filing_Batch.py`,
      context only) is covered by the v2 rules.
- Step 6 (`validate.py`), following her `source_review.py` and `checked_pilot.py`:
  - Her context checks: no duplicate context ids, context in source order.
  - A review queue (`output/review_queue.jsonl`); held units do not become Claims.
  - Her `source_review.py` functions, logic unchanged. On 4 November 2026 the file was
    reformatted with black (line length 100) in a commit of its own; its syntax tree is
    identical to her handoff version, so only spacing and line breaks differ. To compare with
    her handoff line by line, use the version before that commit. Since #45,
    `claim_review_flags()` looks up context units by `unit_id` instead of joining her short ids
    to a section prefix; which units it flags is unchanged. Hold rules: `needs_review`, list
    intro, non-continuous text (removed since, see 3.1), mixed tense, past action with possible
    future, existing law as risk, and her hard-coded Microsoft product names (moved to `config/settings.yaml`).
  - Review queue entries in her format, with her flag names and reasons.
  - Entity checks taken: quote not in the sentence, "the Company" or "we" as an entity, generic
    references get no id, uncertain type, named subsidiary needing the catalog, and her segment
    names read from the filing text (`source_review.py`) with the two checks built on them.
  - On her labels, these rules hold 48 of the 49 units she held. The 49th she held by hand (an
    impairment-process sentence in Microsoft's 10-Q MD&A); hand decisions are not coded.
  - Kept, with open points recorded under Remaining issues in the README:
    - the list-intro rule also holds complete sentences (2.1)
    - the non-continuous rule depended on how the UI highlights quotes (3.1; removed since)
    - held units cannot be released yet (1.4)
  - The same 119 units carry entity flags as in her run.
  - Taken but specific to these filings (README, Remaining issues: rules that fit only this data): the product names and the
    segment-name patterns.
- Step 7b (`build_claims.py`): her Claim record, field for field (one record per occurrence,
  `candidate_occurrence_id`, source fragments, paragraph context, entity review), and her
  `claim_id` algorithm (exact text, no normalization).
- Step 8 (`load.py`): reads her record format. Her file uses the old text-keyed `claim_id`, so
  several lines can share one id; step 8 merges them into one Claim node. Her
  `candidate_occurrences.jsonl` loads directly when copied to `data/output/claims.jsonl` (2,420
  Claims, as in the graph loaded earlier).

## Not taken; kept in the handoff package as the record of her run

| File | What it does | Why not taken |
|------|--------------|---------------|
| `checked_pilot.py` | Her API-call path | Merged into `label.py` |
| `five_filing_batch.py` | Locked plan of the 173 batches; refuses to run if any code or input changes | Suited to one fixed run; blocks normal development. Its budget cap is merged |
| `source_review.py` (her full file) | Rule checks after the LLM, plus an offline review of three earlier entity pilots | Her four rule functions are in `source_review.py` unchanged. The offline pilot review and its hard-coded batch ids are not taken |
| `tests/test_batch_review_v3.py`, `tests/test_checked_pilot.py` | Tests of checked_pilot.py and five_filing_batch.py | Those two files are not taken; the merged code is tested in tests/test_pipeline.py |
| `02_code/Run_Five_Filing_Batch.py` | Colab launcher with the whole package embedded in base64 | Replaced by `run_pipeline.py` |
| `02_code/Load_Five_Filing_Graph.py` | Loads her results with `GRBatch*` labels | `load.py` uses ontology labels with `pipeline_scope` and reads her results directly |
| Guards in her `label.py`, `build_claims.py`, `load.py` | Disabled the original paths in favour of hers | Not needed with one path |
| Summary reports: `summary.json`, `section_coverage.json`, `model_target_ledger.jsonl` (written by `five_filing_batch.py` and `Resume_Five_Filing_Batch_v2.py`; final versions `resolved_*` by `Run_Three_Target_Repair.py`) | Counts for the run and per section, and one ledger row per sentence sent to the model | Not written by this code. The same counts can be read from `labels.jsonl`, `review_queue.jsonl` and `claims.jsonl` |
