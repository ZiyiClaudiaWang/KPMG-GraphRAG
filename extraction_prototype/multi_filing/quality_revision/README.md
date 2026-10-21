# Source revision 1

This source-grounded draft revision contains **39 Claims, 48 nodes and 45
relationships**. The original three-filing run remains unchanged in `../results/`.
The revision was loaded into a separate graph scope and verified in the user's
Colab/Sandbox session. All classifications and revision drafts remain `needs_review`.

## Changes

1. Recover Apple's sentence beginning `As a result` from the earlier single-paragraph
   extraction. It matches the current source and retains the earlier model/request
   provenance. This was not a new model extraction or an exhaustive recall repair.
2. Add the preceding source sentence as context for JPM's `This situation` Claim.
3. Add the introduction and all eight preceding list items as context for JPM's
   `These types of changes` Claim. The list remains context, not new Claims.

`results/changes.json` records these three edits. The two context updates are
code-applied source annotations, not newly generated Claude responses. Original
model responses remain in the baseline directory. Their model/request provenance
does not imply the added context was returned by that model call.

## Validation

From the repository root:

```bash
python extraction_prototype/multi_filing/quality_revision/validate_revision.py
```

This first runs the original saved-result checker, then checks the exact revision
delta, recovery provenance, evidence fragments, complete context spans, Claim and
relationship identities, all 39 saved paths, and the derived review queue. It is
an offline consistency check, not a fresh API, SEC, or Neo4j run.

The user's live verification records show two loads at 48/45, 39 paths with
source evidence, two chunk-metadata simulations with stable counts/edge IDs,
and cleanup restoring original chunk metadata. Actual Data-team re-chunking was
not tested. Graph scope: `cw-extraction-three-filings-revision1`.

## Reproduction and limits

The updated `../Three_Filing_Prototype.ipynb` includes the original pilots and
the revision. It prompts for credentials, clears saved outputs in this repository,
and may incur four model calls in a fresh full execution. The revision targets
specific saved Claim IDs; fresh model responses can differ or omit those targets,
so an uncached live run may need review before the fixed revision cells apply.
Do not close findings merely because the notebook reaches its final cell.

For exact saved-result verification, use the script above without credentials.
`results/review_queue.csv` is derived from the 39 revision records; it is a queue
for review, not an assertion of human sign-off.

The known Apple omission has been recovered, and the two identified JPM context
gaps have source-grounded patches pending review. Broader Claim recall, missing
Microsoft issuer mentions, and the treatment of definitions remain open. Local
section IDs still require Data alignment. The team path API, roughly 100-node
graph and end-to-end Extraction v1 milestones are not completed by this revision.
