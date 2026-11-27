"""Download a filing's primary document (the 10-K or 10-Q itself) from SEC EDGAR, once, and
remember its URL (urls.json next to the cached files) for the review links.

Needs SEC_USER_AGENT, like Exhibit 21 (ex21/edgar.py, whose client this reuses).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote, urlsplit

from lxml import html

from ..common import filing_stem
from ..entities.ex21 import edgar


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


class Urls:
    """accession -> primary document URL, kept in <directory>/urls.json."""

    def __init__(self, directory: Path):
        self.path = directory / "urls.json"
        self.urls: dict[str, str] = json.loads(self.path.read_text()) if self.path.is_file() else {}

    def get(self, filing: dict, fetch: bool) -> str | None:
        """The URL, looked up on EDGAR (one index page) when unknown and fetch is on."""
        if filing["accession"] not in self.urls and fetch:
            self.urls[filing["accession"]] = primary_url(filing)
            self.save()
        return self.urls.get(filing["accession"])

    def save(self) -> None:
        atomic_write(self.path, lambda stream: json.dump(self.urls, stream, indent=1, sort_keys=True))


def filing_html(filing: dict, directory: Path, fetch: bool, urls: Urls | None = None) -> Path:
    """The cached copy, downloaded first when missing and fetch is on."""
    path = cached_path(filing, directory)
    if not path.is_file() and fetch:
        url = urls.get(filing, fetch) if urls else primary_url(filing)
        raw = edgar.get(url)
        if not html.fromstring(raw).xpath("//body"):
            raise ValueError(f"No HTML body in {url}")
        atomic_write(path, lambda stream: stream.write(raw), binary=True)
    return path


def fragment(text: str) -> str:
    """Percent-encode a piece of a text-fragment link (also "-", "," and "&", which the syntax uses)."""
    return quote(" ".join(text.split()), safe="").replace("-", "%2D")


def text_link(url: str | None, heading: str, suffix: str = "", split: bool = False) -> str:
    """A link that opens the filing scrolled to the heading and highlights it (Chrome, Edge,
    Safari: `#:~:text=`). A long heading, or one the HTML splits over lines (split), is given by
    its first and last words; the first words of the block after it, when known, pick this copy
    and not one in a table of contents."""
    if not url:
        return ""
    words = heading.split()
    n = 4 if len(words) > 8 else len(words) // 2
    target = (fragment(" ".join(words[:n])) + "," + fragment(" ".join(words[-n:]))
              if (len(words) > 8 or split) and n else fragment(heading))
    return f"{url}#:~:text={target}" + (f",-{fragment(suffix)}" if suffix else "")


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
