"""Item 1A only: the rule that proposes risk lead sentences, and the risk factor summary.

rule_heading (unit field): "high" / "medium" when the rules propose a paragraph as the sentence
that opens and sums up a risk (rule_lead_strength). Since step 3b, these are used as lead
sentences only where the HTML cannot be read (segment.lead_rule, link_leads.uses_rule). The field
keeps its old name for now; risk categories (level-1 headings) are in headings.py.

The risk factor summary some filings open with (MU "Risk Factor Summary", TTWO "Summary of Risk
Factors") repeats the risks described below it in short form. Its units are labelled as usual and
carry risk_summary = true, so later steps can tell them apart.
"""

from __future__ import annotations

import re

from .headings import is_category, is_heading_like
from .lists import is_bullet
from .sentences import TERMINAL, sentence_count

RISK_SUMMARY = re.compile(r"^(?:risk factors? summary|summary of risk factors?)$", re.IGNORECASE)
# Body sentences often open by pointing back; lead sentences never do.
POINTS_BACK = re.compile(
    r"^(such|this|these|that|those|although|while|if|in addition|additionally|also|however|"
    r"for all|any of|as a result|accordingly|further|moreover)\b",
    re.IGNORECASE,
)


def rule_lead_strength(paragraphs: list[dict], i: int, max_chars: int) -> str | None:
    """Return 'high' or 'medium' if paragraph i looks like a risk lead sentence, else None.

    Risk leads are single sentences (Apple's end with a period) followed by
    their body: a paragraph of two or more sentences (high), or a sub-heading as
    in Microsoft's 1A (medium). Single-sentence body paragraphs are followed by
    another single sentence or by a category heading, so they are not matched.
    """
    text = paragraphs[i]["text"]
    if (len(text) > max_chars or is_bullet(text) or text.endswith(":") or POINTS_BACK.match(text)
            or not TERMINAL.search(text) or sentence_count(text) != 1):
        return None
    if i + 1 >= len(paragraphs):
        return None
    nxt = paragraphs[i + 1]["text"]
    if is_bullet(nxt):
        return None
    if is_heading_like(nxt):
        return None if is_category(nxt) else "medium"
    return "high" if sentence_count(nxt) >= 2 else None


def mark_risk_summary(units: list[dict]) -> None:
    """Flag the risk factor summary (risk_summary = true): it repeats the risks described after it.

    The summary runs from its heading to the first heading that appears for the second time
    (where the full text starts again), at any level: with heading levels (step 3b) the
    categories listed in the summary sit under its heading at level 2, and are level 1 in the
    full text (MU, TTWO). Without such a repeat nothing is marked.
    """
    start = next((k for k, u in enumerate(units)
                  if u["role"] == "heading" and RISK_SUMMARY.match(u["text"])), None)
    if start is None:
        return
    seen, end = set(), None
    for k in range(start + 1, len(units)):
        if units[k]["role"] != "heading":
            continue
        key = units[k]["text"].lower()
        if key in seen:
            end = k
            break
        seen.add(key)
    if end is None:
        return
    for u in units[start:end]:
        u["risk_summary"] = True
