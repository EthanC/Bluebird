# Bluebird

![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/EthanC/Bluebird/ci.yml?branch=main) ![Docker Pulls](https://img.shields.io/docker/pulls/ethanchrisp/bluebird?label=Docker%20Pulls) ![Docker Image Size (tag)](https://img.shields.io/docker/image-size/ethanchrisp/bluebird/latest?label=Docker%20Image%20Size)

Bluebird monitors users on X and reports new posts via Discord.

<p align="center">
    <img src="https://i.imgur.com/7r4eMLt.png" draggable="false">
</p>

## Setup

Although not required, a [Discord Webhook](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) is recommended for notifications.

An X account is required. It is recommended to use a throwaway account due to use of the internal API.

**Environment Variables:**

-   `LOG_LEVEL`: [Loguru](https://loguru.readthedocs.io/en/stable/api/logger.html) severity level to write to the console.
-   `LOG_DISCORD_WEBHOOK_URL`: [Discord Webhook](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) URL to receive log events.
-   `LOG_DISCORD_WEBHOOK_LEVEL`: Minimum [Loguru](https://loguru.readthedocs.io/en/stable/api/logger.html) severity level to forward to Discord.
-   `USERS_ALL`: Comma-separated list of [X](https://x.com/) usernames to monitor for all posts.
-   `USERS_TOP`: Comma-separated list of [X](https://x.com/) usernames to monitor for top-level posts only.
-   `USERS_MEDIA`: Comma-separated list of [X](https://x.com/) usernames to monitor for media posts only.
-   `COOLDOWN_MIN_TIME`: Minimum randomized cooldown time between checking for new posts (default is 60).
-   `COOLDOWN_MAX_TIME`: Maximum randomized cooldown time between checking for new posts (default is 300).
-   `X_CSRF_TOKEN`: CSRF Token obtained via request inspection on [X](https://x.com/).
-   `X_AUTH_TOKEN`: Cookie Auth Token obtained via request inspection on [X](https://x.com/).
-   `X_BEARER_TOKEN`: Authentication Bearer Token obtained via request inspection on [X](https://x.com/).
-   `DISCORD_WEBHOOK_URL`: [Discord Webhook](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) URL to receive new post notifications.

### Docker (Recommended)

Modify the following `docker-compose.yml` example file, then run `docker compose up`.

```yml
services:
  bluebird:
    container_name: bluebird
    image: ethanchrisp/bluebird:latest
    environment:
      LOG_LEVEL: INFO
      LOG_DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/YYYYYYYY/YYYYYYYY
      LOG_DISCORD_WEBHOOK_LEVEL: WARNING
      USERS_ALL: Mxtive,spectatorindex,Breaking911
      USERS_TOP: X,XData
      USERS_MEDIA: archillect
      COOLDOWN_MIN_TIME: 60
      COOLDOWN_MAX_TIME: 300
      X_CSRF_TOKEN: XXXXXXXX
      X_AUTH_TOKEN: XXXXXXXX
      X_BEARER_TOKEN: XXXXXXXX
      DISCORD_WEBHOOK_URL: https://discord.com/api/webhooks/XXXXXXXX/XXXXXXXX
    restart: unless-stopped
```

### Standalone

Bluebird is built for [Python 3.12](https://www.python.org/) or greater.

1. Install required dependencies using [uv](https://github.com/astral-sh/uv): `uv sync`
2. Rename `.env.example` to `.env`, then provide the environment variables.
3. Start Bluebird: `python bluebird.py`
