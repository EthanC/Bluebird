"""CarryFeed data source for X posts (https://carryfeed.com)."""

from typing import Self

import niquests
from loguru import logger
from niquests import Response

from .fxembed import FxEmbed
from .retry import RequestRateLimiter, retry_request, retry_transient_request
from .service import ServiceCircuitBreaker

# CarryFeed currently reports a 60-request client limit. Its advertised Cloudflare
# Workers limiter supports 10- or 60-second periods, so use the conservative window.
_RATE_LIMITER = RequestRateLimiter(limit=60, period=60.0)


class CarryFeed(FxEmbed):
    """Fetch and normalize X posts with CarryFeed."""

    service_name: str = "CarryFeed"
    api_url: str = "https://api.carryfeed.com/api"
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    timeline_page_size: int = 40
    supports_timeline_since: bool = False

    def __init__(
        self: Self,
        circuit_breaker: ServiceCircuitBreaker,
        rate_limiter: RequestRateLimiter | None = None,
    ) -> None:
        """Initialize the data source with shared service health state."""
        super().__init__(circuit_breaker)
        self.rate_limiter: RequestRateLimiter = rate_limiter or _RATE_LIMITER

    def _request_post(self: Self, username: str, post_id: str) -> Response:
        """Resolve one X post with CarryFeed."""
        return retry_request(
            lambda: self._get(f"{self.api_url}/post/{post_id}").raise_for_status(),
            self.retries,
            self.retry_delay,
            niquests.RequestException,
            retry_transient_request,
        )

    def _get(self: Self, url: str, params: dict[str, str] | None = None) -> Response:
        """Pace one CarryFeed request and apply its rate-limit response headers."""
        self.rate_limiter.wait()
        response: Response = super()._get(url, params)
        self.rate_limiter.observe(response.headers)

        if response.status_code == 429:
            delay: float = self.rate_limiter.defer(response)
            logger.warning(
                f"{self.service_name} rate limited requests; pausing all callers for "
                f"{delay:g}s (access_class="
                f"{response.headers.get('x-carryfeed-access-class')!r}, "
                f"scope={response.headers.get('x-rate-limit-scope')!r}, "
                f"client_limit={response.headers.get('x-rate-limit-limit')!r}, "
                f"ip_limit={response.headers.get('x-rate-limit-ip-limit')!r}, "
                f"retry_after={response.headers.get('Retry-After')!r})"
            )

        return response
