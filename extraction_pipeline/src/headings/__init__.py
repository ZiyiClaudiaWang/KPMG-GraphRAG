"""Step 4b - Heading levels from each filing's own EDGAR HTML (no LLM).

Plain text shows that a line is a heading, not how deep it sits: in "Results of Operations" /
"Segment Results" / "Intelligent Cloud" each heading would replace the one before it in
section_path. The filing's HTML still has the typography (size, bold, capitals, ...), so this
step gives each heading a level from it, and step 4 keeps the headings above a unit in
section_path and heading_path.

  candidates.py     the headings step 4 finds, and the levels file step 4 reads
  html.py           HTML -> blocks with typography, aligned to the Data-team text
  levels.py         the level rules (see its docstring)
  edgar_primary.py  download the filing's primary document once
  step.py           the step: run(), review and summary files
"""

from .candidates import levels_path, load_levels  # noqa: F401


def run(fetch: bool = True, compare_with=None) -> None:
    from .step import run as _run
    _run(fetch=fetch, compare_with=compare_with)
