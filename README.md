# Bluebird

![Python](https://img.shields.io/badge/Python-3-blue?logo=python&logoColor=white)
![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/ethanc/bluebird/workflow.yaml)
![Docker Pulls](https://img.shields.io/docker/pulls/ethanchrisp/bluebird)
![Docker Image Size (tag)](https://img.shields.io/docker/image-size/ethanchrisp/bluebird)

Bluebird tracks users on X (formerly Twitter) and sends post notifications to Discord.

![Example](/.github/images/readme_example.png)

## Features

-   Monitor any public user on X — no login required (thanks to [dylanpdx/BetterTwitFix](https://github.com/dylanpdx/BetterTwitFix)).
-   Get structured alerts with rich Discord Components.
-   Fine-tune alerts using filters like keywords or media-only.
-   Deploy effortlessly with Docker or run locally with Python.

## Getting Started

### Quick Start: Docker Compose

Rename `config.example.toml` to `config.toml` and set your instance configuration(s).

Next, edit and run this example `compose.yaml` with `docker compose up`.

```yaml
services:
    bluebird:
        container_name: bluebird
        image: ethanchrisp/bluebird:latest
        environment:
            LOG_LEVEL: INFO
            LOG_DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/YYYYYYYY/YYYYYYYY
            LOG_DISCORD_WEBHOOK_LEVEL: WARNING
        volumes:
            - /local/path/to/config.toml:/bluebird/config.toml:ro
        restart: unless-stopped
```

### Standalone: Python

> [!NOTE]
> Python 3.13 or later required.

1. Install dependencies.

    ```bash
    uv sync
    ```

2. Rename `.env.example` to `.env` and configure your environment.

3. Rename `config.example.toml` to `config.toml` and set your instance configuration(s).

4. Run Bluebird

    ```bash
    uv run bluebird.py
    ```

### Configuration

Each instance within `config.toml` can be configured to filter posts from sending notifications.

| **Key**               | **Description**                                          | **Type**         | **Required** | **Example**                                         |
| --------------------- | -------------------------------------------------------- | ---------------- | ------------ | --------------------------------------------------- |
| `usernames`           | X usernames to track.                                    | Array of Strings | Yes          | `["RockstarGames", "CallofDuty", "Mxtive"]`         |
| `discord_webhook_url` | Discord Webhook URL to send post notifications to.       | String           | Yes          | `https://discord.com/api/webhook/XXXXXXXX/XXXXXXXX` |
| `require_media`       | Set to `true` to only notify of posts with media.        | Boolean          | No           | `true`                                              |
| `require_keyword`     | Only notify of the post if one of these words are found. | Array of Strings | No           | `["trailer", "new", "announcement", "delay"]`       |
| `exclude_reply`       | Set to `true` to skip posts that are replies.            | Boolean          | No           | `true`                                              |
| `exclude_repost`      | Set to `true` to skip posts that are reposts.            | Boolean          | No           | `true`                                              |
| `exclude_keyword`     | Skip the post if at least one of these words are found.  | Array of Strings | No           | `["store", "price", "shop", "bundle"]`              |
