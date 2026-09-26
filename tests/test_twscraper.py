from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from twscrape.models import TextLink, Tweet

from core.twscraper import Twscraper


def tweet_with_links(text: str, links: list[TextLink]) -> Tweet:
    return cast(
        Tweet,
        SimpleNamespace(
            id_str="2103575607242060199",
            url="https://x.com/_Tom_Henderson_/status/2103575607242060199",
            user=SimpleNamespace(
                username="_Tom_Henderson_",
                displayname="Tom Henderson",
                rawDescription="",
                profileImageUrl="",
            ),
            date=datetime(2026, 9, 26, tzinfo=UTC),
            rawContent=text,
            links=links,
            media=SimpleNamespace(photos=[], videos=[], animated=[]),
            possibly_sensitive=False,
            inReplyToTweetIdStr=None,
            inReplyToUser=None,
            inReplyToScreenName=None,
            isQuoteStatus=False,
            quotedTweet=None,
            retweetedTweet=None,
        ),
    )


def test_normalize_post_expands_tco_links() -> None:
    tweet = tweet_with_links(
        "INISDER GAMING IS LIVEEEEEEEEE https://t.co/d2IVGwzun5",
        [
            TextLink(
                url="https://www.youtube.com/watch?v=YirGnAwW5hU",
                text="youtube.com/watch?v=YirGnA...",
                tcourl="https://t.co/d2IVGwzun5",
            )
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {})

    assert post.text == (
        "INISDER GAMING IS LIVEEEEEEEEE https://www.youtube.com/watch?v=YirGnAwW5hU"
    )


def test_normalize_post_only_expands_mapped_tco_links() -> None:
    tweet = tweet_with_links(
        "https://t.co/first https://t.co/unmapped https://t.co/second",
        [
            TextLink("https://example.com/first", None, "https://t.co/first"),
            TextLink("https://example.com/missing", None, None),
            TextLink("https://example.com/second", None, "https://t.co/second"),
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {})

    assert post.text == (
        "https://example.com/first https://t.co/unmapped https://example.com/second"
    )
