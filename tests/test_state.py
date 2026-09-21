from pathlib import Path

import pytest

from core.state import StateStore, XCursor


def test_cursor_order_handles_numeric_and_text_ids() -> None:
    assert XCursor(2, "1").is_after(XCursor(1, "999"))
    assert XCursor(1, "10").is_after(XCursor(1, "2"))
    assert XCursor(1, "002").sort_key() == XCursor(1, "2").sort_key()
    assert XCursor(1, "abc").is_after(XCursor(1, "abd")) is False
    assert XCursor(1, "1").is_after(XCursor(1, "abc"))


def test_store_persists_monotonic_cursors(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.toml"
    store = StateStore(path)
    assert path.read_text(encoding="utf-8") == "[x]\n"

    store.set('instance"one', "ExampleUser", XCursor(10, "20"))
    store.set('instance"one', "exampleuser", XCursor(9, "99"))
    assert store.get('instance"one', "EXAMPLEUSER") == XCursor(10, "20")

    store.set('instance"one', "exampleuser", XCursor(9, "line\nbreak"), force=True)
    reloaded = StateStore(path)
    assert reloaded.get('instance"one', "ExampleUser") == XCursor(9, "line\nbreak")


@pytest.mark.parametrize("cursor", [XCursor(0, "1"), XCursor(1, ""), XCursor(-1, "1")])
def test_store_rejects_invalid_cursors(tmp_path: Path, cursor: XCursor) -> None:
    store = StateStore(tmp_path / "state.toml")

    with pytest.raises(ValueError, match="positive timestamp"):
        store.set("instance", "user", cursor)


@pytest.mark.parametrize(
    "content",
    [
        "not toml",
        "name = 'missing x table'",
        "x = []",
        "[x.instance]\nuser = 'invalid'",
        "[x.instance.user]\ncreated_at = 0\npost_id = '1'",
        "[x.instance.user]\ncreated_at = 1\npost_id = ''",
    ],
)
def test_store_rejects_invalid_state(tmp_path: Path, content: str) -> None:
    path = tmp_path / "state.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="(Failed to load|Invalid X)"):
        StateStore(path)
