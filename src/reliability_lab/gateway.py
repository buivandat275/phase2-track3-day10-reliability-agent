from __future__ import annotations

import time
from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


class RouteReason:
    """Detailed route string that remains compatible with legacy route checks."""

    def __init__(self, value: str):
        self.value = value

    @property
    def base(self) -> str:
        return self.value.split(":", 1)[0]

    def startswith(self, prefix: str) -> bool:
        return self.value.startswith(prefix)

    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
        return self.value.split(sep, maxsplit)

    def __contains__(self, needle: object) -> bool:
        return isinstance(needle, str) and needle in self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or (":" not in other and self.base == other)
        if isinstance(other, RouteReason):
            return self.value == other.value
        return False

    def __hash__(self) -> int:
        return hash(self.base)

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return repr(self.value)


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: RouteReason
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response or a static fallback."""
        start = time.perf_counter()
        if self.cache is not None:
            cached, score = self.cache.get(prompt)
            if cached is not None:
                latency_ms = (time.perf_counter() - start) * 1000
                return GatewayResponse(cached, RouteReason(f"cache_hit:{score:.2f}"), None, True, latency_ms, 0.0)

        last_error: str | None = None
        skipped_open: list[str] = []
        for provider in self.providers:
            breaker = self.breakers[provider.name]
            try:
                response: ProviderResponse = breaker.call(provider.complete, prompt)
                if self.cache is not None:
                    self.cache.set(prompt, response.text, {"provider": provider.name})
                role = "primary" if provider == self.providers[0] else "fallback"
                route = f"{role}:{provider.name}"
                if skipped_open:
                    route = f"{route}:after_open={','.join(skipped_open)}"
                latency_ms = (time.perf_counter() - start) * 1000
                return GatewayResponse(
                    text=response.text,
                    route=RouteReason(route),
                    provider=provider.name,
                    cache_hit=False,
                    latency_ms=latency_ms,
                    estimated_cost=response.estimated_cost,
                )
            except CircuitOpenError as exc:
                last_error = str(exc)
                skipped_open.append(provider.name)
                continue
            except ProviderError as exc:
                last_error = f"{provider.name}:{exc}"
                continue

        latency_ms = (time.perf_counter() - start) * 1000
        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route=RouteReason("static_fallback:all_providers_failed"),
            provider=None,
            cache_hit=False,
            latency_ms=latency_ms,
            estimated_cost=0.0,
            error=last_error,
        )
