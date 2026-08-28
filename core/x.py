"""Monitor X (Twitter) posts from configurable data sources."""

import random
import re
from dataclasses import dataclass, replace
from os import environ
from threading import Event
from typing import Protocol, Self, Sequence
from urllib.parse import urlsplit

from archivist import (
    ArchivistError,
    InternetArchiveAccount,
    InternetArchiveClient,
    InternetArchiveSaveOptions,
)
from clyde import AllowedMentions, Webhook
from clyde.components import (
    ActionRow,
    Container,
    LinkButton,
    MediaGallery,
    MediaGalleryItem,
    Section,
    Seperator,
    SeperatorSpacing,
    TextDisplay,
    Thumbnail,
    UnfurledMediaItem,
)
from clyde.markdown import Markdown
from clyde.timestamp import Timestamp
from environs import env
from loguru import logger

from .config import XConfig
from .format import Format
from .state import StateStore, XCursor

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_]{1,15}")
ALT_TEXT_MAX_LENGTH = 1_024
MAIN_TEXT_MAX_LENGTH = 3_000
RELATED_TEXT_MAX_LENGTH = 800


def _valid_https_url(url: str, hosts: set[str] | None = None) -> bool:
    """Return whether a URL is HTTPS and optionally uses an expected host."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False

    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and (hosts is None or parsed.hostname.casefold() in hosts)
    )


@dataclass(frozen=True, slots=True)
class XPostReference:
    """Identify another X post."""

    username: str
    post_id: str

    def __post_init__(self) -> None:
        """Validate an X post reference."""
        if not USERNAME_PATTERN.fullmatch(self.username) or not self.post_id.isdigit():
            raise ValueError("Invalid X post reference")


@dataclass(frozen=True, slots=True)
class XMedia:
    """Represent media attached to an X post."""

    url: str
    alt_text: str | None = None

    def __post_init__(self) -> None:
        """Validate attached media."""
        if not _valid_https_url(self.url):
            raise ValueError("Invalid X media URL")


@dataclass(frozen=True, slots=True)
class XPost:
    """Represent a source-neutral X post."""

    post_id: str
    url: str
    username: str
    display_name: str
    created_at: int
    source: str
    text: str | None = None
    bio: str | None = None
    profile_image_url: str | None = None
    media: tuple[XMedia, ...] = ()
    possibly_sensitive: bool = False
    is_reply: bool = False
    is_quote: bool = False
    is_repost: bool = False
    reply_to: XPostReference | None = None
    quote_of: XPostReference | None = None
    repost_of: XPostReference | None = None

    def __post_init__(self) -> None:
        """Validate normalized X post data."""
        if not self.post_id.isdigit():
            raise ValueError("Invalid X post ID")
        if not USERNAME_PATTERN.fullmatch(self.username):
            raise ValueError("Invalid X username")
        if self.created_at <= 0:
            raise ValueError("Invalid X post timestamp")
        if not _valid_https_url(
            self.url, {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
        ):
            raise ValueError("Invalid X post URL")
        if self.profile_image_url and not _valid_https_url(self.profile_image_url):
            raise ValueError("Invalid X profile image URL")


@dataclass(frozen=True, slots=True)
class XFeed:
    """Contain the posts and polling metadata returned by a data source."""

    username: str
    posts: tuple[XPost, ...]
    max_age: float | None = None
    complete: bool = True


class XDataSource(Protocol):
    """Provide normalized X post data."""

    retries: int
    retry_delay: float

    def fetch_user(self, username: str) -> XFeed | None:
        """Fetch the latest available posts for an X user."""
        ...

    def fetch_post(self, username: str, post_id: str) -> XPost | None:
        """Fetch one X post."""
        ...


class XInstance:
    """Class representing an X instance configuration."""

    base_url: str = "https://x.com/"

    def __init__(
        self: Self,
        sources: Sequence[XDataSource],
        state: StateStore,
        archive_account: InternetArchiveAccount | None = None,
    ) -> None:
        """Initialize an X instance with its post data sources."""
        self.sources: tuple[XDataSource, ...] = tuple(sources)
        self.state: StateStore = state
        self.archive_account: InternetArchiveAccount | None = archive_account
        self.archive_client: InternetArchiveClient | None = None
        self.archive_options: InternetArchiveSaveOptions | None = (
            InternetArchiveSaveOptions(capture_screenshot=True, save_to_archive=True)
            if archive_account
            else None
        )
        self.index: int = 0
        self.state_key: str = ""
        self.usernames: tuple[str, ...] = ()
        self.webhook_url: str = ""
        self.require_media: bool = False
        self.require_keyword: tuple[str, ...] = ()
        self.exclude_reply: bool = False
        self.exclude_repost: bool = False
        self.exclude_keyword: tuple[str, ...] = ()
        self.proxy: bool = False
        self.proxy_name: str = "X"
        self.archive_base_url: str = "https://twstalker.com/"

    def log(self: Self, username: str | None = None, post_id: str | None = None) -> str:
        """Craft the head of a log message given an instance and username."""
        head: str = "X"

        head += f"[{self.index:,}]"

        if username:
            head += f"[@{username}]"

        if post_id:
            head += f"[{post_id}]"

        return head

    def start(self: Self, config: XConfig, index: int, stop: Event) -> None:
        """Run a continuous loop for the usernames within the X instance."""
        self.index = index
        self.state_key = config.state_key
        self.usernames = config.usernames
        self.webhook_url = config.discord_webhook_url
        self.require_media = config.require_media
        self.require_keyword = config.require_keyword
        self.exclude_reply = config.exclude_reply
        self.exclude_repost = config.exclude_repost
        self.exclude_keyword = config.exclude_keyword
        self.proxy = config.proxy
        self.proxy_name = config.proxy_name if config.proxy else "X"
        self.base_url = (
            f"https://{config.proxy_host}/" if config.proxy else "https://x.com/"
        )
        self.archive_base_url = f"https://{config.proxy_host}/"

        for source in self.sources:
            source.retries = config.retries
            source.retry_delay = config.retry_delay

        logger.info(f"{self.log()} Loaded instance configuration")

        cooldown_configured: float = config.cooldown
        self.archive_client = (
            InternetArchiveClient(account=self.archive_account)
            if config.archive
            else None
        )

        if self.archive_client:
            mode: str = "authenticated" if self.archive_account else "anonymous"
            logger.info(f"{self.log()} Enabled {mode} Internet Archive captures")

        try:
            while not stop.is_set():
                cooldown: float = cooldown_configured

                for index, username in enumerate(self.usernames):
                    if stop.is_set():
                        return

                    if environ.get("DEBUG_STATE"):
                        debug_created_at: int = env.int("DEBUG_STATE")
                        self.state.set(
                            self.state_key,
                            username,
                            XCursor(debug_created_at, "0"),
                            force=True,
                        )

                    cooldown_new: float | None = self.watch_user(username)

                    if cooldown_new and cooldown_new > cooldown:
                        cooldown = cooldown_new

                    if (index + 1) < len(self.usernames):
                        # Wait between watching users to avoid API load
                        if stop.wait(random.uniform(3.0, 10.0)):
                            return

                logger.info(
                    f"{self.log()} Instance is sleeping for {int(cooldown):,}s..."
                )

                stop.wait(cooldown)
        finally:
            if self.archive_client:
                self.archive_client.close()
                self.archive_client = None

    def watch_user(self: Self, username: str) -> float | None:
        """Process user data and trigger notifications."""
        logger.info(f"{self.log(username)} Checking for new posts...")

        feed: XFeed | None = self.fetch_user(username)

        if not feed:
            logger.debug(f"{self.log(username)} Received invalid data")
            logger.trace(f"{self.log(username)} {feed=}")

            return

        if not feed.posts:
            logger.debug(f"{self.log(username)} Received an empty feed")

            return feed.max_age

        username = feed.username
        posts: tuple[XPost, ...] = feed.posts

        cursor: XCursor | None = self.state.get(self.state_key, username)

        if cursor is None:
            latest: XPost = max(
                posts,
                key=lambda post: XCursor(post.created_at, post.post_id).sort_key(),
            )
            cursor = XCursor(latest.created_at, latest.post_id)
            self.state.set(self.state_key, username, cursor)

            logger.info(
                f"{self.log(username)} Set initial state ({cursor.created_at}, {cursor.post_id})"
            )

            return feed.max_age

        for post in posts:
            post_id: str = post.post_id
            post_cursor = XCursor(post.created_at, post_id)

            if not post_cursor.is_after(cursor):
                logger.debug(
                    f"{self.log(username, post_id)} Skipped post, not newer than state"
                )
                logger.trace(f"{self.log(username, post_id)} {post=}")

                continue

            if reason := self.filter_reason(post):
                logger.debug(f"{self.log(username, post_id)} Skipped post, {reason}")
                logger.trace(f"{self.log(username, post_id)} {post=}")
                self.state.set(self.state_key, username, post_cursor)
                cursor = post_cursor

                continue

            logger.success(
                f"{self.log(username, post_id)} Discovered new post {post.url}"
            )

            self.archive_post(post)
            self.notify(post)
            self.state.set(self.state_key, username, post_cursor)
            cursor = post_cursor

            logger.info(
                f"{self.log(username)} Set latest state ({cursor.created_at}, {cursor.post_id})"
            )

        logger.info(f"{self.log(username)} {len(posts):,} posts processed")

        return feed.max_age

    def filter_reason(self: Self, post: XPost) -> str | None:
        """Return the configured reason a post should be filtered."""
        post_text: str = post.text or ""
        folded_text: str = post_text.casefold()

        if self.require_keyword and not any(
            keyword.casefold() in folded_text for keyword in self.require_keyword
        ):
            return "keyword requirement not met"
        if self.require_media and not post.media:
            return "media requirement not met"
        if self.exclude_reply and post.is_reply:
            return "replies excluded"
        if self.exclude_repost and post.is_repost:
            return "reposts excluded"

        if keyword := next(
            (
                keyword
                for keyword in self.exclude_keyword
                if keyword.casefold() in folded_text
            ),
            None,
        ):
            return f"keyword {keyword} excluded"

        return None

    def fetch_user(self: Self, username: str) -> XFeed | None:
        """Fetch and safely merge available feeds for an X user."""
        feeds: list[XFeed] = []

        for source in self.sources:
            feed: XFeed | None = source.fetch_user(username)

            if feed:
                feeds.append(feed)

        if not feeds:
            return

        complete_feeds: list[XFeed] = [feed for feed in feeds if feed.complete]

        if not complete_feeds:
            logger.warning(
                f"{self.log(username)} No complete source feed was available"
            )

            return

        posts_by_id: dict[str, XPost] = {}
        latest_complete_cursor: XCursor | None = max(
            (
                XCursor(post.created_at, post.post_id)
                for feed in complete_feeds
                for post in feed.posts
            ),
            key=XCursor.sort_key,
            default=None,
        )

        for feed in feeds:
            for post in feed.posts:
                post_cursor = XCursor(post.created_at, post.post_id)

                if not feed.complete and (
                    latest_complete_cursor is None
                    or post_cursor.is_after(latest_complete_cursor)
                ):
                    continue

                posts_by_id.setdefault(post.post_id, post)

        posts: tuple[XPost, ...] = tuple(
            sorted(
                posts_by_id.values(),
                key=lambda post: XCursor(post.created_at, post.post_id).sort_key(),
            )
        )
        max_ages: list[float] = [
            feed.max_age for feed in complete_feeds if feed.max_age is not None
        ]

        return XFeed(
            username=complete_feeds[0].username,
            posts=posts,
            max_age=max(max_ages) if max_ages else None,
        )

    def fetch_post(self: Self, username: str, post_id: str) -> XPost | None:
        """Fetch a post and supplement it with the first available author bio."""
        fallback: XPost | None = None

        for source in self.sources:
            post: XPost | None = source.fetch_post(username, post_id)

            if post and post.post_id == post_id:
                if post.bio:
                    return replace(fallback, bio=post.bio) if fallback else post

                fallback = fallback or post

                continue

            if post:
                logger.error(
                    f"{self.log(username, post_id)} {type(source).__name__} returned the wrong post"
                )

        return fallback

    def archive_post(self: Self, post: XPost) -> None:
        """Archive a post when Internet Archive capture is enabled."""
        if not self.archive_client:
            return

        archive_target: str = Format.x_url(post.url, self.archive_base_url)

        try:
            capture = self.archive_client.save(archive_target, self.archive_options)
        except ArchivistError as error:
            logger.opt(exception=error).error(
                f"{self.log(post.username, post.post_id)} Internet Archive capture failed"
            )

            return

        logger.success(
            f"{self.log(post.username, post.post_id)} Archived post {capture.archive_url()}"
        )

    def notify(self: Self, post: XPost) -> None:
        """Send a Discord Webhook notification for the provided X post."""
        webhook: Webhook = Webhook(
            url=self.webhook_url, allowed_mentions=AllowedMentions(parse=[])
        )
        post_containers: list[Container] = []
        original_post: XPost | None = None

        if post.reply_to:
            reply_parent: XPost | None = self.fetch_post(
                post.reply_to.username, post.reply_to.post_id
            )

            if reply_parent:
                post_containers.append(
                    self.build_post(reply_parent, True, include_footer=False)
                )

        post_containers.append(self.build_post(post, include_footer=False))

        if post.quote_of:
            quote_post: XPost | None = self.fetch_post(
                post.quote_of.username, post.quote_of.post_id
            )

            if quote_post:
                original_post = quote_post
                post_containers.append(
                    self.build_post(quote_post, True, include_footer=False)
                )

        if post.repost_of:
            repost: XPost | None = self.fetch_post(
                post.repost_of.username, post.repost_of.post_id
            )

            if repost:
                original_post = repost
                post_containers.append(
                    self.build_post(repost, True, include_footer=False)
                )

        container: Container = post_containers[0]

        for post_container in post_containers[1:]:
            container.add_component(
                Seperator(divider=False, spacing=SeperatorSpacing.LARGE)
            )
            container.add_component(post_container.components)

        container.add_component(Seperator(divider=True, spacing=SeperatorSpacing.SMALL))
        container.add_component(self.build_post_footer(post))

        webhook.add_component(container)
        webhook.add_component(self.build_post_outbound(post, original_post))

        logger.debug(f"{self.log(post.username, post.post_id)} Built Webhook for post")

        try:
            webhook.execute()
        except Exception:
            raise RuntimeError("Discord webhook delivery failed") from None

    def build_post(
        self: Self, post: XPost, mini: bool = False, *, include_footer: bool = True
    ) -> Container:
        """Build a Discord Container Component for the provided X post."""
        head: Section = self.build_post_head(post, mini)
        body: TextDisplay | None = self.build_post_body(
            post, RELATED_TEXT_MAX_LENGTH if mini else MAIN_TEXT_MAX_LENGTH
        )
        media: MediaGallery | None = None if mini else self.build_post_media(post)

        container: Container = Container(components=[head], accent_color="#000000")

        if body and not post.is_repost:
            container.add_component(body)

        if media and not post.is_repost:
            container.add_component(media)

        if include_footer:
            container.add_component(
                Seperator(divider=True, spacing=SeperatorSpacing.SMALL)
            )
            container.add_component(self.build_post_footer(post))

        logger.debug(
            f"{self.log(post.username, post.post_id)} Built Container for post"
        )
        logger.trace(f"{self.log(post.username, post.post_id)} {container=}")

        return container

    def build_post_head(self: Self, post: XPost, mini: bool = False) -> Section:
        """Build a Discord Section Component for the provided X post."""
        name_username: str = Markdown.masked_link(
            f"@{post.username}", f"{self.base_url}{post.username}"
        )
        name_display: str = Format.escape_markdown(post.display_name)
        avatar: str | None = post.profile_image_url
        bio: str | None = post.bio

        if bio:
            bio = Format.x_text(bio, self.base_url, rewrite_urls=self.proxy)

        if avatar:
            accessory: Thumbnail | LinkButton = Thumbnail(
                media=UnfurledMediaItem(url=avatar.replace("_normal", "")),
                description=f"X user @{post.username}'s avatar.",
            )
        else:
            accessory = LinkButton(
                label="View Profile", url=f"{self.base_url}{post.username}"
            )

        if mini:
            head_content: list[str] = [
                Markdown.bold(f"{name_display} ({name_username})")
            ]

            if bio:
                head_content.append(Markdown.subtext(bio))

            return Section(
                components=[TextDisplay(content=content) for content in head_content],
                accessory=accessory,
            )

        head_content = [Markdown.header_1(f"{name_display} ({name_username})")]

        if bio:
            head_content.append(Markdown.subtext(bio))

        head: Section = Section(
            components=[TextDisplay(content=content) for content in head_content],
            accessory=accessory,
        )

        logger.debug(f"{self.log(post.username, post.post_id)} Built head for post")
        logger.trace(f"{self.log(post.username, post.post_id)} {head=}")

        return head

    def build_post_body(self: Self, post: XPost, max_length: int) -> TextDisplay | None:
        """Build a Discord Text Display Component for the provided X post."""
        text: str | None = post.text

        if not text:
            return

        text = Format.x_text(
            text, self.base_url, max_length=max_length - 4, rewrite_urls=self.proxy
        )

        # Text may have become None after formatting
        if not text:
            return

        body: TextDisplay = TextDisplay(content=Markdown.block_quote(text))

        logger.debug(f"{self.log(post.username, post.post_id)} Built body for post")
        logger.trace(f"{self.log(post.username, post.post_id)} {body=}")

        return body

    def build_post_media(self: Self, post: XPost) -> MediaGallery | None:
        """Build a Discord Media Gallery Component for the provided X post."""
        if not post.media:
            return

        items: list[MediaGalleryItem] = []

        for post_media in post.media:
            item: MediaGalleryItem = MediaGalleryItem(
                media=UnfurledMediaItem(url=post_media.url)
            )

            if post_media.alt_text:
                item.set_description(post_media.alt_text[:ALT_TEXT_MAX_LENGTH])

            if post.possibly_sensitive:
                item.set_spoiler(True)

            items.append(item)

        if not items:
            return

        media: MediaGallery = MediaGallery(items=items)

        logger.debug(f"{self.log(post.username, post.post_id)} Built media for post")
        logger.trace(f"{self.log(post.username, post.post_id)} {media=}")

        return media

    def build_post_footer(self: Self, post: XPost) -> TextDisplay:
        """Build a footer for the provided X post."""
        posted: int = post.created_at
        ts_long: str = Timestamp.long_date_time(posted)
        ts_relative: str = Timestamp.relative_time(posted)

        action: str = "Posted"

        if post.is_repost:
            action = "Reposted"
        elif post.is_quote:
            action = "Quoted"
        elif post.is_reply:
            action = "Replied"

        footer: TextDisplay = TextDisplay(
            content=Markdown.subtext(
                f"{action} {ts_long} ({ts_relative}) [{post.source}]"
            )
        )

        logger.debug(f"{self.log(post.username, post.post_id)} Built footer for post")
        logger.trace(f"{self.log(post.username, post.post_id)} {footer=}")

        return footer

    def build_post_outbound(
        self: Self, post: XPost, original_post: XPost | None = None
    ) -> ActionRow:
        """Build actions for the provided X post."""
        post_url: str = (
            Format.x_url(post.url, self.base_url) if self.proxy else post.url
        )
        components: list[LinkButton] = [
            LinkButton(label=f"View Post on {self.proxy_name}", url=post_url)
        ]
        original: XPostReference | None = post.repost_of or post.quote_of

        if original:
            original_username: str = (
                original_post.username
                if original_post and original_post.post_id == original.post_id
                else original.username
            )
            components.append(
                LinkButton(
                    label=f"View Original Post on {self.proxy_name}",
                    url=f"{self.base_url}{original_username}/status/{original.post_id}",
                )
            )

        components.append(
            LinkButton(
                label="Powered by Bluebird", url="https://github.com/EthanC/Bluebird"
            )
        )
        outbound: ActionRow = ActionRow(components=components)

        logger.debug(
            f"{self.log(post.username, post.post_id)} Built outbound links for post"
        )
        logger.trace(f"{self.log(post.username, post.post_id)} {outbound=}")

        return outbound
