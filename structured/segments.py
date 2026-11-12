"""Work out which segment members a filer actually uses, from the store.

The `segment` filter used to hold three members in a literal dict, all of them
Microsoft's. Asking for a segment belonging to any of the other nine companies
raised "Unknown segment", so the filter was dead for 90% of the corpus.

Nothing had to be hardcoded. The members are in the facts: every segment fact
carries a dimension on `us-gaap:StatementBusinessSegmentsAxis`, and the member
QName on it is the segment. This module reads them out and builds the lookup
the filter needs, so the dictionary grows when the corpus does.

Three things a caller might reasonably type all resolve to the same member:

    "Greater China"                   the label, as a person would say it
    "Greater China Segment"           the label with the filer's own suffix
    "aapl:GreaterChinaSegmentMember"  the QName, when you want to be exact

A label can name more than one member, and that is not an error. NetApp tags
the same segment both `ntap:PublicCloudMember` and
`ntap:PublicCloudSegmentMember`, so "Public Cloud" has to reach both or it
silently drops half the rows.
"""

from __future__ import annotations

import os
import re

import duckdb

# Splits a QName's local part into words, keeping runs of capitals whole.
# `concept_match` has the same pattern but lowercases its output; a segment
# label is shown to a reader, so the filer's casing has to survive.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[0-9]+|[a-z]+")

# The axis filers put their reportable segments on. Geography and product
# axes exist too, but "segment" means this one.
SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"

# Suffixes filers add to a member name. A label keeps them, so Casey's
# `casy:ReportableSegmentMember` reads "Reportable Segment" rather than the
# bare "Reportable". Lookup accepts the short form too, because a caller
# asking for Apple's "Japan" should not have to know the tag says
# "JapanSegment".
SUFFIXES = ("Segment", "Segments")

# How many near misses to offer when a segment is not found.
SUGGESTIONS = 5

_MEMBERS_SQL = f"""
SELECT DISTINCT d.value ->> 'member' AS member
FROM financial_facts_v1, json_each(financial_facts_v1.dimensions) AS d
WHERE d.value ->> 'axis' = '{SEGMENT_AXIS}'
  AND d.value ->> 'member' IS NOT NULL
ORDER BY member
"""

# Rebuilt when the store changes underneath us.
_CACHE_KEY = None
_INDEX: dict[str, list[str]] = {}
_LABELS: dict[str, str] = {}


def _words(qname: object) -> list[str]:
    """`aapl:GreaterChinaSegmentMember` becomes `[Greater, China, Segment]`.

    Casing is kept, so `mu:CDBUMember` stays "CDBU" and `nke:NIKEBrandMember`
    stays "NIKE Brand" rather than being title-cased into nonsense.
    """
    local = str(qname or "").split(":")[-1]
    words = _WORD.findall(local)
    if words and words[-1] == "Member":
        words = words[:-1]
    return words


def label_for(qname: object) -> str:
    """The name a person would use for this member."""
    return " ".join(_words(qname))


def _keys(qname: object) -> list[str]:
    """Every spelling of this member a caller is allowed to type."""
    words = _words(qname)
    if not words:
        return []
    keys = [" ".join(words).lower(), str(qname).lower()]
    if len(words) > 1 and words[-1] in SUFFIXES:
        keys.append(" ".join(words[:-1]).lower())
    return keys


def _cache_key(path: str) -> tuple:
    try:
        stat = os.stat(path)
        return (path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (path, None, None)


def load_dictionary(conn, path: str | None = None) -> dict[str, list[str]]:
    """Read the segment members out of the store, newest build wins.

    Keyed on the store's path and modification time, so rebuilding the corpus
    with a new company in it does not serve the old dictionary.
    """
    global _CACHE_KEY, _INDEX, _LABELS

    path = path or ""
    key = _cache_key(path)
    if key == _CACHE_KEY and _INDEX:
        return _INDEX

    try:
        members = [row[0] for row in conn.execute(_MEMBERS_SQL).fetchall()]
    except duckdb.CatalogException:
        # A store with no facts table has no segments. That is a question the
        # caller can be told the answer to, not a crash.
        members = []

    index: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for member in members:
        labels[label_for(member)] = member
        for key_text in _keys(member):
            bucket = index.setdefault(key_text, [])
            if member not in bucket:
                bucket.append(member)

    _CACHE_KEY, _INDEX, _LABELS = key, index, labels
    return _INDEX


def known_labels() -> list[str]:
    """The segment names in the store, for an error message or a UI."""
    return sorted(_LABELS)


def _near(wanted: str) -> list[str]:
    """Labels worth suggesting when `wanted` matched nothing."""
    asked = set(wanted.lower().split())
    scored = []
    for label in _LABELS:
        shared = asked & set(label.lower().split())
        if shared:
            scored.append((-len(shared), label))
    return [label for _, label in sorted(scored)[:SUGGESTIONS]]


def resolve(conn, wanted: object, path: str | None = None) -> list[str]:
    """The member QNames a caller's segment names.

    Returns a list because one label can cover several tags. Raises
    `ValueError` naming what the store does hold, so a caller who guessed
    wrong learns the vocabulary instead of just being told no.
    """
    index = load_dictionary(conn, path)
    if not isinstance(wanted, str) or not wanted.strip():
        raise ValueError("segment must be a non-empty string")

    members = index.get(" ".join(wanted.split()).lower())
    if members:
        return list(members)

    if not index:
        raise ValueError(
            "no segment facts are loaded, so the segment filter cannot match"
        )
    near = _near(wanted)
    hint = f" Did you mean: {', '.join(near)}?" if near else (
        f" Known segments: {', '.join(known_labels()[:SUGGESTIONS])}..."
    )
    raise ValueError(f"Unknown segment: {wanted}.{hint}")
