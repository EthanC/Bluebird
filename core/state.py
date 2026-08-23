"""Persist X monitoring cursors."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class XCursor:
    """Identify the last processed X post in chronological order."""

    created_at: int
    post_id: str

    def sort_key(self) -> tuple[int, int, str]:
        """Return a key that orders same-second posts by ID."""
        if self.post_id.isdigit():
            numeric_id: str = self.post_id.lstrip("0") or "0"

            return (self.created_at, 1, f"{len(numeric_id):010d}:{numeric_id}")

        return (self.created_at, 0, self.post_id)

    def is_after(self, other: "XCursor") -> bool:
        """Return whether this cursor follows another cursor."""
        return self.sort_key() > other.sort_key()


class StateStore:
    """Store per-instance X cursors in an atomic TOML file."""

    def __init__(self, path: Path) -> None:
        """Load cursor state from a path if it exists."""
        self.path: Path = path
        self._lock: RLock = RLock()
        self._cursors: dict[str, dict[str, XCursor]] = {}

        if path.exists():
            self._load()
        else:
            self._write()

    def get(self, instance: str, username: str) -> XCursor | None:
        """Return the cursor for an instance and username."""
        with self._lock:
            return self._cursors.get(instance, {}).get(username.casefold())

    def set(
        self, instance: str, username: str, cursor: XCursor, *, force: bool = False
    ) -> None:
        """Persist a cursor if it advances the current state."""
        if cursor.created_at <= 0 or not cursor.post_id:
            raise ValueError("X cursor must contain a positive timestamp and post ID")

        with self._lock:
            instance_state: dict[str, XCursor] = self._cursors.setdefault(instance, {})
            key: str = username.casefold()
            current: XCursor | None = instance_state.get(key)

            if not force and current and not cursor.is_after(current):
                return

            instance_state[key] = cursor
            self._write()

    def _load(self) -> None:
        """Read and validate persisted state."""
        try:
            with self.path.open("rb") as file:
                data: Any = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"Failed to load state file {self.path}") from error

        instances: Any = data.get("x")

        if not isinstance(instances, dict):
            raise ValueError(f"Invalid X state in {self.path}")

        for instance, users in instances.items():
            if not isinstance(instance, str) or not isinstance(users, dict):
                raise ValueError(f"Invalid X instance state in {self.path}")

            parsed_users: dict[str, XCursor] = {}

            for username, raw_cursor in users.items():
                if not isinstance(username, str) or not isinstance(raw_cursor, dict):
                    raise ValueError(f"Invalid X user state in {self.path}")

                created_at: Any = raw_cursor.get("created_at")
                post_id: Any = raw_cursor.get("post_id")

                if (
                    isinstance(created_at, bool)
                    or not isinstance(created_at, int)
                    or created_at <= 0
                    or not isinstance(post_id, str)
                    or not post_id
                ):
                    raise ValueError(f"Invalid X cursor state in {self.path}")

                parsed_users[username.casefold()] = XCursor(created_at, post_id)

            self._cursors[instance] = parsed_users

    def _write(self) -> None:
        """Atomically write all cursor state."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path = self.path.with_name(f".{self.path.name}.tmp")
        lines: list[str] = ["[x]"]

        for instance, users in sorted(self._cursors.items()):
            for username, cursor in sorted(users.items()):
                lines.extend(
                    (
                        "",
                        f"[x.{_toml_string(instance)}.{_toml_string(username)}]",
                        f"created_at = {cursor.created_at}",
                        f"post_id = {_toml_string(cursor.post_id)}",
                    )
                )

        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(lines))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary, self.path)


def _toml_string(value: str) -> str:
    """Encode a string as a TOML basic string."""
    escaped: list[str] = []
    replacements: dict[str, str] = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }

    for character in value:
        if character in replacements:
            escaped.append(replacements[character])
        elif ord(character) < 0x20:
            escaped.append(f"\\u{ord(character):04X}")
        else:
            escaped.append(character)

    return '"' + "".join(escaped) + '"'
