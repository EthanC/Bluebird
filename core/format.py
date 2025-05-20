import re
from re import Pattern

from clyde.markdown import Markdown

pattern_mention: Pattern[str] = re.compile(r"(?<!\w)@(\w+)")
pattern_hashtag: Pattern[str] = re.compile(r"(?<!\w)#(\w+)")
pattern_cashtag: Pattern[str] = re.compile(r"(?<!\w)\$(\w+)")


class Format:
    """Utility class containing static methods for text formatting."""

    @staticmethod
    def replace_mentions(text: str | None, base_url: str) -> str | None:
        """Find and replace @mentions with masked links."""
        if not text:
            return None

        text = re.sub(
            pattern_mention,
            lambda mention: Markdown.masked_link(
                mention.group(0), f"{base_url}{mention.group(1)}"
            ),
            text,
        ).strip()

        if text == "":
            return None

        return text

    @staticmethod
    def replace_hashtags(text: str | None, base_url: str) -> str | None:
        """Find and replace #hashtags with masked links."""
        if not text:
            return None

        text = re.sub(
            pattern_hashtag,
            lambda mention: Markdown.masked_link(
                mention.group(0), f"{base_url}{mention.group(1)}"
            ),
            text,
        ).strip()

        if text == "":
            return None

        return text

    @staticmethod
    def replace_cashtags(text: str | None, base_url: str) -> str | None:
        """Find and replace $cashtags with masked links."""
        if not text:
            return None

        text = re.sub(
            pattern_cashtag,
            lambda mention: Markdown.masked_link(
                mention.group(0), f"{base_url}{mention.group(1)}"
            ),
            text,
        ).strip()

        if text == "":
            return None

        return text
