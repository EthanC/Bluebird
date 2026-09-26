<div align="center">

# Bluebird

Bluebird watches X accounts and sends new posts to Discord webhooks.

[![Python 3.14+](https://img.shields.io/badge/Python-3.14%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Build](https://img.shields.io/github/actions/workflow/status/EthanC/Bluebird/workflow.yaml?branch=main&style=flat-square&label=build)](https://github.com/EthanC/Bluebird/actions/workflows/workflow.yaml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen?style=flat-square)](https://github.com/EthanC/Bluebird/actions/workflows/workflow.yaml)

</div>

![A Bluebird post notification in Discord](.github/images/readme_example.png)

## Features

- Watch multiple accounts from one process.
- Send notifications to one or more Discord webhooks.
- Filter posts by media, keywords, replies, or reposts.
- Archive posts with the Wayback Machine.
- Replace X links with proxy links.
- Run with Docker Compose or Python and [`uv`](https://docs.astral.sh/uv/).

## Docker Compose

Create `config.toml` from `config.example.toml` and `.env` from `.env.example`. Add your accounts and webhooks, create a `data` directory, then save this as `compose.yaml`:

```yaml
services:
  bluebird:
    container_name: bluebird
    image: ghcr.io/ethanc/bluebird:latest
    env_file:
      - .env
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

Install Python 3.14 or newer and [`uv`](https://docs.astral.sh/uv/), then install the dependencies:

```console
uv sync
```

Create and edit `config.toml` from the example, then run Bluebird from the repository root:

```console
uv run bluebird.py
```

Bluebird creates `data/state.toml` on startup. Its first check sets the starting point; only newer posts trigger notifications.

## Environment Variables

Environment variables are optional. Bluebird reads them from the process and from `.env`; `PUID` and `PGID` apply only to the container.

| Variable | Description | Type | Required | Default |
| --- | --- | --- | :---: | --- |
| `PUID` | Container user ID and owner of `data` | Integer | No | `1000` |
| `PGID` | Container group ID and owner of `data` | Integer | No | `1000` |
| `LOG_LEVEL` | Console log level | String | No | `"DEBUG"` |
| `LOG_DISCORD_WEBHOOK_URL` | Webhook for Bluebird logs, not post notifications | URL | No | None |
| `LOG_DISCORD_WEBHOOK_LEVEL` | Discord log level | String | No | `"WARNING"` |
| `INTERNET_ARCHIVE_EMAIL` | Internet Archive email; set with the password | String | Conditional | None |
| `INTERNET_ARCHIVE_PASSWORD` | Internet Archive password; set with the email | String | Conditional | None |
| `ZIGGY_HOST` | Ziggy hostname or IP address with an optional port; set with the identifier | String | Conditional | None |
| `ZIGGY_IDENTIFIER` | Ziggy submission attribution, up to 128 characters | String | Conditional | None |
| `TWSCRAPER_COOKIES` | X `auth_token` and `ct0` cookies | String | No | None |
| `TWS_TELEMETRY` | Set to `0` to disable twscrape telemetry | String | No | `"0"` |
| `SERVICE_FAILURE_THRESHOLD` | Failed requests before a source is disabled | Integer | No | `10` |
| `SERVICE_DISABLE_SECONDS` | Time before retrying a disabled source | Number | No | `3600` |
| `SERVICE_DISABLE_ERROR_THRESHOLD` | Failed recovery periods before an error is logged | Integer | No | `24` |
| `USER_AGENT_BETTERTWITFIX` | User agent sent to BetterTwitFix | String | No | `"https://github.com/EthanC/Bluebird"` |
| `USER_AGENT_CARRYFEED` | User agent sent to CarryFeed | String | No | Chrome 152 on Windows 10 |
| `USER_AGENT_FXEMBED` | User agent sent to FxEmbed | String | No | `"https://github.com/EthanC/Bluebird"` |
| `DISABLE_TWSCRAPER` | Disable Twscraper | Boolean | No | `false` |
| `DISABLE_BETTERTWITFIX` | Disable BetterTwitFix | Boolean | No | `false` |
| `DISABLE_CARRYFEED` | Disable CarryFeed | Boolean | No | `false` |
| `DISABLE_FXEMBED` | Disable FxEmbed | Boolean | No | `false` |

Bluebird queries enabled sources in this order: Twscraper, BetterTwitFix, CarryFeed, then FxEmbed. A source is disabled after `SERVICE_FAILURE_THRESHOLD` failures and retried later; Bluebird exits if every source is unavailable.

## Configuration

Each `[[instances.x]]` table has its own accounts, webhooks, schedule, and filters. Use either `discord_webhook_url` or `discord_webhook_urls`, not both.

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
| `usernames` | X usernames without `@` | String array | Yes | None |
| `discord_webhook_url` | One notification webhook | String | Conditional | None |
| `discord_webhook_urls` | Multiple notification webhooks | String array | Conditional | None |
| `cooldown` | Seconds between completed checks | Number | No | `60` |
| `retries` | Retries per data-source request | Integer | No | `3` |
| `retry_delay` | Seconds between retries | Number | No | `5.0` |
| `require_media` | Require attached media | Boolean | No | `false` |
| `require_keyword` | Require a listed substring; case-insensitive | String array | No | `[]` |
| `exclude_reply` | Skip replies | Boolean | No | `false` |
| `exclude_repost` | Skip reposts | Boolean | No | `false` |
| `exclude_keyword` | Skip a listed substring; case-insensitive | String array | No | `[]` |
| `archive` | Submit notified posts to the configured archive backend | Boolean | No | `false` |
| `proxy` | Replace X links with proxy links | Boolean | No | `false` |
| `proxy_host` | Host used for proxy and archive links | String | No | `"nitter.app"` |
| `proxy_name` | Label for the proxy button | String | No | `"Nitter"` |

Archive requests use `proxy_host` because the Internet Archive rejects direct `x.com` captures. Bluebird submits this proxy URL even when `proxy = false`.

Without Ziggy configuration, Bluebird captures posts directly through the Internet Archive. Credentials enable screenshots and My Web Archive; anonymous captures do not include either. A completed direct capture adds an Internet Archive button to the Discord message.

Set both Ziggy variables to send archive work to its HTTP queue instead:

```dotenv
ZIGGY_HOST=ziggy:9449
ZIGGY_IDENTIFIER=bluebird
```

Bluebird submits to `http://<ZIGGY_HOST>/v1/queue`. Include the port in `ZIGGY_HOST` when Ziggy is not reachable on HTTP port 80. Ziggy takes precedence over Internet Archive credentials. A `202 Accepted` response confirms that Ziggy committed the URL to its queue, not that capture completed, so Bluebird logs the receipt and does not add an Internet Archive button. Bluebird retries network errors, rate limits, and server errors with a 30-second request timeout and honors `Retry-After`. If Ziggy rejects a submission or remains unavailable after the retries, Bluebird logs the failure, updates the post cursor, and continues. It never falls back to a direct capture.

`ZIGGY_IDENTIFIER` records caller attribution; it does not authenticate Bluebird. Ziggy's listener has no authentication and should be reachable only over a trusted network.

## Twscraper Authentication

Twscraper is optional and uses an authenticated X session. BetterTwitFix, CarryFeed, and FxEmbed require no authentication, but they are more likely to return posts late or fail requests. Sign in to X in a browser, then add its `auth_token` and `ct0` cookies to `.env`:

```dotenv
TWSCRAPER_COOKIES="auth_token=...; ct0=..."
DISABLE_TWSCRAPER=false
```

Restart Bluebird after changing the cookies. It stores the session in `data/twscraper.db`; replace both cookies when the session expires.
