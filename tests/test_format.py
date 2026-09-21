from core.format import Format


def test_escapes_discord_markdown() -> None:
    assert Format.escape_markdown("**bold** [link](url)!") == (
        r"\*\*bold\*\* \[link\]\(url\)\!"
    )


def test_formats_x_entities_and_urls() -> None:
    formatted = Format.x_text(
        "Hello @user, see #news and $BLUE https://x.com/user/status/1",
        "https://nitter.example/",
        rewrite_urls=True,
    )

    assert formatted is not None
    assert "[@user](https://nitter.example/user)" in formatted
    assert "[#news](https://nitter.example/hashtag/news)" in formatted
    assert "[$BLUE](https://nitter.example/search?q=%24BLUE)" in formatted
    assert "https://nitter.example/user/status/1" in formatted


def test_handles_empty_and_truncated_text() -> None:
    assert Format.x_text(None, "https://x.com/") is None
    assert Format.x_text("   ", "https://x.com/") is None
    assert Format.x_text("@user and more", "https://x.com/", max_length=6) == "@us..."
    assert Format.x_text("abcdef", "https://x.com/", max_length=3) == "..."
    assert Format.x_text("abcdef", "https://x.com/", max_length=0) == ""


def test_rewrites_only_x_urls() -> None:
    assert (
        Format.x_url("https://twitter.com/user/status/1?ref=x", "https://proxy.test/")
        == "https://proxy.test/user/status/1?ref=x"
    )
    assert Format.x_url("https://example.com/path", "https://proxy.test/") == (
        "https://example.com/path"
    )
    invalid = "https://[invalid/path"
    assert Format.x_url(invalid, "https://proxy.test/") == invalid
