### 1. Objective

Design a small, evidence-grounded knowledge graph for SEC financial filings (10-K, 10-Q, and 8-K) to support cross-company comparisons, multi-period analysis, and multi-hop financial question answering.

To define how financial entities, relationships, and claims should be represented, while ensuring that every extracted statement can be traced back to its original filing.

## 2. Proposed Entities / Nodes

| Node Type | Definition | Example |
| --- | --- | --- |
| Company | SEC-reporting company | Apple |
| Filing | SEC financial document | FY2025 10-K |
| Section | Section within a filing | Item 1A – Risk Factors |
| SourceChunk | Original extracted passage | Paragraph from Item 1A |
| FinancialMetric | Financial indicator | Revenue |
| MetricObservation | Metric value for a specific period | FY2025 Revenue = X |
| RiskFactor | Identifiable risk theme | Supply Chain Risk |
| Claim | Statement disclosed by a company | “Supply constraints may affect production.” |
| Segment | Reported business segment | Services |
| Executive | Company officer | Chief Financial Officer |

Recommendation: Keep `MetricObservation` separate from `FinancialMetric`, because revenue values change across companies and reporting periods.

## 3. Proposed Relationships

| Source | Relationship | Target |
| --- | --- | --- |
| Company | FILED | Filing |
| Filing | HAS_SECTION | Section |
| Section | HAS_CHUNK | SourceChunk |
| Filing | REPORTS_METRIC | MetricObservation |
| MetricObservation | OF_METRIC | FinancialMetric |
| SourceChunk | SUPPORTS | Claim |
| SourceChunk | SUPPORTS | MetricObservation |
| Claim | ABOUT_COMPANY | Company |
| Claim | CONCERNS_RISK | RiskFactor |
| Company | HAS_SEGMENT | Segment |
| Company | PEER_OF | Company |

### 4. Claims and Provenance

We should distinguish between reported facts, risk disclosures, forward-looking statements, and inferred relationships.

For example, a filing stating that supply chain disruptions may affect revenue should not automatically become a factual claim that supply chain disruptions caused a revenue decline.

Each extracted claim or financial observation should retain:

- `filing_id` and `source_chunk_id`
- `section_id` and original evidence excerpt
- `reporting_period`
- `claim_type` and relevant qualifiers
- `extraction_status` or review status

This will help prevent unsupported relationships and enable source-grounded retrieval.

### 5. Business Use Cases to Validate the Ontology

Use Case A: Financial Comparison

Question: How do Company A and Company B compare in revenue growth and operating margin in FY2025?

Required entities: Company, Filing, FinancialMetric, MetricObservation, SourceChunk.

The graph needs to connect each company to its reported financial results, preserve fiscal periods, and allow comparisons using cited source values.

Use Case B: Risk Comparison Across Years

Question: How did Company A's supply chain risk disclosures change between FY2024 and FY2025?

Required entities: Company, Filing, Section, SourceChunk, Claim, RiskFactor.

The graph should identify related risk themes across filings while preserving the specific wording, reporting period, and supporting evidence of each claim.

### 6. Proposed Toy Graph

For the initial Neo4j prototype, use just three filings:

- Company A — FY2024 10-K
- Company A — FY2025 10-K
- Company B — FY2025 10-K

Extract a small set of financial observations and risk disclosures, build their relationships, then manually validate retrieval paths and citations.

### 7. Key Questions for Team Discussion

1. Should claims and financial observations be separate nodes rather than properties attached to companies?
2. How should we distinguish explicit disclosures from LLM-inferred relationships?
3. What minimum metadata should Data Engineering provide to ensure provenance?
4. Should the first prototype prioritize financial metrics, risk disclosures, or both?
5. How should we define peer relationships between companies?
