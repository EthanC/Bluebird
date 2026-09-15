"""Prepare the writable volume and run Bluebird without root privileges."""

import os
import sys
from pathlib import Path

DEFAULT_ID = 1000
MAX_ID = 2_147_483_647
DATA_PATH = Path("/bluebird/data")
chown = getattr(os, "chown")
getegid = getattr(os, "getegid")
geteuid = getattr(os, "geteuid")
setgid = getattr(os, "setgid")
setgroups = getattr(os, "setgroups")
setuid = getattr(os, "setuid")


def _read_id(name: str) -> int:
    """Read and validate a container user or group ID."""
    raw_value: str = os.environ.get(name, str(DEFAULT_ID))

    if not raw_value.isascii() or not raw_value.isdecimal():
        raise ValueError(f"{name} must be an integer between 1 and {MAX_ID}")

    value: int = int(raw_value)
    if not 1 <= value <= MAX_ID:
        raise ValueError(f"{name} must be an integer between 1 and {MAX_ID}")

    return value


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    """Set volume ownership without following links outside the volume."""
    path.mkdir(parents=True, exist_ok=True)

    for root, directories, files in os.walk(path):
        root_path = Path(root)
        chown(root_path, uid, gid, follow_symlinks=False)

        for name in directories + files:
            chown(root_path / name, uid, gid, follow_symlinks=False)


def main() -> int:
    """Apply runtime IDs, drop privileges, and execute the requested command."""
    try:
        uid: int = _read_id("PUID")
        gid: int = _read_id("PGID")

        if geteuid() == 0:
            _chown_tree(DATA_PATH, uid, gid)
            setgroups([])
            setgid(gid)
            setuid(uid)
        elif uid != geteuid() or gid != getegid():
            raise PermissionError("PUID and PGID cannot be changed by a non-root user")
    except (OSError, ValueError) as error:
        print(f"entrypoint: {error}", file=sys.stderr)
        return 1

    if not sys.argv[1:]:
        print("entrypoint: no command specified", file=sys.stderr)
        return 1

    os.execvp(sys.argv[1], sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
