"""Entity mentions: segments and subsidiaries named in a Claim (Claudia's module, P1 of #91).

The LLM returns mentions in step 5 (instructions are in prompts/label_v2.txt); step 7b checks
them against the sentence and matches them to the entity catalog (data/entity_catalog.json,
built by step 1c, src/entity_catalog.py). Catalog IDs are assigned by code only, and nothing
here writes to the graph.

Matching (docs/claim-entity-linking.md, P1):
  - Names are compared on match_key(): accents, case, punctuation, Unicode dashes, a leading
    "the"/"our", the legal form ("Inc.", "LLC") and a trailing "segment(s)" are ignored. So
    "Rockstar Games" matches Exhibit 21's "Rockstar Games, Inc.", and "Medical‑Surgical
    Solutions segment" (non-breaking hyphen) matches "Medical-Surgical Solutions".
  - A catalog hit is the type evidence: the catalog decides whether a name is a subsidiary or
    a segment, whatever type the model gave (unknown included). The cue-word rule ("segment",
    "subsidiary", "wholly owned" in the quote) now applies only to mentions with no catalog
    hit, and to guarded segment names that are also places or common words
    (config/entity_linking.yaml guard_terms: "Europe", "Services").
  - The registrant's own name is never an entity (registrant_reference).
  - Catalog first: a dictionary pass finds every catalog name of the filing in the sentence;
    then the model's mentions are matched and merged with it (annotate_mentions). The model's
    own mentions (generic references, counterparties) are kept whatever the catalog says.
    Word boundaries are required, so "Lord" never matches inside "landlord", and a name must
    start with a capital or a digit, so "public cloud providers" is not NetApp's Public Cloud.

match_status, for a mention with a valid source:
  generic_reference              "our international subsidiaries": no id
  registrant_reference           the company itself
  matched                        one hit on a reviewed catalog entry
  matched_auto                   one exact-key hit on an Exhibit 21 or XBRL entry, listed in
                                 the filing's own reference 10-K (team policy, #91 P1.5)
  candidate_match_needs_review   one hit on any other entry, or on a subsidiary listed only
                                 in the 10-K before the reference one (prior_year_only)
  ambiguous_catalog_match        several entries of one type share the key
  needs_type_evidence_review     a guarded segment name without "segment" in the sentence, or
                                 a named mention with no hit and no cue word
  needs_review                   no hit, model type unknown or kind uncertain
  needs_catalog / no_catalog_match   named, cue word present, but nothing to match against
A name that is both a subsidiary and a segment (Nike's Converse) links to both.
"""
import functools
import re

from .ex21.normalize import entity_key

VERSION = "entity_mentions_v2"
SCHEMA = {
    "type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["mention_text", "entity_type", "reference_kind", "evidence_quote"],
        "properties": {
            "mention_text": {"type": "string"},
            "entity_type": {"type": "string", "enum": ["segment", "subsidiary", "unknown"]},
            "reference_kind": {"type": "string", "enum": ["named", "generic", "uncertain"]},
            "evidence_quote": {"type": "string"},
        },
    },
}
ENTITY_TYPES = ("segment", "subsidiary")
CATALOG_TYPES = ENTITY_TYPES + ("registrant",)
AUTO_SOURCES = ("exhibit_21", "xbrl")
LINKED = ("matched", "matched_auto")
SEGMENT_CUE = re.compile(r"\bsegments?\b", re.I)
SUBSIDIARY_CUE = re.compile(r"\bsubsidiar(?:y|ies)\b|\bwholly[- ]owned\b", re.I)
# Hyphens and dashes (Pd), the soft hyphen, and trademark signs read as a space.
_DASHES = re.compile(r"[­‐-―−﹘﹣－®™©]")
_LEAD = re.compile(r"^(?:the|our)\s+")
_TRAIL = re.compile(r"(?:\s+(?:reportable|operating|business))?\s+segments?$")


def normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def match_key(name: str) -> str:
    """The key a mention and a catalog name are compared on (see the module docstring)."""
    key = entity_key(_DASHES.sub(" ", name or ""))
    prev = None
    while prev != key:
        prev = key
        key = _TRAIL.sub("", _LEAD.sub("", key)).strip()
    return key


@functools.lru_cache(maxsize=1)
def settings() -> dict:
    """config/entity_linking.yaml, with guard terms as match keys."""
    from ..common import load_config     # imported here so the module stays importable alone
    try:
        cfg = load_config("entity_linking") or {}
    except FileNotFoundError:
        cfg = {}
    return {"guard": frozenset(match_key(t) for t in cfg.get("guard_terms") or []),
            "min_key": int(cfg.get("dictionary_min_key_length") or 4),
            "skip": frozenset(match_key(t) for t in cfg.get("dictionary_skip") or []),
            "short_ok": frozenset(match_key(t) for t in cfg.get("dictionary_short_names") or []),
            "registrant_names": cfg.get("registrant_names") or {},
            "context_after": re.compile(r"^(?:['\u2019]s)?\s+(?:" + "|".join(
                cfg.get("guard_context_after") or ["segments?"]) + r")\b", re.I)}


def validate_catalog(catalog):
    if not isinstance(catalog, list):
        raise ValueError("Entity catalog must be a list.")
    seen = set()
    for row in catalog:
        required = {"entity_id", "entity_type", "issuer_cik", "name", "aliases", "accessions", "review_status"}
        if not isinstance(row, dict) or not required <= set(row):
            raise ValueError("Incomplete catalog row.")
        if row["entity_type"] not in CATALOG_TYPES or row["review_status"] not in ("reviewed", "needs_review"):
            raise ValueError("Invalid catalog type/status.")
        if not isinstance(row["issuer_cik"], str) or not re.fullmatch(r"\d{10}", row["issuer_cik"]):
            raise ValueError("Catalog issuer CIK must be ten digits.")
        if not all(isinstance(row[k], str) and row[k].strip() for k in ("entity_id", "name")):
            raise ValueError("Catalog ID and name must be nonempty.")
        for key in ("aliases", "accessions"):
            if not isinstance(row[key], list) or not all(isinstance(v, str) and v.strip() for v in row[key]):
                raise ValueError("Catalog aliases/accessions must be lists of nonempty strings.")
        if not row["accessions"]:
            raise ValueError("Catalog entries require an explicit filing scope.")
        identity = (row["issuer_cik"], row["entity_type"], row["entity_id"])
        if identity in seen:
            raise ValueError("Duplicate catalog identity.")
        seen.add(identity)


def entry_keys(row: dict) -> set[str]:
    return {k for k in (match_key(n) for n in [row["name"]] + row["aliases"]) if k}


class _Scope:
    """The catalog entries one filing may link to, indexed by match key."""

    def __init__(self, catalog, issuer_cik, accession):
        self.by_key: dict[str, list[dict]] = {}
        self.by_id: dict[str, list[dict]] = {}
        self.registrant: set[str] = set()
        for row in catalog:
            if row["issuer_cik"] != issuer_cik or accession not in row["accessions"]:
                continue
            keys = entry_keys(row)
            if row["entity_type"] == "registrant":
                self.registrant |= keys
                continue
            for k in keys:
                self.by_key.setdefault(k, []).append(row)
            self.by_id.setdefault(row["entity_id"], []).append(row)
        for k in self.registrant:           # the company itself is never one of its entities
            self.by_key.pop(k, None)
        self.types = {row["entity_type"] for rows in self.by_key.values() for row in rows}


_SCOPES: dict[tuple, tuple[list, _Scope]] = {}


def scope(catalog, issuer_cik, accession) -> _Scope:
    """A run passes the same catalog list to every unit: index it once per filing. The cache
    holds the list itself, so its id cannot be reused by another list while cached."""
    key = (id(catalog), issuer_cik, accession)
    hit = _SCOPES.get(key)
    if hit is None or hit[0] is not catalog:
        if len(_SCOPES) > 256:
            _SCOPES.clear()
        validate_catalog(catalog)
        hit = _SCOPES[key] = (catalog, _Scope(catalog, issuer_cik, accession))
    return hit[1]


def _status(row: dict, accession: str) -> str:
    if row["review_status"] == "reviewed":
        return "matched"
    if row.get("source") in AUTO_SOURCES and accession not in (row.get("prior_year_only_accessions") or []):
        return "matched_auto"
    return "candidate_match_needs_review"


def _segment_context(text: str, wording: str, start: int | None = None) -> bool:
    """A guarded segment name reads as the segment: "segment(s)" in the sentence, or the name
    followed by guard_context_after words."""
    if SEGMENT_CUE.search(text):
        return True
    starts = [start] if start is not None else [i.start() for i in re.finditer(re.escape(wording), text)]
    after = settings()["context_after"]
    return any(after.search(text[s + len(wording):]) for s in starts)


def _link(m: dict, hits: list[dict], text: str, accession: str, related: dict) -> None:
    """Set match_status and ids on a mention that hit the catalog."""
    by_type: dict[str, list[dict]] = {}
    for row in hits:
        by_type.setdefault(row["entity_type"], []).append(row)
    m["candidate_entity_ids"] = [r["entity_id"] for r in hits]
    m["model_entity_type"] = m.get("entity_type")
    if any(len(rows) > 1 for rows in by_type.values()):
        m["match_status"] = "ambiguous_catalog_match"
        return
    rows = [rs[0] for rs in by_type.values()]
    guard = settings()["guard"]
    if all(r["entity_type"] == "segment" for r in rows) and match_key(m["mention_text"]) in guard \
            and not _segment_context(text, m["mention_text"], m.get("char_start")):
        m["entity_type"] = "segment"
        m["match_status"] = "needs_type_evidence_review"
        return
    statuses = [_status(r, accession) for r in rows]
    # The weakest status of the hits is the mention's status.
    order = ["candidate_match_needs_review", "matched_auto", "matched"]
    m["match_status"] = min(statuses, key=order.index)
    primary = next((r for r in rows if r["entity_type"] == m["model_entity_type"]), rows[0])
    m["entity_type"] = primary["entity_type"]
    if len(rows) > 1:
        m["also_entity_types"] = sorted(r["entity_type"] for r in rows if r is not primary)
    if m["match_status"] in LINKED:
        m["matched_entity_id"] = primary["entity_id"]
        for r, s in zip(rows, statuses):
            if s in LINKED and r["entity_id"] not in related[r["entity_type"]]:
                related[r["entity_type"]].append(r["entity_id"])


def _model_mention(mention, text, sc, accession, related, flags):
    m = dict(mention)
    m.setdefault("origin", "model")
    m["matched_entity_id"] = None
    m["candidate_entity_ids"] = []
    wording, evidence = m["mention_text"], m["evidence_quote"]
    if not (wording.strip() and evidence.strip() and wording in evidence and evidence in text):
        m["match_status"] = "invalid_source_mention"
        flags.append("entity_mention_does_not_match_target_text")
        return m
    if m["reference_kind"] == "generic":
        m["match_status"] = "generic_reference"
        return m
    key = match_key(wording)
    if not _named(wording):
        key = ""                                # "public cloud", "digital surgery": not a name
    if key and key in sc.registrant:
        m["match_status"] = "registrant_reference"
        return m
    hits = sc.by_key.get(key, [])
    if hits:
        _link(m, hits, text, accession, related)
        return m
    if m["reference_kind"] == "uncertain" or m["entity_type"] == "unknown":
        m["match_status"] = "needs_review"
        return m
    kind = m["entity_type"]
    cue = SEGMENT_CUE if kind == "segment" else SUBSIDIARY_CUE
    if not cue.search(evidence):
        m["match_status"] = "needs_type_evidence_review"
    elif kind not in sc.types:
        m["match_status"] = "needs_catalog"
    else:
        m["match_status"] = "no_catalog_match"
    return m


def _named(wording: str) -> bool:
    """A name starts with a capital or a digit: "Hybrid Cloud" is NetApp's segment, "hybrid
    cloud" and "public cloud providers" are ordinary words. Words in all lower case never
    match the catalog."""
    first = next((c for c in wording if c.isalnum()), "")
    return first.isupper() or first.isdigit()


_SEP = r"[\s\-­‐-―−.,'’]*"


@functools.lru_cache(maxsize=20000)
def _key_pattern(key: str) -> re.Pattern:
    """A regex for the words of a match key in running text: any case, punctuation or dashes
    between the words, "and" also as "&", and whole words only."""
    parts = []
    for word in key.split():
        parts.append(r"(?:and|&)" if word == "and" else re.escape(word))
    return re.compile(r"(?<![A-Za-z0-9])" + _SEP.join(parts) + r"(?![A-Za-z0-9])", re.I)


def dictionary_hits(text: str, sc: _Scope) -> list[tuple[int, int, str]]:
    """(start, end, key) of every catalog key in the text, longest first, never overlapping."""
    cfg = settings()
    found = []
    for key in sc.by_key:
        if (len(key) < cfg["min_key"] and key not in cfg["short_ok"]) or key in cfg["skip"]:
            continue
        # cheap prefilter on the first word before running the regex
        if key.split()[0] not in text.lower():
            continue
        short = len(key) < cfg["min_key"]
        for hit in _key_pattern(key).finditer(text):
            # A short name right after a capitalised word is part of a longer name:
            # "NBA 2K" and "WWE 2K" are game series, not Take-Two's 2K label.
            if short and re.search(r"[A-Z][A-Za-z]*\s+$", text[:hit.start()]):
                continue
            if _named(hit.group()):
                found.append((hit.start(), hit.end(), key))
    found.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept = []
    for h in found:
        if all(h[1] <= k[0] or h[0] >= k[1] for k in kept):
            kept.append(h)
    return sorted(kept)


def annotate_mentions(text, mentions, *, issuer_cik, accession, catalog, dictionary=True):
    """Catalog first, then the model's mentions.

    1. Dictionary pass: every catalog name of this filing found in the text (code only).
    2. The model's mentions are checked and matched as before. One that names an entity the
       dictionary already found replaces that dictionary mention (the model's type, kind and
       evidence are kept) and is marked found_by ["dictionary", "model"]. The rest stay as the
       model gave them, generic references and counterparties included (found_by ["model"]).
    3. Dictionary hits no model mention covers are added (found_by ["dictionary"]).
    origin is "model" or "dictionary": who produced the mention record.
    """
    sc = scope(catalog, issuer_cik, accession)
    flags, related = [], {"segment": [], "subsidiary": []}
    found = []                                  # dictionary mentions, before the model's
    if dictionary and sc.by_key:
        scratch = {"segment": [], "subsidiary": []}
        for start, end, key in dictionary_hits(text, sc):
            rows = sc.by_key[key]
            m = {"mention_text": text[start:end], "entity_type": rows[0]["entity_type"],
                 "reference_kind": "named", "evidence_quote": text, "origin": "dictionary",
                 "char_start": start, "char_end": end, "matched_entity_id": None}
            _link(m, rows, text, accession, scratch)
            m["model_entity_type"] = None
            found.append(m)
    output = []
    for mention in mentions:
        m = _model_mention(mention, text, sc, accession, related, flags)
        ids = set(m["candidate_entity_ids"])
        also = [d for d in found if ids and ids & set(d["candidate_entity_ids"])]
        m["found_by"] = ["dictionary", "model"] if also else ["model"]
        for d in also:
            found.remove(d)
            m.setdefault("char_start", d["char_start"])
            m.setdefault("char_end", d["char_end"])
        output.append(m)
    for d in found:
        d["found_by"] = ["dictionary"]
        if d["match_status"] in LINKED:
            rows = [r for i in d["candidate_entity_ids"] for r in sc.by_id.get(i, [])]
            for r in rows:
                if _status(r, accession) in LINKED and r["entity_id"] not in related[r["entity_type"]]:
                    related[r["entity_type"]].append(r["entity_id"])
        output.append(d)
    return {"entity_mentions": output, "related_segment_ids": related["segment"],
            "related_subsidiary_ids": related["subsidiary"], "entity_review_flags": flags,
            "entity_annotation_version": VERSION, "graph_relations_created": 0}
