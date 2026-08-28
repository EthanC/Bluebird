"""CarryFeed data source for X posts (https://carryfeed.com)."""

from typing import Self

import niquests
from niquests import Response

from .fxembed import FxEmbed
from .retry import retry_request, retry_transient_request
from .service import ServiceCircuitBreaker


class CarryFeed(FxEmbed):
    """Fetch and normalize X posts with CarryFeed."""

    service_name: str = "CarryFeed"
    api_url: str = "https://carryfeed.com/api"
    supports_profile_lookup: bool = False
    supports_timeline_parameters: bool = False

    def __init__(self: Self, circuit_breaker: ServiceCircuitBreaker) -> None:
        """Initialize the data source with shared service health state."""
        super().__init__(circuit_breaker)

    def _request_post(self: Self, username: str, post_id: str) -> Response:
        """Resolve one X post with CarryFeed."""
        return retry_request(
            lambda: niquests.get(
                f"{self.api_url}/resolve",
                params={"url": f"https://x.com/{username}/status/{post_id}"},
                headers={"User-Agent": self.user_agent},
                timeout=5,
                allow_redirects=False,
                retries=0,
            ).raise_for_status(),
            self.retries,
            self.retry_delay,
            niquests.RequestException,
            retry_transient_request,
        )
