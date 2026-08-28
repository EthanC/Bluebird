<div align="center">

# Bluebird

Bluebird tracks users on X (formerly Twitter) and sends post notifications to Discord.

[![Python 3.14+](https://img.shields.io/badge/Python-3.14%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Build](https://img.shields.io/github/actions/workflow/status/EthanC/Bluebird/workflow.yaml?branch=main&style=flat-square&label=build)](https://github.com/EthanC/Bluebird/actions/workflows/workflow.yaml)

</div>

![A Bluebird post notification in Discord](.github/images/readme_example.png)

## Features

- Track several usernames and webhooks from one process.
- Filter notifications by media, keywords, replies, and reposts.
- Optionally archive notified posts with the Internet Archive Wayback Machine.
- Optionally route X links through a configurable proxy frontend.
- Render posts with Discord components, media galleries, and links back to X.
- Run from the published Docker container or directly with Python and `uv`.

## Docker Compose

Copy `config.example.toml` to `config.toml`, add your usernames and Discord webhook URLs, create a `data` directory, then create `compose.yaml` beside them:

```yaml
services:
  bluebird:
    container_name: bluebird
    image: ghcr.io/ethanc/bluebird:latest
    volumes:
      - ./config.toml:/bluebird/config.toml:ro
      - ./data:/bluebird/data
    restart: unless-stopped
```

Start the container:

```console
docker compose up -d
```

`LOG_DISCORD_WEBHOOK_URL` is optional. It sends Bluebird's own warning and error logs to a separate Discord webhook; post notifications use the webhooks in `config.toml`.

`SERVICE_FAILURE_THRESHOLD` and `SERVICE_DISABLE_SECONDS` are optional and default to `10` consecutive failures and `3600` seconds. The rules apply to every data source, but failures are tracked independently for each service across all X instances. A disabled service permits one recovery request after the configured duration; success restores it, while failure disables it again. An exhausted request, including its configured retries, counts as one failure. Confirmed missing resources do not count as failures.

`SERVICE_DISABLE_ERROR_THRESHOLD` is optional and defaults to `24`. Bluebird emits an error after a service reaches that many consecutive disable periods, allowing `LOG_DISCORD_WEBHOOK_URL` to report a prolonged outage. The initial disable and each failed recovery probe count as one disable period. A successful recovery resets the count.

Each `DISABLE_*` variable is optional and defaults to `false`. Setting one to `true` disables that data source for every X instance. Bluebird logs a critical error and exits if all three data sources are disabled.

Set `archive = true` on an X instance to save each post selected for notification to the Internet Archive Wayback Machine. Internet Archive rejects direct `x.com` captures, so Bluebird archives the corresponding URL on `proxy_host` even when `proxy = false`. Archive failures are logged without blocking the Discord notification.

`INTERNET_ARCHIVE_USERNAME` and `INTERNET_ARCHIVE_PASSWORD` are optional and must be set together. When present, Bluebird requests a screenshot and adds each capture to the account's My Web Archive. Without credentials, archive-enabled instances submit anonymous captures; Internet Archive does not allow screenshots or My Web Archive saves for anonymous requests.

## Python

Python 3.14 or newer and [`uv`](https://docs.astral.sh/uv/) are required.

```console
uv sync
```

Copy `config.example.toml` to `config.toml` and edit it. To configure logging, Internet Archive credentials, or data-source availability, also copy `.env.example` to `.env`. All environment variables are optional.

Run Bluebird from the repository root:

```console
uv run bluebird.py
```

Bluebird creates `data/state.toml` on startup. The first successful profile check records the newest post as the starting cursor; notifications begin with posts published afterward.

## Configuration

Each `[[instances.x]]` table defines one polling loop, one or more usernames, and a destination webhook. Add another table when accounts need a different webhook, schedule, or filter set.

```toml
[instances]

[[instances.x]]
usernames = ["RockstarGames", "CallofDuty"]
discord_webhook_url = "https://discord.com/api/webhooks/XXXXXXXX/XXXXXXXX"
require_keyword = ["trailer", "announcement"]
exclude_reply = true
cooldown = 900
archive = true
proxy = true
```

| Key | Description | Type | Required | Default |
| --- | --- | --- | :---: | --- |
| `usernames` | X usernames to track, without `@` | String array | Yes | None |
| `discord_webhook_url` | Discord webhook that receives post notifications | String | Yes | None |
| `cooldown` | Minimum seconds to wait after all usernames are checked | Number | No | `60` |
| `retries` | Number of retries for each X data-service request | Integer | No | `3` |
| `retry_delay` | Seconds to wait between retries for each X data-service request | Number | No | `5.0` |
| `require_media` | Send only posts that contain media | Boolean | No | `false` |
| `require_keyword` | Send only posts containing at least one listed substring; case-insensitive | String array | No | `[]` |
| `exclude_reply` | Skip replies | Boolean | No | `false` |
| `exclude_repost` | Skip reposts | Boolean | No | `false` |
| `exclude_keyword` | Skip posts containing any listed substring; case-insensitive | String array | No | `[]` |
| `archive` | Save posts selected for notification to the Internet Archive Wayback Machine | Boolean | No | `false` |
| `proxy` | Replace navigational X links with proxy links; excludes media | Boolean | No | `false` |
| `proxy_host` | Hostname used for proxy links and Internet Archive capture targets | String | No | `"twstalker.com"` |
| `proxy_name` | Proxy name used by the outbound button; requires `proxy = true` | String | No | `"TwStalker"` |
