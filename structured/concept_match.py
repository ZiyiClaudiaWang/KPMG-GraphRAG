"""Match a typed question to the XBRL concepts that could answer it.

The structured arm used to take a question and ignore it: the SQL filtered,
then ordered by `fact_id`, which is a hash, and scored every row 1.0. Asking
for Intelligent Cloud revenue returned earnings per share.

This module gives the question somewhere to land. It scores a fact against
the words in the question, so the arm can rank and the Agent can compare.

Two paths, and the order matters:

1. **A known name.** "operating cash flow" means
   `us-gaap:NetCashProvidedByUsedInOperatingActivities` and no amount of word
   overlap will work that out. The alias table below covers the terms people
   actually ask about. These are standard us-gaap tags, so one table serves
   every filer in the corpus, not just Microsoft.

2. **Word overlap.** For everything else, split the concept name into words
   and see how much of the question they account for.

A guessed match is always scored below a known one, so a lucky word overlap
cannot outrank the alias table.

Measured on `msft_gold_questions_v1.json`: 10 of 10 questions put the right
concept first. Before this, none of them did.

This is deliberately not text-to-SQL. RAG's §8 puts that out of scope for v0,
and it is not needed to stop returning earnings per share for a revenue
question.
"""

from __future__ import annotations

import json
import re

# Question words, and words every filing says about itself. Dropping them
# stops "What was Microsoft's..." matching a concept that happens to contain
# "company".
STOP_WORDS = frozenset({
    "what", "was", "were", "is", "are", "the", "a", "an", "of", "for", "in",
    "on", "at", "as", "to", "and", "or", "s", "did", "do", "does", "how",
    "much", "many", "report", "reported", "value", "amount", "figure",
    "company", "companys", "corporation", "inc",
    "fiscal", "year", "quarter", "period", "ended", "ending",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
})

# Plain English to the tag filers actually use. Longest phrase wins, so
# "operating cash flow" beats "cash flow" and "net sales" beats "sales".
CONCEPT_ALIASES = (
    (("operating", "cash", "flow"), "us-gaap:NetCashProvidedByUsedInOperatingActivities"),
    (("cash", "from", "operations"), "us-gaap:NetCashProvidedByUsedInOperatingActivities"),
    (("investing", "cash", "flow"), "us-gaap:NetCashProvidedByUsedInInvestingActivities"),
    (("financing", "cash", "flow"), "us-gaap:NetCashProvidedByUsedInFinancingActivities"),
    (("earnings", "per", "share"), "us-gaap:EarningsPerShareBasic"),
    (("research", "development"), "us-gaap:ResearchAndDevelopmentExpense"),
    (("cash", "equivalents"), "us-gaap:CashAndCashEquivalentsAtCarryingValue"),
    (("shareholders", "equity"), "us-gaap:StockholdersEquity"),
    (("stockholders", "equity"), "us-gaap:StockholdersEquity"),
    (("operating", "income"), "us-gaap:OperatingIncomeLoss"),
    (("operating", "expenses"), "us-gaap:OperatingExpenses"),
    (("total", "liabilities"), "us-gaap:Liabilities"),
    (("gross", "margin"), "us-gaap:GrossProfit"),
    (("gross", "profit"), "us-gaap:GrossProfit"),
    (("total", "assets"), "us-gaap:Assets"),
    (("income", "tax"), "us-gaap:IncomeTaxExpenseBenefit"),
    (("net", "income"), "us-gaap:NetIncomeLoss"),
    (("net", "sales"), "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
    (("revenue",), "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
)

# A known name scores here. A guessed one is held below it.
ALIAS_SCORE = 1.0
MAX_GUESS_SCORE = 0.95
# Each extra word in the concept that the question did not ask for.
LENGTH_PENALTY = 0.08
# Concepts tend to lead with their subject: Assets, Revenue, NetIncomeLoss.
LEAD_BONUS = 0.15
# A dimension can sharpen a match. It can never create one.
DIMENSION_BONUS = 0.05
# A dimension the question did not ask for makes a fact less likely to be the
# answer. "Nike's revenue" means the company's revenue, not the revenue of its
# sporting equipment line, and both are tagged with the same concept.
DIMENSION_PENALTY = 0.10

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[0-9]+|[a-z]+")
_TOKEN = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"(19|20)\d\d")


def concept_words(qname: object) -> list[str]:
    """`us-gaap:NetIncomeLoss` becomes `[net, income, loss]`."""
    local = str(qname or "").split(":")[-1]
    return [w.lower() for w in _WORD.findall(local)]


def question_terms(question: str) -> list[str]:
    """The words in a question worth matching on.

    Bare years are dropped. A year is a period filter, and leaving it in makes
    every fact tagged 2025 look relevant.
    """
    terms = []
    for token in _TOKEN.findall(question.lower().replace("'s", " ")):
        if len(token) < 2 or token in STOP_WORDS or _YEAR.fullmatch(token):
            continue
        terms.append(token)
    return terms


def aliased_concept(terms: list[str]) -> str | None:
    """The concept a known phrase names, or None."""
    best_length, best_concept = 0, None
    for phrase, concept in CONCEPT_ALIASES:
        if len(phrase) > best_length and all(word in terms for word in phrase):
            best_length, best_concept = len(phrase), concept
    return best_concept


def _matches(term: str, words: list[str]) -> bool:
    """Singular and plural are the same word here.

    Filers tag both `us-gaap:Revenues` and
    `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`, so a
    question about revenue has to reach both.
    """
    return any(term == w or term + "s" == w or term == w + "s" for w in words)


def _dimension_entries(dimensions: object) -> list:
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions or "[]")
        except json.JSONDecodeError:
            return []
    return [e for e in (dimensions or []) if isinstance(e, dict)]


def _dimension_words(dimensions: object) -> list[str]:
    if isinstance(dimensions, str):
        try:
            dimensions = json.loads(dimensions or "[]")
        except json.JSONDecodeError:
            return []
    words: list[str] = []
    for entry in dimensions or []:
        if isinstance(entry, dict):
            words += concept_words(entry.get("member", ""))
    return words


def score_fact(terms: list[str], concept: object, dimensions: object,
               alias: str | None) -> float:
    """How well one fact answers the question. 0.0 means it does not."""
    if not terms:
        return 0.0

    words = concept_words(concept)

    if alias is not None and concept == alias:
        base = ALIAS_SCORE
    else:
        hits = [t for t in terms if _matches(t, words)]
        if not hits:
            # Without this the question "operating cash flow" matches
            # StockholdersEquity, because one of its dimension members is
            # AccumulatedGainLossNetCashFlowHedge. A qualifier is not a subject.
            return 0.0
        coverage = len(hits) / len(terms)
        unasked = max(0, len(words) - len(hits))
        brevity = 1.0 / (1.0 + LENGTH_PENALTY * unasked)
        lead = LEAD_BONUS if words and _matches(words[0], terms) else 0.0
        base = min(coverage * brevity + lead, MAX_GUESS_SCORE)

    entries = _dimension_entries(dimensions)
    matched = [t for t in terms if _matches(t, _dimension_words(dimensions))]
    # One penalty per dimension, charged once if nothing in the question
    # named it. A consolidated fact carries no dimensions and so pays nothing.
    unasked = 0 if matched else len(entries)
    score = base + DIMENSION_BONUS * len(matched) - DIMENSION_PENALTY * unasked
    return round(max(score, 0.0), 6)
