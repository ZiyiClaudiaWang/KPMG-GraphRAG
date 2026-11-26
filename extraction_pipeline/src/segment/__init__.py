"""Step 4 - Segment: split kept sections into units, in source order.

Input : data/interim/clean/*.jsonl, data/output/section_manifest.csv,
        data/interim/heading_levels.jsonl and lead_sentences.jsonl (step 4b, when it has run)
Output: data/output/sentences.jsonl   one row per unit, in source order
        data/output/lead_summary.csv, lead_review.csv

A unit is one sentence, one list item, one list group (an intro ending in ":" plus a short list of
fragments), a heading, a risk category heading or a section title.

  sentences.py   where a sentence ends (abbreviations, OPENERS)
  lists.py       bullets and lettered items, list_group or split items
  headings.py    which lines are headings, their levels (step 4b), the heading path
  risk.py        Item 1A: rule-proposed lead sentences (rule_heading), the risk factor summary
  prefilter.py   not_a_claim by rule, before the LLM
  units.py       the unit record, unit_id, segmentation_version
  section.py     one section, paragraph by paragraph (the order of the rules)
  link_leads.py  lead sentences: lead_source, lead_unit_id, the lead review files
  step.py        run()

Everything other modules and the tests use is importable from src.segment directly.
"""

from ..common import settings  # noqa: F401
from ..headings.candidates import NOT_A_HEADING, SENTENCE_LEVEL  # noqa: F401
from .headings import (HeadingPath, Levels, heading_id, is_category, is_colon_heading,  # noqa: F401
                       is_heading_like, is_line_heading, is_period_heading)
from .link_leads import lead_report, mark_leads, uses_rule  # noqa: F401
from .lists import (BULLET, is_bullet, is_fragment, item_marker, list_items_end, merge_list,  # noqa: F401
                    short_item, starts_list)
from .prefilter import CROSS_REFERENCE, TABLE_NOTE_KEYS, TABLE_NOTES, note_key, prefilter  # noqa: F401
from .risk import mark_risk_summary, rule_lead_strength  # noqa: F401
from .section import segment_section  # noqa: F401
from .sentences import OPENERS, TERMINAL, sentence_count, split_sentences  # noqa: F401
from .step import STEP, run  # noqa: F401
from .units import SEGMENTATION_VERSION, section_label  # noqa: F401
