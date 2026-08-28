"""CarryFeed data source for X posts (https://carryfeed.com)."""

from typing import Self

from .fxembed import FxEmbed
from .service import ServiceCircuitBreaker
from .x import XPost


class CarryFeed(FxEmbed):
    """Fetch and normalize X profile feeds with CarryFeed."""

    service_name: str = "CarryFeed"
    api_url: str = "https://carryfeed.com/api"

    def __init__(self: Self, circuit_breaker: ServiceCircuitBreaker) -> None:
        """Initialize the data source with shared service health state."""
        super().__init__(circuit_breaker)

    def fetch_post(self: Self, username: str, post_id: str) -> XPost | None:
        """Return no post because CarryFeed does not expose a single-post route."""
        return None
