from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from twscrape.models import MediaPhoto, TextLink, Tweet

from core.twscraper import Twscraper


def tweet_with_links(
    text: str,
    links: list[TextLink],
    *,
    bio: str = "",
    description_links: list[TextLink] | None = None,
) -> Tweet:
    return cast(
        Tweet,
        SimpleNamespace(
            id_str="123",
            url="https://x.com/example/status/123",
            user=SimpleNamespace(
                username="example",
                displayname="Example User",
                rawDescription=bio,
                descriptionLinks=description_links or [],
                profileImageUrl="",
            ),
            date=datetime(2000, 1, 1, tzinfo=UTC),
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
        "Lorem ipsum https://t.co/article",
        [
            TextLink(
                url="https://example.com/article",
                text="example.com/article",
                tcourl="https://t.co/article",
            )
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == "Lorem ipsum https://example.com/article"


def test_normalize_post_only_expands_mapped_tco_links() -> None:
    tweet = tweet_with_links(
        "https://t.co/first https://t.co/unmapped https://t.co/second",
        [
            TextLink("https://example.com/first", None, "https://t.co/first"),
            TextLink("https://example.com/missing", None, None),
            TextLink("https://example.com/second", None, "https://t.co/second"),
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == (
        "https://example.com/first https://t.co/unmapped https://example.com/second"
    )


def test_normalize_post_expands_tco_links_in_bio() -> None:
    tweet = tweet_with_links(
        "Post text",
        [],
        bio=(
            "Lorem ipsum dolor sit amet https://t.co/profile // Email: user@example.com"
        ),
        description_links=[
            TextLink(
                url="https://example.com/profile",
                text="example.com/profile",
                tcourl="https://t.co/profile",
            ),
            TextLink(
                url="https://example.com/about", text="example.com/about", tcourl=None
            ),
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.bio == (
        "Lorem ipsum dolor sit amet https://example.com/profile // Email: "
        "user@example.com"
    )


def test_normalize_post_removes_media_shortlinks() -> None:
    tweet = tweet_with_links("Lorem ipsum dolor sit amet https://t.co/media", [])
    tweet.media.photos.append(MediaPhoto(url="https://example.com/media/photo.jpg"))

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, {"https://t.co/media"})

    assert post.text == "Lorem ipsum dolor sit amet"
    assert post.media[0].url == "https://example.com/media/photo.jpg"


def test_media_metadata_recovers_alt_text_and_shortlinks() -> None:
    result = {
        "legacy": {
            "extended_entities": {
                "media": [
                    {
                        "url": "https://t.co/photo",
                        "media_url_https": "https://example.com/media/photo.jpg",
                        "ext_alt_text": "Lorem ipsum",
                    },
                    {
                        "url": "https://t.co/video",
                        "media_url_https": "https://example.com/media/video.jpg",
                        "ext_alt_text": None,
                    },
                ]
            }
        }
    }

    alt_text, shortlinks = Twscraper._media_metadata(result)

    assert alt_text == {"https://example.com/media/photo.jpg": "Lorem ipsum"}
    assert shortlinks == {"https://t.co/photo", "https://t.co/video"}
