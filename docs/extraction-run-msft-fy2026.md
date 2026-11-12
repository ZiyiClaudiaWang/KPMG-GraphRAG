# A real extraction run — MSFT FY2026, 2 Nov 2026

The claim pipeline (PR #35) run end to end on one filing, against the Data team's database
rather than a fixture. Written down because three things were only knowable by running it:
what the labeller actually decides, what a run costs, and whether a Claim can be located in
the source.

**Filing:** MSFT 10-K, accession `0001193125-26-323660`, fiscal year ending 2026-06-30, filed
2026-07-29.

**Outputs** are not in the repository — they rebuild. See "Reproducing it" below.

## What came out

Data holds 27 sections for this filing. The pipeline keeps 3 — Business, Risk Factors and
MD&A — drops 23 and flags 1 for review.

```
27 sections  ->  3 kept
1,165 sentences segmented
  890 occurrences of 889 Claims
   19 RiskFactors
```

| label | count | decided by |
| --- | --- | --- |
| `stated_fact` | 589 | model |
| `hedged_risk` | 287 | model |
| `not_a_claim` | **210** | **rules, before any model call** |
| `forward_looking` | 47 | model |
| `not_a_claim` | 28 | model |
| `needs_review` | 4 | model |

Then 33 more are held by validation, for list-intro boundaries (26), non-verbatim quotes (9)
and known product-list fragments (2).

### The prefilter does most of the dropping

210 of the 238 `not_a_claim` verdicts are made by rules, before any model call. Each records
which rule fired:

| rule | n | example |
| --- | --- | --- |
| `heading` | 101 | `"GENERAL"` |
| `table_residue` | 92 | `"Name \| \| Age \| \| \| Position with the Company \|"` |
| `cross_reference` | 12 | `"The information found on these websites is not part of..."` |
| `section_title` | 3 | `"ITEM 1. BUSINESS"` |
| `too_short` | 2 | `"Piracy."` |

**All 92 `table_residue` drops came from the pipe rule. The "more than 30% digits" rule fired
zero times on this filing.** That rule had been flagged as a risk to genuine prose — a revenue
sentence is mostly numerals — and on real text it never triggered. Worth re-checking on a
filing with denser MD&A tables before concluding it is harmless, but it is not the hazard it
looked like.

### What the model rejects that rules do not

Mostly list stems, which is what the prompt asks for:

```
"This segment primarily comprises:"                       (three times)
"In addition to computing device market volume, Windows OEM revenue is impacted by:"
"The mix of computing devices based on form factor and screen size."
```

### The four `needs_review`

Three are list fragments where the model declined rather than guessed:

> *"Windows and Devices, including Windows OEM licensing..."*
> note: *"List fragment naming Windows and Devices components; not a complete claim on its own
> but part of segment description."*

The fourth is different and the distinction matters:

> *"We may have additional tax liabilities."*
> note: *"CODE REVIEW ONLY: self_or_future_context_reference. No candidate Claim published...
> **This is not a model classification.**"*

The pipeline separates *the model was unsure* from *the code set this aside*. Both land in the
same bucket; only one is a judgement.

## What a run costs, and why the estimate is five times higher

```
66 requests · 212,549 tokens · $0.1443
```

The dry run reported **$0.79**. That is not a bad estimate; it is a deliberately pessimistic
one, and the difference is almost entirely one assumption.

| | |
| --- | --- |
| output per request, actual | 765 tokens |
| output allowance assumed | 4,000 tokens |
| output cost, worst case | $0.4224 |
| output cost, actual | $0.0807 |
| **saved on output** | **$0.3417** |

`max_completion_tokens: 4000` is a ceiling, not a target. Labelling fifteen sentences needs
about 765 tokens of JSON, a fifth of the allowance.

It has to be pessimistic: `reserve_cost()` runs **before** each request to check the
`budget_usd` ceiling, so it must assume the worst or a run could cross its budget mid-flight.
The function is named `reserve_cost`, not `estimate_cost`, for that reason.

Caching contributed almost nothing — $0.0013, from 3% of input tokens. Each batch sends
different sentences, so only the system prompt repeats.

**Rule of thumb: divide the dry-run figure by about five for a real run.** All five pilot
filings would be roughly $0.75, not $4.

## Where a Claim sits in the source

Every Claim already carries Data's `section_id` and character offsets within that section's
content:

```json
"section_id": "e358f4a0-fed6-4530-885f-66ea84d58285",
"source_fragments": [{"start": 27, "end": 178, "quote": "Microsoft is a technology company..."}],
"provenance": {
  "raw_spans": [[27, 178]],
  "coordinate_system": "exact_data_section_content_unicode_codepoints",
  "section_content_sha256": "c48e4cd60e45ed364858a893bfbd9db6d81f9e2a1397dde1148d47136d7b2b63"
}
```

Checked on the real output: slicing Data's `content[27:178]` for that section returns the
Claim's `quote` **exactly**. So a Claim is locatable to the character, not merely to its
section.

Two details worth having in writing, because they are the questions anyone integrating will
ask:

**The offsets are relative to the section, not the document.** Data's section record carries
`offset_start: 8071` for where the section begins in the document; the Claim's spans are
positions inside that section's `content`. Absolute document position is
`section.offset_start + span.start`.

**The unit is Unicode codepoints,** named explicitly in `coordinate_system`. Not bytes, which
matters wherever a filing contains non-ASCII characters — and these do, in the form of curly
quotes in section titles.

`section_content_sha256` pins the exact text the offsets were computed against, so a consumer
can tell whether the content has moved under them.

### For the Vector/RAG team

The ask was whether each Claim can carry Data's `section_id` plus `char_start`/`char_end`
within that section, in the same offset scheme as Data's chunks. **The first half already
ships**, in the fields above.

The part still to confirm is "same scheme as Data's chunks": whether their chunk offsets are
also section-relative, and also codepoints. Ours are both, and
`coordinate_system` says so on every record. If their chunks are document-relative or
byte-based, the conversion is arithmetic rather than re-extraction — but it should be
confirmed with Data rather than assumed, since nothing on either side currently asserts it.

## Reproducing it

Outputs are deliberately not committed; they rebuild from the database in about ten minutes,
most of which is the API call.

```bash
# 1 · export the sections (needs .env.aws, see data-access.md)
cd ~/db-test
python3 graphrag_data.py fetch --accessions 0001193125-26-323660

# 2 · run the pipeline (needs OPENAI_API_KEY in extraction_pipeline/.env)
cp ~/db-test/data/sections/*.jsonl <repo>/data/sections/
cd <repo>/extraction_pipeline
python3 run_pipeline.py --steps clean,filter,segment
python3 run_pipeline.py --steps label --dry-run      # prints the worst-case cost
python3 run_pipeline.py --steps label
python3 run_pipeline.py --steps validate,risk_factors,build_claims
```

Reading the results:

| file | what it holds |
| --- | --- |
| `sentences.jsonl` | every segmented sentence, with its prefilter rule if one fired |
| `labels.jsonl` | every verdict, including the dropped ones, with its source |
| `review_queue.jsonl` | units held for a person, with `candidate_retained` |
| `claims.jsonl` | survivors only, with offsets and provenance |
| `usage.jsonl` | one row per request: tokens, latency, estimated cost |

"What was dropped and why" is a join of the first two on `sentence_id`.

## One environment note

The labelling step fails against current `openai` and `httpx` versions with a `TypeError`
raised inside the HTTP response decoder, before any reply is parsed. Setting
`Accept-Encoding: identity` on the client avoids it. Tracked as issue #46.

The failure is worth recording for a second reason: the no-automatic-retry guard behaved
exactly as designed. It stopped on the first error, wrote `attempt.json` with
`billing: unknown`, and refused to resend until the attempt had been looked at. A library
fault that could have quietly burned sixty-five requests cost nothing.
