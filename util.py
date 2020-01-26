import json
import logging
from datetime import datetime

import httpx

log = logging.getLogger(__name__)


class Utility:
    """Class containing utilitarian functions intended to reduce duplicate code."""

    def Webhook(self, url: str, data: dict):
        """POST the provided data to the specified Discord webhook url."""

        headers = {"content-type": "application/json"}
        data = json.dumps(data)

        req = httpx.post(url, headers=headers, data=data)

        return req.status_code

    def nowISO(self):
        """Return the current utc time in ISO8601 timestamp format."""

        return datetime.utcnow().isoformat()

    def ReadFile(self, filename: str, extension: str, directory: str = ""):
        """
        Read and return the contents of the specified file.

        Optionally specify a relative directory.
        """

        try:
            with open(
                f"{directory}{filename}.{extension}", "r", encoding="utf-8"
            ) as file:
                return file.read()
        except Exception as e:
            log.error(f"Failed to read {filename}.{extension}, {e}")
