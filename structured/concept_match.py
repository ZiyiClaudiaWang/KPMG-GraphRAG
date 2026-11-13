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

# Words that say which legal entity filed, not which business it describes.
LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "plc", "lp", "llc", "holdings", "group", "the", "and",
})

# This axis says whether a fact is a segment row or an elimination. Every
# segment fact carries it and no question ever names it, so counting it would
# charge every segment fact the same penalty and bury all of them.
STRUCTURAL_AXES = frozenset({"srt:ConsolidationItemsAxis"})

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


def filer_words(ticker: object, company_name: object = None) -> frozenset:
    """The words that name the filer rather than one of its segments.

    A filer tags its own brand as a dimension member: Nike files
    `nke:NIKEBrandMember`, Microsoft files `msft:MicrosoftCloudMember`. Without
    this, asking "Nike revenue" scores every NIKE Brand slice above the
    company's own figure, because the company's name looks like a segment name.
    """
    words = {str(ticker or "").lower()}
    for word in _TOKEN.findall(str(company_name or "").lower()):
        if word and word not in LEGAL_SUFFIXES:
            words.add(word)
    return frozenset(w for w in words if w)


def score_fact(terms: list[str], concept: object, dimensions: object,
               alias: str | None, filer: frozenset = frozenset()) -> float:
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

    # The filer's own name describes the company, not one of its segments.
    asked = [t for t in terms if t not in filer]

    matched = 0
    unasked = 0
    for entry in _dimension_entries(dimensions):
        if str(entry.get("axis", "")) in STRUCTURAL_AXES:
            continue
        words = concept_words(entry.get("member", ""))
        hits = [t for t in asked if _matches(t, words)]
        # Charge per axis, not once for the whole fact. One named axis used to
        # excuse every other axis, so a three-axis slice of a segment tied with
        # the segment itself and the fact id decided which one was returned.
        if hits:
            matched += len(hits)
        else:
            unasked += 1

    score = base + DIMENSION_BONUS * matched - DIMENSION_PENALTY * unasked
    return round(max(score, 0.0), 6)


# How long a period the question asks about, in days. A statement prints the
# quarter and the year-to-date column side by side, so two facts for the same
# concept end on the same date and differ only in how far back they start.
# Without this the ranker cannot tell them apart and the fact id decides.
_DURATIONS = {
    "three": 91, "quarterly": 91, "quarter": 91,
    "six": 182,
    "nine": 273,
    "twelve": 365, "annual": 365, "year": 365, "yearly": 365,
}

# A filing's quarter is never exactly 91 days, and a 52/53-week year moves by
# a week. Match on the nearest named duration instead of an exact count.
_DURATION_TOLERANCE = 20


def asked_duration_days(question: str) -> int | None:
    """The period length the question names, or None if it names none.

    "the three months ended March 31" is 91 days; "fiscal year 2026" is 365.
    A question that names no period returns None, and the ranker then leaves
    the order alone rather than guessing one.
    """
    words = [w for w in _TOKEN.findall(str(question).lower())]
    for i, word in enumerate(words):
        if word not in _DURATIONS:
            continue
        # "three months" and "nine-month" count; "three segments" does not.
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if word in ("three", "six", "nine", "twelve") and not nxt.startswith("month"):
            continue
        return _DURATIONS[word]
    return None


def duration_days(period_start: object, period_end: object) -> int | None:
    """How long a fact covers, or None for a balance-sheet instant."""
    from datetime import date

    def parsed(value):
        text = str(value or "")[:10]
        try:
            return date(*(int(p) for p in text.split("-")))
        except (TypeError, ValueError):
            return None

    start, end = parsed(period_start), parsed(period_end)
    if start is None or end is None or start == end:
        return None
    return (end - start).days


def duration_matches(asked: int | None, actual: int | None) -> bool:
    """Whether a fact covers the period the question asked for."""
    if asked is None or actual is None:
        return False
    return abs(asked - actual) <= _DURATION_TOLERANCE


# Words that say the answer is negative. A single XBRL concept covers both
# directions -- NetIncomeLoss is the figure whether the company made money or
# lost it -- so "which company reported a net loss" matches every filer's
# profit just as well as the one loss. Nothing in the concept name can tell
# them apart; only the value can.
_NEGATIVE_WORDS = frozenset({
    "loss", "losses", "lost", "negative", "deficit", "shortfall",
    "decline", "declined", "decrease", "decreased", "fell", "drop", "dropped",
})

# And the other way, so "which companies were profitable" does not rank a loss
# first for the same reason.
_POSITIVE_WORDS = frozenset({
    "profit", "profitable", "gain", "gains", "income", "earnings",
    "growth", "grew", "increase", "increased", "rose",
})


def asked_sign(question: str) -> int | None:
    """-1 if the question asks about a negative figure, 1 positive, None neither.

    `income` and `loss` both appear in plenty of neutral questions, so a word
    on its own is weak evidence. This only commits when the question leans one
    way and not the other.
    """
    words = {w for w in _TOKEN.findall(str(question).lower())}
    negative = bool(words & _NEGATIVE_WORDS)
    positive = bool(words & _POSITIVE_WORDS)
    if negative == positive:
        return None
    return -1 if negative else 1


def sign_matches(asked: int | None, value: object) -> bool:
    """Whether a fact's value points the way the question asked."""
    if asked is None or value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if number == 0:
        return False
    return (number < 0) == (asked < 0)
