# Real-filing extraction pilot

Latest recorded draft: [source revision 1](multi_filing/quality_revision/README.md),
with 39 Claims, 48 nodes and 45 relationships. Review and completeness work remain open.

The expanded [three-filing prototype](multi_filing/README.md) records 38 Claims,
47 nodes and 44 relationships, with multi-fragment evidence and open quality
findings. The files below retain the original single-paragraph pilot.

CW's working prototype: one paragraph from Apple FY2025 Item 1A,
accession `0000320193-25-000079`, extracted with Claude structured output
and Pydantic. This is separate from the constructed schema probe in `toy_graph/`.

## Files

- `Extraction_Prototype_v1.ipynb`: runnable Colab workflow, with outputs cleared.
- `results/`: original extraction, source excerpt, resolutions, Claims, runtime
  versions, and the user's recorded Neo4j verification results.
- `validate_saved_pilot.py`: standard-library-only consistency check of those files.

## Check saved results without API or database credentials

From the repository root:

```bash
python extraction_prototype/validate_saved_pilot.py
```

This checks source spans, deterministic identities, source metadata, request
fingerprints, resolution records and saved path evidence. It does not rerun the
live database tests or independently authenticate the recorded execution.

## Rerun the live workflow

Open the notebook in Colab and run cells in order. It downloads the working
`claudia/ontology-merge` branch, retrieves the SEC HTML, locates Item 1A, extracts
one paragraph, resolves issuer references separately, and loads Neo4j.
Supply a contact email for SEC requests, an Anthropic API key and Neo4j connection
credentials through the runtime prompts. Model calls incur API charges. The
notebook lists available models; adjust `MODEL` if the saved choice is unavailable.
Package versions from the successful run are in `results/runtime.json`; notebook
install commands use compatible ranges/current SDK versions rather than a lockfile.

Downloaded HTML and generated outputs live under `/content/` in Colab. Verify
the HTML checksum in `results/source_metadata.json` when comparing XPath locations.
Saved request fingerprints allow identical notebook requests to reuse local results;
a fresh runtime makes a new model request, which may produce different output.
The graph uses `prototype_id = cw-extraction-prototype-v1` and an additional
`ExtractionPrototype` label for isolated pilot checks. Exact 6/5 count assertions
apply to this one-paragraph scope. Do not expand that scope without updating them.

## Recorded outcome

- Four mentions; issuer reference resolved to Apple CIK `0000320193` in code.
- Three sentence-level Claims, all `stated_fact` and still `needs_review`.
- Six nodes and five relationships after both first and second loads.
- Three `Company -> Filing -> Section -> Claim` paths with provenance at every hop.
- Claim support contains source XPath and offsets into whitespace-normalized block text.
- Two simulated chunk-metadata versions preserved counts and edge IDs; test metadata
  was removed afterward.

## Remaining work

This is one filing paragraph, not the two-to-three-filing prototype required by #5,
the roughly 100-node real graph, or Extraction v1 across three to five companies.
The local section key needs alignment with Data's authoritative section identity.
The path helper is local to the notebook, not an implementation of the team API.
Two `Other` mentions remain unresolved and one risk theme needs review.
Company identity is filer metadata; Claim subjects preserve their original scope.
No `CONCERNS_RISK` edges have been inferred. Actual Data-team re-chunking and
production identity resolution have not been tested. Repeated quotes within a
section share a logical Claim ID; occurrence-level identity remains future work.
XPath validation here compares stored locators; it does not re-fetch and validate
the complete source DOM offline.
