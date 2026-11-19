"""Jurisdictions and identity keys for Exhibit 21 rows.

Library code: imports nothing from the pipeline.

Jurisdiction: normalize_jurisdiction() maps the text as filed to (country, state_or_region).
A value it cannot map gives country "" (the step flags it in subsidiary_qa.csv); the text as
filed is always kept as jurisdiction_raw.

Three name keys, for three jobs:
  normalised_name  case, accents and punctuation removed, legal form KEPT. Builds sub_id, so
                   "Apple Asia LLC" and "Apple Asia Limited" stay two subsidiaries.
  entity_key       legal-form words also removed. Stored on each Subsidiary node: when we load
                   several years, a subsidiary that disappears and one that appears under the
                   same parent with the same entity_key is likely a rename, not a new entity.
  is_registrant    normalised_name without a leading "the", legal forms abbreviated as EDGAR
                   does: spots a row that is the parent itself.
"""
from __future__ import annotations

import re
import unicodedata
import uuid

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
# Canadian provinces and territories: country = Canada, the province goes to state_or_region.
CA_PROVINCES = {
    "alberta": "Alberta", "british columbia": "British Columbia", "manitoba": "Manitoba",
    "new brunswick": "New Brunswick", "newfoundland and labrador": "Newfoundland and Labrador",
    "newfoundland": "Newfoundland and Labrador", "nova scotia": "Nova Scotia",
    "ontario": "Ontario", "prince edward island": "Prince Edward Island", "quebec": "Quebec",
    "québec": "Quebec", "saskatchewan": "Saskatchewan", "northwest territories": "Northwest Territories",
    "nunavut": "Nunavut", "yukon": "Yukon",
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
    # B.W.I. = British West Indies, an old suffix on Caribbean jurisdictions
    "cayman islands, b.w.i.": "Cayman Islands", "cayman islands b.w.i.": "Cayman Islands",
    "cayman islands, bwi": "Cayman Islands", "grand cayman": "Cayman Islands",
    "bvi": "British Virgin Islands", "virgin islands (british)": "British Virgin Islands",
    "côte d'ivoire": "Ivory Coast", "cote d'ivoire": "Ivory Coast", "czechia": "Czech Republic",
    "türkiye": "Turkey", "turkiye": "Turkey", "russian federation": "Russia", "viet nam": "Vietnam",
    "macao": "Macau", "uae": "United Arab Emirates", "u.a.e.": "United Arab Emirates",
}
COUNTRIES = {c.lower(): c for c in """Argentina Australia Austria Belgium Bermuda Brazil Bulgaria
Canada Cayman_Islands Chile China Colombia Costa_Rica Croatia Cyprus Czech_Republic Denmark Egypt
Estonia Finland France Germany Gibraltar Greece Guernsey Hong_Kong Hungary India Indonesia Ireland
Isle_of_Man Israel Italy Japan Jersey Kenya Latvia Lithuania Luxembourg Macau Malaysia Malta
Mauritius Mexico Morocco Netherlands New_Zealand Nigeria Norway Pakistan Panama Peru Philippines
Poland Portugal Qatar Romania Russia Saudi_Arabia Serbia Singapore Slovakia Slovenia South_Africa
South_Korea Spain Sweden Switzerland Taiwan Thailand Turkey Ukraine United_Arab_Emirates
United_Kingdom United_States Uruguay Vietnam British_Virgin_Islands Bahamas Barbados Curacao""".split()}
# The rest of the world's countries and the territories that show up as places of
# incorporation, so a real country is never left unmapped.
COUNTRIES.update({c.lower(): c for c in """Afghanistan Albania Algeria Andorra Angola
Antigua_and_Barbuda Armenia Aruba Azerbaijan Bahrain Bangladesh Belarus Belize Benin Bhutan
Bolivia Bosnia_and_Herzegovina Botswana Brunei Burkina_Faso Burundi Cambodia Cameroon Cape_Verde
Central_African_Republic Chad Comoros Congo Cuba Djibouti Dominica Dominican_Republic Ecuador
El_Salvador Equatorial_Guinea Eritrea Eswatini Ethiopia Fiji Gabon Gambia Georgia Ghana Grenada
Guatemala Guinea Guyana Haiti Honduras Iceland Iran Iraq Ivory_Coast Jamaica Jordan Kazakhstan
Kiribati Kosovo Kuwait Kyrgyzstan Laos Lebanon Lesotho Liberia Libya Liechtenstein Madagascar
Malawi Maldives Mali Marshall_Islands Mauritania Micronesia Moldova Monaco Mongolia Montenegro
Mozambique Myanmar Namibia Nauru Nepal New_Caledonia Nicaragua Niger North_Macedonia Oman Palau
Papua_New_Guinea Paraguay Puerto_Rico Rwanda Saint_Kitts_and_Nevis Saint_Lucia
Saint_Vincent_and_the_Grenadines Samoa San_Marino Senegal Seychelles Sierra_Leone
Solomon_Islands Somalia Sri_Lanka Sudan Suriname Syria Tajikistan Tanzania Togo Tonga
Trinidad_and_Tobago Tunisia Turkmenistan Tuvalu Uganda Uzbekistan Vanuatu Venezuela Yemen Zambia
Zimbabwe Anguilla Faroe_Islands French_Polynesia Greenland Guam Montserrat Reunion
Turks_and_Caicos_Islands US_Virgin_Islands Sint_Maarten Bonaire Martinique Guadeloupe
French_Guiana Mayotte Macedonia""".split()})
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
    """Return (country, state_or_region); ("", "") when the value cannot be mapped."""
    if not raw:
        return "", ""
    parts = [p.strip(" .") for p in raw.split(",") if p.strip(" .")]
    low = [p.lower() for p in parts]
    # "Delaware, U.S." / "Nevada" / "State of Delaware"
    for i, p in enumerate(low):
        p2 = re.sub(r"^state of\s+", "", p)
        if p2 in US_STATES:
            return "United States", parts[i].replace("State of ", "").strip()
    # "Ontario" / "Province of Quebec" / "British Columbia, Canada"
    for p in low:
        p2 = re.sub(r"^province of\s+", "", p)
        if p2 in CA_PROVINCES:
            return "Canada", CA_PROVINCES[p2]
    whole = raw.strip(" .").lower()
    if whole in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[whole], ""
    if whole in COUNTRIES:
        return COUNTRIES[whole], ""
    last = low[-1] if low else whole
    if last in COUNTRY_ALIASES or last + "." in COUNTRY_ALIASES:
        country = COUNTRY_ALIASES.get(last) or COUNTRY_ALIASES[last + "."]
        return country, ", ".join(parts[:-1])
    if last in COUNTRIES:
        return COUNTRIES[last], ", ".join(parts[:-1])
    if low and (low[0] in COUNTRY_ALIASES or low[0] in COUNTRIES):   # "Cayman Islands, B.W.I."
        return COUNTRY_ALIASES.get(low[0]) or COUNTRIES[low[0]], ""
    return "", ""


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


# Fixed namespace, so the same parent and name always give the same sub_id.
SUB_NAMESPACE = uuid.UUID("8b2f4c6e-1d3a-4e5b-9c7d-0a1f2e3d4c5b")


def normalised_name(name: str) -> str:
    """Case, accents and punctuation removed; legal form kept (see the module docstring)."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", n.replace("&", " and ")).strip()


def sub_id(parent_cik: str, name: str) -> str:
    """uuid5 of '<10-digit parent CIK>|<normalised name>' (docs/schema-ids-operations.md)."""
    return str(uuid.uuid5(SUB_NAMESPACE, f"{str(parent_cik).zfill(10)}|{normalised_name(name)}"))


# EDGAR's conformed names abbreviate legal forms ("COCA COLA CO", "MCKESSON CORP"); an exhibit
# spells them out. Compare on one spelling, but keep the legal form: "Medtronic plc" (the
# registrant) and "Medtronic, Inc." (a subsidiary) must stay different.
_LEGAL_ABBREV = {"company": "co", "corporation": "corp", "incorporated": "inc", "limited": "ltd",
                 "l l c": "llc", "public limited company": "plc", "and": "and"}
_ABBREV_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _LEGAL_ABBREV), key=len, reverse=True)) + r")\b")


def _registrant_key(name: str) -> str:
    n = re.sub(r"^the\s+", "", normalised_name(name))
    return _ABBREV_RE.sub(lambda m: _LEGAL_ABBREV[m.group(1)], n)


def is_registrant(name: str, registrant: str) -> bool:
    """True when an Exhibit 21 row names the registrant itself ("The Coca-Cola Company" in
    COCA COLA CO's exhibit). Legal forms are compared, not dropped."""
    return bool(registrant) and _registrant_key(name) == _registrant_key(registrant)
