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
            id_str="2103575607242060199",
            url="https://x.com/_Tom_Henderson_/status/2103575607242060199",
            user=SimpleNamespace(
                username="_Tom_Henderson_",
                displayname="Tom Henderson",
                rawDescription=bio,
                descriptionLinks=description_links or [],
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

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

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

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.text == (
        "https://example.com/first https://t.co/unmapped https://example.com/second"
    )


def test_normalize_post_expands_tco_links_in_bio() -> None:
    tweet = tweet_with_links(
        "Post text",
        [],
        bio=(
            "Zombies news for zombies players // Donation and affiliate Linktree: "
            "https://t.co/it9nz8l1an // Email for inquiries: "
            "margwanetwork@gmail.com"
        ),
        description_links=[
            TextLink(
                url="https://linktr.ee/margwanetwork",
                text="linktr.ee/margwanetwork",
                tcourl="https://t.co/it9nz8l1an",
            ),
            TextLink(url="http://Margwa.net", text="Margwa.net", tcourl=None),
        ],
    )

    post = object.__new__(Twscraper)._normalize_post(tweet, {}, set())

    assert post.bio == (
        "Zombies news for zombies players // Donation and affiliate Linktree: "
        "https://linktr.ee/margwanetwork // Email for inquiries: "
        "margwanetwork@gmail.com"
    )


def test_normalize_post_removes_media_shortlinks() -> None:
    tweet = tweet_with_links(
        "Grand Theft Auto VI is on the cover of GameInformer https://t.co/s6v5vOkeNj",
        [],
    )
    tweet.media.photos.append(
        MediaPhoto(url="https://pbs.twimg.com/media/HTFPkt6W8AADmP9.jpg")
    )

    post = object.__new__(Twscraper)._normalize_post(
        tweet, {}, {"https://t.co/s6v5vOkeNj"}
    )

    assert post.text == "Grand Theft Auto VI is on the cover of GameInformer"
    assert post.media[0].url == "https://pbs.twimg.com/media/HTFPkt6W8AADmP9.jpg"


def test_media_metadata_recovers_alt_text_and_shortlinks() -> None:
    result = {
        "legacy": {
            "extended_entities": {
                "media": [
                    {
                        "url": "https://t.co/photo",
                        "media_url_https": "https://pbs.twimg.com/photo.jpg",
                        "ext_alt_text": "Photo description",
                    },
                    {
                        "url": "https://t.co/video",
                        "media_url_https": "https://pbs.twimg.com/video.jpg",
                        "ext_alt_text": None,
                    },
                ]
            }
        }
    }

    alt_text, shortlinks = Twscraper._media_metadata(result)

    assert alt_text == {"https://pbs.twimg.com/photo.jpg": "Photo description"}
    assert shortlinks == {"https://t.co/photo", "https://t.co/video"}
