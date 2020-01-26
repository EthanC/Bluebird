import json
import logging
from sys import exit

import coloredlogs
import twitter

from util import Utility

log = logging.getLogger(__name__)
coloredlogs.install(level="INFO", fmt="[%(asctime)s] %(message)s", datefmt="%I:%M:%S")


class Bluebird:
    """
    Bluebird is a Twitter username-watching service notifies the user
    via Discord.
    """

    def main(self):
        print("Bluebird - Twitter username-watching service")
        print("https://github.com/EthanC/Bluebird\n")

        initialized = Bluebird.LoadConfiguration(self)

        if initialized is True:
            self.twitter = Bluebird.LoginTwitter(self)

            if self.twitter is not None:
                for username in self.available:
                    available = Bluebird.CheckAvailability(self, username)

                    if available is True:
                        log.info(f"Username @{username} is available, notifying...")

                        Bluebird.Notify(self, username, True)
                    else:
                        log.info(f"Username @{username} is unavailable")
                for username in self.unavailable:
                    available = Bluebird.CheckAvailability(self, username)

                    if available is True:
                        log.info(f"Username @{username} is available")
                    else:
                        log.info(f"Username @{username} is unavailable, notifying...")

                        Bluebird.Notify(self, username, False)

    def LoadConfiguration(self):
        """
        Set the configuration values specified in configuration.json
        
        Return True if configuration sucessfully loaded.
        """

        configuration = json.loads(Utility.ReadFile(self, "configuration", "json"))

        try:
            self.twitterAPIKey = configuration["twitter"]["apiKey"]
            self.twitterAPISecret = configuration["twitter"]["apiSecret"]
            self.twitterAccessToken = configuration["twitter"]["accessToken"]
            self.twitterAccessSecret = configuration["twitter"]["accessSecret"]
            self.avatar = configuration["webhook"]["avatarURL"]
            self.color = configuration["webhook"]["color"]
            self.webhook = configuration["webhook"]["url"]
            self.username = configuration["webhook"]["username"]
            self.available = configuration["usernames"]["available"]
            self.unavailable = configuration["usernames"]["unavailable"]

            log.info("Loaded configuration")

            return True
        except Exception as e:
            log.critical(f"Failed to load configuration, {e}")

    def LoginTwitter(self):
        """
        Authenticate with the Twitter API using the provided
        credentials, return the authenticated client if successful.
        """

        try:
            auth = twitter.Api(
                consumer_key=self.twitterAPIKey,
                consumer_secret=self.twitterAPISecret,
                access_token_key=self.twitterAccessToken,
                access_token_secret=self.twitterAccessSecret,
            )

            auth.VerifyCredentials()
        except Exception as e:
            log.critical(f"Failed to authenticate with Twitter, {e}")

            return

        return auth

    def CheckAvailability(self, username: str):
        """Check the availability of a given Twitter username."""

        try:
            self.twitter.GetUser(screen_name=username)

            return False
        except Exception as e:
            # Hacky solution to python-twitter's lack of exception types,
            # find a better way to do this?
            if str(e) == "[{'code': 50, 'message': 'User not found.'}]":
                return True
            elif str(e) == "[{'code': 63, 'message': 'User has been suspended.'}]":
                return False
            else:
                log.error(
                    f"Unknown error encountered for username @{username}, assuming it's available"
                )

                return True

    def Notify(self, username: str, available: bool):
        """
        Send an availability report to the configured Discord Webhook
        using a Rich Embed.
        """

        if available is True:
            data = {
                "username": self.username,
                "avatar_url": self.avatar,
                "embeds": [
                    {
                        "color": int(self.color, base=16),
                        "author": {
                            "name": "Bluebird",
                            "url": "https://github.com/EthanC/Bluebird",
                            "icon_url": "https://i.imgur.com/eJP9TUo.png",
                        },
                        "description": f"Twitter username [@{username}](https://twitter.com/{username}) is now available",
                        "timestamp": Utility.nowISO(self),
                    }
                ],
            }
        elif available is False:
            data = {
                "username": self.username,
                "avatar_url": self.avatar,
                "embeds": [
                    {
                        "color": int(self.color, base=16),
                        "author": {
                            "name": "Bluebird",
                            "url": "https://github.com/EthanC/Bluebird",
                            "icon_url": "https://i.imgur.com/eJP9TUo.png",
                        },
                        "description": f"Twitter username [@{username}](https://twitter.com/{username}) is now unavailable",
                        "timestamp": Utility.nowISO(self),
                    }
                ],
            }

        status = Utility.Webhook(self, self.webhook, data)

        # HTTP 204 (No Content)
        if status == 204:
            return True
        else:
            log.error(
                f"Failed to notify of availability change for username @{username} (HTTP {status})"
            )


if __name__ == "__main__":
    try:
        Bluebird.main(Bluebird)
    except KeyboardInterrupt:
        exit()
