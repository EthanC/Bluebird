# Bluebird

Bluebird is a Twitter username-watching service notifies the user via Discord.

<p align="center">
    <img src="https://i.imgur.com/4VYfaJH.png" width="650px" draggable="false">
</p>

## Requirements

-   [Python 3.8](https://www.python.org/downloads/)
-   [HTTPX](https://www.python-httpx.org/)
-   [python-twitter]()
-   [coloredlogs](https://pypi.org/project/coloredlogs/)

[Twitter API credentials](https://developer.twitter.com/en/apps) are required to check username availability.

## Usage

Open `configuration_example.json` in your preferred text editor, fill the configurable values. Once finished, save and rename the file to `configuration.json`.

Bluebird is designed to be ran using a scheduler, such as [cron](https://en.wikipedia.org/wiki/Cron).

```
python bluebird.py
```
