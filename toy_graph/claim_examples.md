# Claim examples — Apple FY2025

Status: Draft annotations prepared by CW with AI assistance; pending team review.
Date: 2026-10-16

Purpose: Examine real Item 1A sentences before designing the Claim node.

## Source

- Company: Apple Inc.
- CIK: 0000320193
- Form: 10-K
- Fiscal year: 2025
- Accession number: 0000320193-25-000079
- Section: Part I, Item 1A — Risk Factors
- Source URL: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm

All excerpts below come from this section. Search the quoted sentence in the source to locate it.

## Annotation rules

- stated_fact: A situation or action presented by the company as existing or having occurred. This does not mean independently verified.
- forward_looking: An explicit expectation or prediction about the future.
- hedged_risk: A possible adverse event or outcome, without asserting that it will occur.

These categories can overlap. For mixed sentences, assign a primary label and record the other content in a note. Preserve qualifiers and avoid converting possibilities into established facts.

## 01 — International sales

**Original sentence**

> The Company has international operations with sales outside the U.S. representing a majority of the Company’s total net sales.

**Label:** stated_fact

**Reason:** Describes the company's existing sales distribution.

**Interpretation limit:** Does not establish that international operations have caused losses.

## 02 — Outsourced logistics

**Original sentence**

> The Company has also outsourced much of its transportation and logistics management.

**Label:** stated_fact

**Reason:** Describes an outsourcing arrangement already in place.

**Interpretation limit:** Does not by itself establish any adverse effect from outsourcing.

## 03 — Expected competition

**Original sentence**

> The Company also expects competition to intensify as competitors imitate the Company’s approach to providing components seamlessly within their offerings or work collaboratively to offer integrated solutions.

**Label:** forward_looking

**Reason:** Explicitly states an expectation about future competition.

**Interpretation limit:** An expectation is not a guaranteed outcome.

## 04 — Expected cyberattack trends

**Original sentence**

> Globally, attacks are expected to continue accelerating in both frequency and sophistication with increasing use by actors of tools and techniques that are designed to circumvent controls, avoid detection, and remove or obfuscate forensic evidence, all of which hinders the Company’s ability to identify, investigate and recover from incidents.

**Primary label:** forward_looking

**Reason:** The main statement predicts future global attack trends.

**Mixed-content note:** The final clause also describes how attack techniques hinder identification, investigation and recovery. A single sentence may contain multiple claims.

**Interpretation limit:** The prediction concerns global attacks; it does not quantify future attacks specifically against Apple.

## 05 — Possible future supply disruptions

**Original sentence**

> Such disruptions could occur in the future.

**Label:** hedged_risk

**Reason:** Expresses a possibility rather than a certainty.

**Context — preceding sentence**

> For example, the global semiconductor industry has in the past experienced high demand and shortages of supply, which adversely affected the Company’s ability to obtain sufficient quantities of components and products on commercially reasonable terms, or at all.

**Context note:** Preserve the preceding sentence to identify what “Such disruptions” refers to. The context describes a past event; the labelled sentence describes possible recurrence.

## 06 — Potentially insufficient insurance

**Original sentence**

> While the Company maintains insurance coverage for certain types of losses, such insurance coverage may be insufficient to cover all losses that may arise.

**Primary label:** hedged_risk

**Reason:** The main risk is that insurance may not cover all potential losses.

**Mixed-content note:** The opening clause states that insurance coverage exists; the remaining clause expresses potential insufficiency.

**Interpretation limit:** Does not establish that an uncovered loss has already occurred.

## Questions for the ontology reconciliation

- Should a mixed sentence produce multiple Claim instances?
- How should a Claim retain the context needed to resolve references such as “Such disruptions”?
- How should expectations and risk qualifiers be preserved?
- How should the subject and scope of a prediction be recorded?
