"""Normalize jurisdictions and build stable entity keys for cross-year matching."""
from __future__ import annotations

import re
import unicodedata

US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "district of columbia", "puerto rico",
}
COUNTRY_ALIASES = {
    "u.s.": "United States", "us": "United States", "usa": "United States", "u.s.a.": "United States",
    "united states": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "england": "United Kingdom",
    "england and wales": "United Kingdom", "scotland": "United Kingdom",
    "korea": "South Korea", "republic of korea": "South Korea", "south korea": "South Korea",
    "prc": "China", "people's republic of china": "China", "china": "China",
    "the netherlands": "Netherlands", "holland": "Netherlands",
    "hong kong sar": "Hong Kong", "hong kong": "Hong Kong",
}
COUNTRIES = {c.lower(): c for c in """Argentina Australia Austria Belgium Bermuda Brazil Bulgaria
Canada Cayman_Islands Chile China Colombia Costa_Rica Croatia Cyprus Czech_Republic Denmark Egypt
Estonia Finland France Germany Gibraltar Greece Guernsey Hong_Kong Hungary India Indonesia Ireland
Isle_of_Man Israel Italy Japan Jersey Kenya Latvia Lithuania Luxembourg Macau Malaysia Malta
Mauritius Mexico Morocco Netherlands New_Zealand Nigeria Norway Pakistan Panama Peru Philippines
Poland Portugal Qatar Romania Russia Saudi_Arabia Serbia Singapore Slovakia Slovenia South_Africa
South_Korea Spain Sweden Switzerland Taiwan Thailand Turkey Ukraine United_Arab_Emirates
United_Kingdom United_States Uruguay Vietnam British_Virgin_Islands Bahamas Barbados Curacao""".split()}
# Multi-word names are written with "_" so split() keeps them whole; restore the spaces here.
COUNTRIES = {k.replace("_", " "): v.replace("_", " ") for k, v in COUNTRIES.items()}

LEGAL_SUFFIXES = [
    "unlimited company", "limited liability company", "private limited", "pty limited", "pty ltd",
    "pte ltd", "pte. ltd.", "s.a. de c.v.", "s. de r.l. de c.v.", "co., ltd.", "co. ltd.",
    "incorporated", "corporation", "company", "limited", "l.l.c.", "llc", "l.p.", "lp", "ltd",
    "inc", "corp", "co", "plc", "gmbh", "b.v.", "bv", "s.a.", "sa", "ag", "k.k.", "kk", "s.r.l.",
    "sarl", "s.a.r.l.", "ab", "as", "oy", "nv", "n.v.", "spa", "s.p.a.", "pte", "private",
]
_SUFFIX_RE = re.compile(
    r"(?:[\s,]+(?:" + "|".join(re.escape(s) for s in sorted(LEGAL_SUFFIXES, key=len, reverse=True))
    + r")\.?)+$",
    re.I,
)


def normalize_jurisdiction(raw: str) -> tuple[str, str]:
    """Return (country, state_or_region). Unknown values pass through as the country."""
    if not raw:
        return "", ""
    parts = [p.strip(" .") for p in raw.split(",") if p.strip(" .")]
    low = [p.lower() for p in parts]
    # "Delaware, U.S." / "Nevada" / "State of Delaware"
    for i, p in enumerate(low):
        p2 = re.sub(r"^state of\s+", "", p)
        if p2 in US_STATES:
            return "United States", parts[i].replace("State of ", "").strip()
    whole = raw.strip(" .").lower()
    if whole in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[whole], ""
    if whole in COUNTRIES:
        return COUNTRIES[whole], ""
    last = low[-1] if low else whole
    if last in COUNTRY_ALIASES or last + "." in COUNTRY_ALIASES:
        country = COUNTRY_ALIASES.get(last) or COUNTRY_ALIASES[last + "."]
        return country, ", ".join(parts[:-1])
    if last in COUNTRIES:  # "British Columbia, Canada"
        return COUNTRIES[last], ", ".join(parts[:-1])
    return raw.strip(), ""


def entity_key(name: str) -> str:
    """Name with punctuation, case and legal-form words removed.

    'Microsoft Ireland Research' and 'Microsoft Ireland Research Unlimited Company'
    get the same key, so the pipeline can tell a name change from a new entity.
    """
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower().replace("&", " and ")
    prev = None
    while prev != n:
        prev = n
        n = _SUFFIX_RE.sub("", n).strip(" ,.")
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()
