import json
from datetime import datetime
from operator import itemgetter
from os import environ
from time import sleep

import httpx
from httpx import Response
from loguru import logger


class X:
    """Interact and engage with the X API."""

    def GetUserID(username: str) -> int:
        """Fetch an X user by their username and return their unique identifier."""

        userId: int | None = None

        try:
            res: Response = httpx.get(f"https://api.fxtwitter.com/{username}")

            logger.debug(f"[@{username}] HTTP {res.status_code} GET {res.url}")
            logger.trace(f"[@{username}] {res.text}")

            res.raise_for_status()

            userId = int(res.json()["user"]["id"])
        except Exception as e:
            logger.opt(exception=e).error(f"[@{username}] Failed to get user")

        if userId:
            logger.trace(f"[@{username}] Found userId {userId}")

        return userId

    def GetUserPosts(
        username: str,
        limit: int = 25,
        includeReplies: bool = True,
        includeReposts: bool = True,
        onlyMedia: bool = False,
    ) -> list[dict[str, int | str]]:
        """Fetch an array of the latest posts from the specified X username."""

        userId: int = X.GetUserID(username)

        if not userId:
            return

        entries: list[dict] = []
        posts: list[dict[str, int]] = []

        endpoint: str = "https://twitter.com/i/api/graphql/-gxtzCQbBPmOwxnY-SbiHQ/UserTweetsAndReplies"
        variables: dict[str, str | int | bool | None] = {
            "userId": userId,
            "count": limit,
            "cursor": None,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True,
        }
        features: dict[str, bool] = {
            "android_graphql_skip_api_media_color_palette": False,
            "blue_business_profile_image_shape_enabled": False,
            "creator_subscriptions_subscription_count_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "freedom_of_speech_not_reach_fetch_enabled": False,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": False,
            "hidden_profile_likes_enabled": False,
            "highlights_tweets_tab_ui_enabled": False,
            "interactive_text_enabled": False,
            "longform_notetweets_consumption_enabled": True,
            "longform_notetweets_inline_media_enabled": False,
            "longform_notetweets_richtext_consumption_enabled": True,
            "longform_notetweets_rich_text_read_enabled": False,
            "responsive_web_edit_tweet_api_enabled": False,
            "responsive_web_enhance_cards_enabled": False,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": False,
            "responsive_web_media_download_video_enabled": False,
            "responsive_web_text_conversations_enabled": False,
            "responsive_web_twitter_article_tweet_consumption_enabled": False,
            "responsive_web_twitter_blue_verified_badge_is_enabled": True,
            "rweb_lists_timeline_redesign_enabled": True,
            "spaces_2022_h2_clipping": True,
            "spaces_2022_h2_spaces_communities": True,
            "standardized_nudges_misinfo": False,
            "subscriptions_verification_info_enabled": True,
            "subscriptions_verification_info_reason_enabled": True,
            "subscriptions_verification_info_verified_since_enabled": True,
            "super_follow_badge_privacy_enabled": False,
            "super_follow_exclusive_tweet_notifications_enabled": False,
            "super_follow_tweet_api_enabled": False,
            "super_follow_user_api_enabled": False,
            "tweet_awards_web_tipping_enabled": False,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": False,
            "tweetypie_unmention_optimization_enabled": False,
            "unified_cards_ad_metadata_container_dynamic_card_content_query_enabled": False,
            "verified_phone_label_enabled": False,
            "vibe_api_enabled": False,
            "view_counts_everywhere_api_enabled": False,
            "rweb_video_timestamps_enabled": False,
            "c9s_tweet_anatomy_moderator_badge_enabled": False,
        }

        if not includeReplies:
            endpoint = (
                "https://twitter.com/i/api/graphql/XicnWRbyQ3WgVY__VataBQ/UserTweets"
            )

        try:
            res: Response = httpx.get(
                endpoint,
                params={
                    "variables": json.dumps(variables),
                    "features": json.dumps(features),
                },
                headers={
                    "authorization": f"Bearer {environ.get("X_BEARER_TOKEN")}",
                    "x-csrf-token": environ.get("X_CSRF_TOKEN"),
                    "cookie": f"ct0={environ.get("X_CSRF_TOKEN")}; auth_token={environ.get("X_AUTH_TOKEN")}",
                },
            )

            logger.debug(f"[@{username}] HTTP {res.status_code} GET {res.url}")
            logger.trace(f"[@{username}] {res.text}")

            res.raise_for_status()

            result: dict = res.json()["data"]["user"].get("result")

            if not result:
                raise Exception("user result is null")

            for instruction in result["timeline_v2"]["timeline"]["instructions"]:
                if instruction["type"] != "TimelineAddEntries":
                    continue

                entries = instruction["entries"]
        except Exception as e:
            if res.status_code == 429:
                now: int = int(datetime.now().timestamp())
                reset: int = int(res.headers.get("x-rate-limit-reset", now + 300))
                backoff: int = reset - now

                logger.info(f"[@{username}] Waiting {backoff:,}s due to ratelimit")

                sleep(backoff)
            else:
                logger.opt(exception=e).error(f"[@{username}] Failed to get posts")

        if len(entries) > 0:
            logger.trace(f"[@{username}] {entries}")

        for entry in entries:
            logger.trace(f"[@{username}] {entry}")

            if len(posts) >= limit:
                break

            content: dict = entry["content"]

            if not content.get("itemContent"):
                logger.debug(
                    f"[@{username}] Skipped {entry["entryId"]} due to lack of itemContent"
                )

                continue

            try:
                result: dict = content["itemContent"]["tweet_results"]["result"]

                if not includeReposts:
                    if result["legacy"].get("retweeted_status_result"):
                        logger.debug(
                            f"[@{username}] Skipped {entry["entryId"]} due to repost"
                        )

                        continue

                if onlyMedia:
                    if not result["legacy"]["entities"].get("media"):
                        logger.debug(
                            f"[@{username}] Skipped {entry["entryId"]} due to lack of media"
                        )

                        continue

                # result data is sometimes within a tweet object.
                if (not result.get("rest_id")) or (not result.get("legacy")):
                    result = result["tweet"]

                posts.append(
                    {
                        "postId": int(result["rest_id"]),
                        "timestamp": int(
                            datetime.strptime(
                                result["legacy"]["created_at"],
                                "%a %b %d %H:%M:%S %z %Y",
                            ).timestamp()
                        ),
                        "username": username,
                    }
                )
            except Exception as e:
                logger.opt(exception=e).warning(f"[@{username}] Failed to parse post")

        # Sort the array from oldest to newest.
        posts = sorted(posts, key=itemgetter("timestamp"))

        if len(posts) > 0:
            logger.trace(f"[@{username}] {posts}")

        return posts

    def GetPost(username: str, postId: int) -> dict:
        """Fetch information for the specified X post."""

        post: dict | None = None

        try:
            res: Response = httpx.get(
                f"https://api.fxtwitter.com/{username}/status/{postId}"
            )

            logger.debug(f"[@{username}] HTTP {res.status_code} GET {res.url}")
            logger.trace(f"[@{username}] {res.text}")

            res.raise_for_status()

            post = res.json()["tweet"]
        except Exception as e:
            logger.opt(exception=e).error(f"[@{username}] Failed to get post {postId}")

        if post:
            logger.trace(f"[@{username}] Found post {postId} {post}")

        return post
