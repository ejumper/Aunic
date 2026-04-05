from __future__ import annotations

import re

from aunic.research.search import canonicalize_url

_INLINE_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def extract_inline_citation_urls(text: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in _INLINE_LINK_RE.finditer(text))


def find_invalid_citation_urls(
    text: str,
    *,
    allowed_canonical_urls: set[str],
) -> tuple[str, ...]:
    invalid: list[str] = []
    for url in extract_inline_citation_urls(text):
        if canonicalize_url(url) not in allowed_canonical_urls:
            invalid.append(url)
    return tuple(invalid)
