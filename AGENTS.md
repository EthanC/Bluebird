# AGENTS.md

Bluebird is a long-running Python service. It polls X (Twitter) accounts through several third-party data sources, posts new posts to Discord webhooks, and can archive them. User-facing setup docs are in `README.md`. This file covers what you need to know to change the code safely.

## Commands

The project uses Python 3.15 (pinned in `.python-version`) and uv. Run every command from the repository root.

```console
uv sync                 # install runtime and dev dependencies
uv run bluebird.py      # run locally (needs config.toml; reads .env)
uv run pytest           # tests
uv run ruff check .     # lint (auto-fixes; CI runs with --no-fix)
uv run ruff format .    # format
uv run ty check         # type check
uv lock --check         # lockfile matches pyproject.toml
```

On every push and pull request, CI (`.github/workflows/workflow.yaml`) runs the lockfile check, Ruff lint, the format check, `ty`, and the tests with coverage. Run the same checks before you finish a change, and always run `uv run ruff format .` after your last edit to code or tests. Don't hand-wrap lines. Ruff's collapsing and wrapping decisions, including the 88-column limit, are easy to get wrong by eye, and CI fails on any unformatted file.

- Change dependencies with `uv add`, `uv remove`, or `uv lock --upgrade-package`. Never edit `uv.lock` by hand.
- Ruff enforces LF line endings. On a Windows checkout with `core.autocrlf=true`, `ruff format --check` flags every CRLF file even when its code is formatted. Git normalizes them to LF on commit. Write new files with LF endings. Because of this, a local `ruff format --check .` is noisy on Windows and can't confirm your changes are formatted. Run `uv run ruff format .` instead, then use `git diff --ignore-space-at-eol` to confirm that only intended lines changed.
- `pyproject.toml` sets coverage `fail_under = 100`, but the suite is below that target and CI's coverage step is `continue-on-error`. Plain `uv run pytest` doesn't measure coverage. Add tests for the code you change instead of relying on the gate.
- When the quality job passes on a push to `main`, CI publishes `ghcr.io/ethanc/bluebird:latest` and a SHA-tagged image.

## Layout

```
bluebird.py            Entrypoint: env, logging, source wiring, worker threads, health file, shutdown
docker-entrypoint.py   Container entrypoint: chowns /bluebird/data to PUID/PGID, drops root, execs CMD
core/config.py         Parses and validates config.toml into XConfig; derives each instance's state_key
core/state.py          XCursor and StateStore (data/state.toml, atomic writes)
core/x.py              Source-neutral models, XDataSource protocol, XInstance (poll, filter, notify, archive)
core/format.py         Escapes untrusted X text for Discord, links entities, rewrites X hosts, truncates
core/service.py        ServiceCircuitBreaker and the ServiceFailure/ServiceNotFound/ServiceDeferred signals
core/retry.py          retry_request, retry_transient_request, RequestRateLimiter, Retry-After parsing
core/archive.py        InternetArchiveSession (direct captures) and ZiggyClient (HTTP queue)
core/twscraper.py      Authenticated twscrape source; owns an asyncio loop thread and data/twscraper.db
core/bettertwitfix.py  BetterTwitFix source (api.vxtwitter.com)
core/fxembed.py        FxEmbed source (api.fxtwitter.com)
core/carryfeed.py      CarryFeed source; subclasses FxEmbed and adds shared rate limiting
tests/                 pytest suite, one file per module; test_bluebird.py covers the entrypoint
```

## How it works

- `bluebird.start()` builds the source factories in priority order: Twscraper, BetterTwitFix, CarryFeed, FxEmbed. Each service gets one `ServiceCircuitBreaker`, which every instance shares.
- Each `[[instances.x]]` table gets its own `XInstance`, with fresh source objects, on its own thread (`x-<index>`). Every thread shares the `StateStore`, the archive client, the circuit breakers, the module-level rate limiters, and the `TwscraperRuntime`, so those must stay thread-safe.
- `XInstance.fetch_user` queries every enabled source and merges the feeds by post ID. A feed with `complete=False` can only add posts that are no newer than the newest post in a complete feed. When no feed is complete, Bluebird skips the check.
- For each username, the first successful check only records a cursor. Later checks notify for posts whose `XCursor(created_at, post_id)` comes after the stored cursor. `StateStore.set` only moves a cursor forward unless called with `force=True`.
- The cursor advances past filtered posts. It also advances past delivered posts, even when archiving fails.
- If Discord delivery or a message edit fails, Bluebird raises `RuntimeError` and the worker stops. The main loop then exits the process, the container restarts, and the post is retried. Don't swallow delivery errors.
- Archiving runs after notification. A direct Internet Archive capture edits the delivered message to add an archive button. Ziggy only queues the proxy post URL and each original media URL, and never edits the message. When Ziggy is configured it takes precedence, and neither backend falls back to the other.
- While every worker is alive, the main loop touches `BLUEBIRD_HEALTHCHECK_FILE` once per second. The Docker `HEALTHCHECK` passes only while that file's modification time is recent.

## Invariants and gotchas

- **Twscrape import order.** Importing twscrape replaces all Loguru sinks. `bluebird.py` imports `core.twscraper` lazily: only when Twscraper is enabled, and before `configure_logging()` runs. Don't import `core.twscraper` or twscrape at module level in the entrypoint or in other `core` modules.
- **State keys.** `config._state_key` hashes each instance's webhook IDs, usernames, filters, and proxy settings. If any of those inputs or the hash itself changes, the instance gets a new key and loses its cursors. It then sets a new starting point without notifying, so posts published in the gap are missed. `cooldown`, `retries`, `retry_delay`, and `archive` are deliberately left out of the hash.
- **Untrusted text.** All X text shown in Discord must go through `Format.x_text` or `Format.escape_markdown`. Webhooks are sent with `AllowedMentions(parse=[])`. The `XPost`, `XMedia`, and `XPostReference` constructors validate IDs, usernames, and HTTPS URLs, so build objects through them rather than bypassing their checks.
- **Text limits.** Respect `MAIN_TEXT_MAX_LENGTH`, `RELATED_TEXT_MAX_LENGTH`, and `ALT_TEXT_MAX_LENGTH` in `core/x.py`. Draft.js article offsets count UTF-16 code units, not Python characters.
- **Library spellings.** `Seperator` and `SeperatorSpacing` are spelled that way in discord-clyde. Don't correct them.
- **Python 3.15 syntax.** Unparenthesized `except A, B:` clauses (PEP 758) and PEP 695 type parameters such as `def call[ResultT](...)` are intentional. Don't rewrite them for older Python versions.
- **Secrets.** `config.toml`, `.env`, and `data/` are gitignored because they hold webhook URLs, cookies, and credentials. Never commit them or log cookie values. Never put real webhook URLs, cookies, or account data in tests.

## Data sources

Every source implements the `XDataSource` protocol in `core/x.py` (`retries`, `retry_delay`, `fetch_user`, and `fetch_post`). It returns `XFeed` and `XPost` objects, with `source` set to its service name. Follow the pattern of the existing sources:

- The public `fetch_user` and `fetch_post` methods wrap a private implementation in `self.circuit_breaker.call(...)`.
- The implementation catches expected errors (`niquests.RequestException`, `TypeError`, `ValueError`, and `OverflowError`) and logs them. It then re-raises them as one of these signals:
  - `ServiceNotFound` when the service confirmed that the resource is missing. The breaker counts this as a success.
  - `ServiceFailure` for any other error. This counts toward disabling the service.
- A subclass of `ServiceDeferred` skips the work without affecting the service's health. Any other exception propagates and crashes the worker.
- Send HTTP requests through `retry_request(..., retry_transient_request)`. Pass niquests `retries=0`, `allow_redirects=False`, and an explicit timeout. Use a `RequestRateLimiter` for services that publish rate limits.
- Read the source's user agent from `USER_AGENT_<SERVICE_NAME>`.
- Set `complete=False` when a feed can't be trusted to include every recent post. FxEmbed and CarryFeed feeds are always incomplete. BetterTwitFix feeds are incomplete when items were malformed. Twscraper feeds are incomplete when data was malformed or pagination stopped before reaching the stored cursor. An incomplete feed only fills gaps behind a complete feed.
- Twscraper doesn't follow this HTTP pattern. Its async twscrape calls go through `TwscraperRuntime.run`, which serializes all account use behind one lock. Each call has a 60-second deadline, and time spent waiting for the lock doesn't count toward it.

To add a source:

1. Implement it in `core/`.
2. Add it to the factory loop in `bluebird.py` with a `DISABLE_<NAME>` variable.
3. Document its variables in `README.md` and `.env.example`.

## Configuration changes

`config.py` rejects unknown keys. To add an `[[instances.x]]` key:

1. Add the field to the `XConfig` dataclass.
2. Add the key to the `allowed` set in `_parse_x_config`, along with its validation.
3. Decide whether the key belongs in `_state_key`. It usually doesn't.
4. Wire it into `XInstance.start`.
5. Update the README table and `config.example.toml`.

Document new environment variables in the README table and `.env.example`.

`DEBUG_STATE=<unix timestamp>` is an undocumented debug variable. It force-sets every cursor to that timestamp before each check, so any later post notifies again on every pass. Use it only with test webhooks.

## Code style

- Ruff selects the `I` (isort) and `D` (pydocstyle, PEP 257) rules. Every module, class, and function in `bluebird.py`, `docker-entrypoint.py`, and `core/` needs a one-line docstring in the imperative mood ending in a period. Tests are exempt.
- Ruff formats with `skip-magic-trailing-comma`, so anything that fits on one line gets collapsed onto it.
- Annotate locals as well as signatures (`posts: tuple[XPost, ...] = ...`), and annotate `self: Self`. Use frozen, slotted dataclasses for value types.
- Log with Loguru, using the bracketed prefixes from the `log()` helpers:
  - `X[0][@user][post_id] ...` for instances.
  - `FxEmbed[@user][post_id] ...` for sources.

  Use `logger.opt(exception=e).error(...)` for handled exceptions. Pair `debug` summaries with `trace` dumps of the full data. Use `success` for discovered posts and archive results.
- Tests are plain pytest functions that use `monkeypatch` and small hand-written fakes; `tests/test_x.py` shows the style. Tests must not make network calls, and must keep any files they write inside `tmp_path`.

## Commits

Write commit subjects as `Type(Scope): Summary` in title case and the imperative mood, without a trailing period. For example: `Fix(Twscraper): Decode HTML entities in text`.

- Types: `Feat`, `Fix`, `Refactor`, `Chore`, or `Docs`.
- Scopes name the affected module or area. Existing scopes include `X`, `Format`, `Config`, `Archive`, `Ziggy`, `Twscraper`, `Docker`, `Deps`, `Tests`, and `README`.
- Add a one-line body only when the subject doesn't explain the change on its own.
