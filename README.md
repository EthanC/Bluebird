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

## Environment Variables

Bluebird reads environment variables from the process and an optional `.env` file in the repository root. All environment variables are optional.

| Variable | Description | Type | Required | Default |
| --- | --- | --- | :---: | --- |
| `LOG_LEVEL` | Minimum level written to the console | String | No | `"DEBUG"` |
| `LOG_DISCORD_WEBHOOK_URL` | Discord webhook that receives Bluebird's own logs; post notifications use the webhooks in `config.toml` | URL | No | None |
| `LOG_DISCORD_WEBHOOK_LEVEL` | Minimum level sent to `LOG_DISCORD_WEBHOOK_URL` | String | No | `"WARNING"` |
| `INTERNET_ARCHIVE_EMAIL` | Internet Archive account email address; must be set with `INTERNET_ARCHIVE_PASSWORD` | String | Conditional | None |
| `INTERNET_ARCHIVE_PASSWORD` | Internet Archive account password; must be set with `INTERNET_ARCHIVE_EMAIL` | String | Conditional | None |
| `SERVICE_FAILURE_THRESHOLD` | Consecutive failed requests before a data source is temporarily disabled; retries are counted as one request and confirmed missing resources are excluded | Integer | No | `10` |
| `SERVICE_DISABLE_SECONDS` | Seconds a failed data source remains disabled before Bluebird makes one recovery request | Number | No | `3600` |
| `SERVICE_DISABLE_ERROR_THRESHOLD` | Consecutive disable periods before Bluebird logs an error for a prolonged outage; a successful recovery resets the count | Integer | No | `24` |
| `DISABLE_BETTERTWITFIX` | Disable the BetterTwitFix data source for every X instance | Boolean | No | `false` |
| `DISABLE_FXEMBED` | Disable the FxEmbed data source for every X instance | Boolean | No | `false` |

Service failures are tracked independently for each data source across all X instances. After the disable period, a successful recovery request restores the source and a failed request disables it again. The initial disable and each failed recovery request count toward `SERVICE_DISABLE_ERROR_THRESHOLD`. Bluebird exits if both data sources are disabled.

When Internet Archive credentials are present, Bluebird requests a screenshot and adds captures to the account's My Web Archive. Without credentials, archive-enabled instances submit anonymous captures, which cannot include screenshots or My Web Archive saves.

## Configuration

Each `[[instances.x]]` table defines one polling loop, one or more usernames, and one or more destination webhooks. Add another table when accounts need a different schedule or filter set. Set exactly one of `discord_webhook_url` and `discord_webhook_urls` per instance.

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
| `discord_webhook_url` | Discord webhook that receives post notifications; mutually exclusive with `discord_webhook_urls` | String | Conditional | None |
| `discord_webhook_urls` | Discord webhooks that receive post notifications; mutually exclusive with `discord_webhook_url` | String array | Conditional | None |
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
| `proxy_host` | Hostname used for proxy links and Internet Archive capture targets | String | No | `"nitter.app"` |
| `proxy_name` | Proxy name used by the outbound button; requires `proxy = true` | String | No | `"Nitter"` |

Set `archive = true` on an X instance to save each post selected for notification to the Internet Archive Wayback Machine. Internet Archive rejects direct `x.com` captures, so Bluebird archives the corresponding URL on `proxy_host` even when `proxy = false`. Archive failures are logged without blocking the Discord notification.
