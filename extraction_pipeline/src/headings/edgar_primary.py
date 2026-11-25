"""Download a filing's primary document (the 10-K or 10-Q itself) from SEC EDGAR, once.

Needs SEC_USER_AGENT, like Exhibit 21 (ex21/edgar.py, whose client this reuses).
"""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from lxml import html

from ..common import filing_stem
from ..ex21 import edgar


def primary_url(filing: dict) -> str:
    """The document of type `form` in row 1 of the filing's EDGAR index page."""
    accession = filing["accession"]
    base = f"{edgar.ARCHIVES}/{int(filing['cik'])}/{accession.replace('-', '')}/"
    index = edgar._IndexRows()
    index.feed(edgar.get(f"{base}{accession}-index.html").decode("utf-8", "replace"))
    urls = set()
    for cells, href in index.rows:
        if not href or len(cells) < 4 or cells[0] != "1" or cells[3] != filing["form"]:
            continue
        href = href.removeprefix("/ix?doc=")
        path = urlsplit(href).path
        url = f"https://www.sec.gov{path}" if path.startswith("/") else base + path
        if url.startswith(base) and re.fullmatch(r"[\w.-]+\.html?", url[len(base):], re.I):
            urls.add(url)
    if len(urls) != 1:
        raise ValueError(f"Expected one primary HTML document for {accession}, found {len(urls)}")
    return urls.pop()


def cached_path(filing: dict, directory: Path) -> Path:
    return directory / f"{filing_stem(filing)}.htm"


def filing_html(filing: dict, directory: Path, fetch: bool) -> Path:
    """The cached copy, downloaded first when missing and fetch is on."""
    path = cached_path(filing, directory)
    if not path.is_file() and fetch:
        url = primary_url(filing)
        raw = edgar.get(url)
        if not html.fromstring(raw).xpath("//body"):
            raise ValueError(f"No HTML body in {url}")
        atomic_write(path, lambda stream: stream.write(raw), binary=True)
    return path


def atomic_write(path: Path, write, *, binary: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb" if binary else "w", encoding=None if binary else "utf-8",
        dir=path.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
        try:
            write(stream)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
