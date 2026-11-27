"""Step 6 - entities a Claim may link to: subsidiaries and segments (no LLM).

  subsidiaries.py  step 6a: Exhibit 21 of each 10-K (SEC EDGAR)
  ex21/            library for 6a, imports nothing from the pipeline: edgar.py downloads,
                   parse.py reads an exhibit, normalize.py maps jurisdictions and builds ids
  catalog.py       step 6b: entity_catalog.json (subsidiaries + XBRL segments + registrant names)
  annotation.py    Claudia's entity matching (used by steps 7-9): model mentions vs the catalog
  headings.py      step 6b: catalog names in headings, inherited by the units under them (step 9)
"""
