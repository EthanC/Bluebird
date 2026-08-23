"""Utility functions for safe Discord text formatting."""

import re
from re import Match, Pattern
from urllib.parse import quote, urlsplit

from clyde.markdown import Markdown

TOKEN_PATTERN: Pattern[str] = re.compile(
    r"(?P<url>https?://[^\s<>]+)|(?<![\w/])(?P<symbol>[@#$])(?P<name>\w+)"
)
MARKDOWN_PATTERN: Pattern[str] = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|>~])")
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}


class Format:
    """Format untrusted X text for Discord."""

    @staticmethod
    def escape_markdown(text: str) -> str:
        """Escape Discord Markdown control characters."""
        return MARKDOWN_PATTERN.sub(r"\\\1", text)

    @classmethod
    def x_text(
        cls,
        text: str | None,
        base_url: str,
        *,
        max_length: int | None = None,
        rewrite_urls: bool = False,
    ) -> str | None:
        """Escape text, link X entities, and optionally rewrite X URLs."""
        if not text or not (text := text.strip()):
            return None

        formatted: list[str] = []
        position: int = 0

        for token in TOKEN_PATTERN.finditer(text):
            formatted.append(cls.escape_markdown(text[position : token.start()]))
            formatted.append(cls._token(token, base_url, rewrite_urls))
            position = token.end()

        formatted.append(cls.escape_markdown(text[position:]))
        result: str = "".join(formatted)

        if max_length is not None and len(result) > max_length:
            result = cls.escape_markdown(text)

        return cls._truncate(result, max_length)

    @classmethod
    def _token(cls, token: Match[str], base_url: str, rewrite_urls: bool) -> str:
        """Format one URL or X entity token."""
        if url := token.group("url"):
            return cls.x_url(url, base_url) if rewrite_urls else url

        symbol: str = token.group("symbol")
        name: str = token.group("name")
        label: str = f"{symbol}{name}"

        if symbol == "@":
            url = f"{base_url}{quote(name, safe='')}"
        elif symbol == "#":
            url = f"{base_url}hashtag/{quote(name, safe='')}"
        else:
            url = f"{base_url}search?q={quote(label, safe='')}"

        return Markdown.masked_link(label, url)

    @staticmethod
    def x_url(url: str, base_url: str) -> str:
        """Replace an X or Twitter URL host with the configured base host."""
        try:
            parsed = urlsplit(url)
        except ValueError:
            return url

        if parsed.hostname and parsed.hostname.casefold() in X_HOSTS:
            base = urlsplit(base_url)
            return parsed._replace(scheme=base.scheme, netloc=base.netloc).geturl()

        return url

    @staticmethod
    def _truncate(text: str, max_length: int | None) -> str:
        """Truncate text without leaving a trailing Markdown escape."""
        if max_length is None or len(text) <= max_length:
            return text
        if max_length <= 3:
            return "." * max_length

        truncated: str = text[: max_length - 3].rstrip("\\")

        return f"{truncated}..."
