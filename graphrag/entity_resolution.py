"""Conservative fuzzy entity resolution for subsidiaries and segment names.

This module is deliberately *conservative*: a false merge is usually more damaging
than leaving a mention unresolved.  It implements the rules documented in
``toy_graph/50_subsidiary_pairs_README.md`` without reading the labelled answer key.

The resolver supports three outcomes:

``match``
    There is enough evidence to automatically treat the two names as one entity.
``no_match``
    There is a hard conflict (for example, two different explicit legal forms) or
    the names are clearly dissimilar.
``ambiguous``
    The strings are similar, but the available evidence is not enough to merge
    safely.  A caller can then use filing-defined aliases, jurisdiction metadata,
    or human review.

The design intentionally accepts contextual evidence instead of trying to infer
legal identity from strings alone.  In particular, names such as ``LinkedIn`` and
``LinkedIn Corporation`` cannot be decided reliably from text similarity alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Mapping


MATCH = "match"
NO_MATCH = "no_match"
AMBIGUOUS = "ambiguous"
VALID_DECISIONS = frozenset({MATCH, NO_MATCH, AMBIGUOUS})


@dataclass(frozen=True)
class ResolutionResult:
    """One entity-resolution decision with an explanation for audit/debugging."""

    decision: str
    score: float
    reason: str
    normalized_a: str
    normalized_b: str
    core_a: str
    core_b: str
    legal_form_a: str | None
    legal_form_b: str | None

    @property
    def is_match(self) -> bool:
        return self.decision == MATCH


# Canonical legal-form labels.  The list is intentionally small and explicit:
# add forms only when they occur in the project corpus and have been reviewed.
_LEGAL_FORM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), canonical)
    for pattern, canonical in (
        (r"(?:^| )national association$", "national association"),
        (r"(?:^| )n a$", "national association"),
        (r"(?:^| )unlimited company$", "unlimited company"),
        (r"(?:^| )public limited company$", "plc"),
        (r"(?:^| )plc$", "plc"),
        (r"(?:^| )limited liability company$", "llc"),
        (r"(?:^| )llc$", "llc"),
        (r"(?:^| )incorporated$", "inc"),
        (r"(?:^| )inc$", "inc"),
        (r"(?:^| )corporation$", "corp"),
        (r"(?:^| )corp$", "corp"),
        (r"(?:^| )pte ltd$", "pte ltd"),
        (r"(?:^| )limited$", "ltd"),
        (r"(?:^| )ltd$", "ltd"),
        (r"(?:^| )s a r l$", "sarl"),
        (r"(?:^| )sa$", "sa"),
        (r"(?:^| )ag$", "ag"),
        (r"(?:^| )se$", "se"),
    )
)


def normalize_name(name: str) -> str:
    """Normalize punctuation/case/spacing without dropping semantic words.

    ``N.A.`` and ``National Association`` are normalized later as legal forms so
    that the core name remains visible for diagnostics.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("entity name must be a non-empty string")

    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    # Keep letters/numbers; punctuation in legal abbreviations becomes spaces.
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_legal_form(name: str) -> tuple[str, str | None]:
    """Return ``(core_name, canonical_legal_form)``.

    A missing form is not treated as evidence of identity or non-identity.
    """
    normalized = normalize_name(name)
    for pattern, canonical in _LEGAL_FORM_PATTERNS:
        match = pattern.search(normalized)
        if match:
            core = normalized[: match.start()].strip()
            return core, canonical
    return normalized, None


def _token_jaccard(a: str, b: str) -> float:
    aa, bb = set(a.split()), set(b.split())
    if not aa and not bb:
        return 1.0
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def name_similarity(a: str, b: str) -> float:
    """Return a 0..1 string similarity score on legal-form-stripped names."""
    core_a, _ = split_legal_form(a)
    core_b, _ = split_legal_form(b)
    sequence = SequenceMatcher(None, core_a, core_b).ratio()
    token = _token_jaccard(core_a, core_b)
    return round(max(sequence, token), 6)


def _normalize_alias_map(defined_aliases: Mapping[str, str] | None) -> dict[str, str]:
    """Normalize an alias -> canonical-name mapping supplied from filing context."""
    if not defined_aliases:
        return {}
    out: dict[str, str] = {}
    for alias, canonical in defined_aliases.items():
        if not isinstance(alias, str) or not isinstance(canonical, str):
            raise ValueError("defined_aliases must map strings to strings")
        out[normalize_name(alias)] = normalize_name(canonical)
    return out


def resolve_entity_names(
    name_a: str,
    name_b: str,
    *,
    defined_aliases: Mapping[str, str] | None = None,
    jurisdiction_a: str | None = None,
    jurisdiction_b: str | None = None,
    low_similarity: float = 0.55,
    high_similarity: float = 0.90,
) -> ResolutionResult:
    """Resolve two entity names using hard rules before fuzzy similarity.

    Parameters
    ----------
    defined_aliases:
        Filing-derived alias definitions, for example
        ``{"JPMSE": "J.P. Morgan SE"}``.  These are stronger evidence than a
        fuzzy score and should come from source text, not the labelled test set.
    jurisdiction_a / jurisdiction_b:
        Explicit jurisdiction metadata (for example the Exhibit 21 column).  If
        both are present and disagree, the pair is rejected.  Jurisdiction is
        never guessed from words inside the name.

    The function intentionally returns ``ambiguous`` for same-core names where
    one side has a legal suffix and the other does not, unless the filing defines
    the short form.  This prevents unsafe merges such as a brand/business-line
    mention into a legal entity.
    """
    if not 0 <= low_similarity <= high_similarity <= 1:
        raise ValueError("similarity thresholds must satisfy 0 <= low <= high <= 1")

    normalized_a = normalize_name(name_a)
    normalized_b = normalize_name(name_b)
    core_a, form_a = split_legal_form(name_a)
    core_b, form_b = split_legal_form(name_b)
    score = name_similarity(name_a, name_b)

    aliases = _normalize_alias_map(defined_aliases)
    resolved_a = aliases.get(normalized_a, normalized_a)
    resolved_b = aliases.get(normalized_b, normalized_b)
    if resolved_a == resolved_b and (normalized_a in aliases or normalized_b in aliases):
        return ResolutionResult(
            MATCH, 1.0, "filing-defined alias maps both names to the same entity",
            normalized_a, normalized_b, core_a, core_b, form_a, form_b,
        )

    if jurisdiction_a and jurisdiction_b:
        ja = normalize_name(jurisdiction_a)
        jb = normalize_name(jurisdiction_b)
        if ja != jb:
            return ResolutionResult(
                NO_MATCH, score, "explicit jurisdictions differ",
                normalized_a, normalized_b, core_a, core_b, form_a, form_b,
            )

    # Full normalized equality handles punctuation/case-only variation.
    if normalized_a == normalized_b:
        return ResolutionResult(
            MATCH, 1.0, "names are identical after normalization",
            normalized_a, normalized_b, core_a, core_b, form_a, form_b,
        )

    # Explicit, conflicting legal forms are a hard block.  This intentionally
    # produces the documented false negative for pair 50 until contextual
    # re-domicile evidence is provided.
    if form_a and form_b and form_a != form_b:
        return ResolutionResult(
            NO_MATCH, score, f"conflicting explicit legal forms: {form_a} vs {form_b}",
            normalized_a, normalized_b, core_a, core_b, form_a, form_b,
        )

    if core_a == core_b:
        if form_a and form_b and form_a == form_b:
            return ResolutionResult(
                MATCH, 1.0, "same core name and equivalent legal form",
                normalized_a, normalized_b, core_a, core_b, form_a, form_b,
            )
        # One name may be a filing-defined short form, but a string-only rule
        # cannot know that safely (e.g. LinkedIn vs LinkedIn Corporation).
        return ResolutionResult(
            AMBIGUOUS, 1.0,
            "same core name but one legal form is missing; source context or alias definition required",
            normalized_a, normalized_b, core_a, core_b, form_a, form_b,
        )

    if score < low_similarity:
        return ResolutionResult(
            NO_MATCH, score, "names are below the conservative similarity floor",
            normalized_a, normalized_b, core_a, core_b, form_a, form_b,
        )

    if score >= high_similarity:
        return ResolutionResult(
            AMBIGUOUS, score,
            "high string similarity is not enough to prove legal identity",
            normalized_a, normalized_b, core_a, core_b, form_a, form_b,
        )

    return ResolutionResult(
        NO_MATCH, score, "names are not similar enough for automatic resolution",
        normalized_a, normalized_b, core_a, core_b, form_a, form_b,
    )


def resolve_subsidiary_names(*args, **kwargs) -> ResolutionResult:
    """Named alias for the current Extraction-2 use case."""
    return resolve_entity_names(*args, **kwargs)


def resolve_segment_names(*args, **kwargs) -> ResolutionResult:
    """Same conservative mechanism for segment names.

    Segment-specific aliases/ground truth are not yet in this repository, so
    callers should supply company context and filing-defined aliases when those
    become available.  This function exists now so the interface does not need to
    change later.
    """
    return resolve_entity_names(*args, **kwargs)
