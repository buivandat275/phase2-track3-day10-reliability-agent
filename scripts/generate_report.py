from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text()))


def _pct_delta(before: float, after: float) -> str:
    if before == 0:
        return "n/a" if after == 0 else f"+{after:.4f}"
    return f"{((after - before) / before) * 100:.1f}%"


def _status(value: bool) -> str:
    return "met" if value else "miss"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--compare", default="reports/metrics_no_cache.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()

    metrics_path = Path(args.metrics)
    metrics = json.loads(metrics_path.read_text())
    no_cache = _load_json(Path(args.compare))

    lines = [
        "# Day 10 Reliability Final Report",
        "",
        "## 1. Architecture summary",
        "",
        "The gateway checks cache first, then sends requests through per-provider circuit breakers in configured order. Failed or open primary providers are skipped quickly, backup providers serve the request, and a static fallback is returned only when every provider path is unavailable.",
        "",
        "```text",
        "User -> ReliabilityGateway -> Cache",
        "                         |-> CircuitBreaker(primary) -> primary provider",
        "                         |-> CircuitBreaker(backup)  -> backup provider",
        "                         |-> static fallback",
        "```",
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        "| failure_threshold | 3 | Opens fast enough for outages while avoiding one-off jitter trips. |",
        "| reset_timeout_seconds | 2 | Allows quick recovery probes during lab-scale provider failures. |",
        "| success_threshold | 1 | A single successful probe closes the breaker for fast recovery. |",
        "| cache TTL | 300 seconds | Good freshness/hit-rate tradeoff for FAQ-style prompts. |",
        "| similarity_threshold | 0.92 | Conservative threshold after adding false-hit checks for year-sensitive prompts. |",
        "| load_test requests | 200 per scenario | Exercises recovery and cache behavior beyond toy traffic. |",
        "",
        "## 3. SLO definitions",
        "",
        "| SLI | SLO target | Actual value | Status |",
        "|---|---|---:|---|",
        f"| Availability | >= 99% | {metrics['availability']} | {_status(float(metrics['availability']) >= 0.99)} |",
        f"| Latency P95 | < 2500 ms | {metrics['latency_p95_ms']} | {_status(float(metrics['latency_p95_ms']) < 2500)} |",
        f"| Fallback success rate | >= 95% | {metrics['fallback_success_rate']} | {_status(float(metrics['fallback_success_rate']) >= 0.95)} |",
        f"| Cache hit rate | >= 10% | {metrics['cache_hit_rate']} | {_status(float(metrics['cache_hit_rate']) >= 0.10)} |",
        f"| Recovery time | < 5000 ms | {metrics['recovery_time_ms']} | {_status(float(metrics['recovery_time_ms'] or 0) < 5000)} |",
        "",
        "## 4. Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        if key != "scenarios":
            lines.append(f"| {key} | {value} |")

    lines += [
        "",
        "## 5. Cache comparison",
        "",
        "| Metric | Without cache | With cache | Delta |",
        "|---|---:|---:|---:|",
    ]
    if no_cache is not None:
        for key in ["latency_p50_ms", "latency_p95_ms", "estimated_cost", "cache_hit_rate"]:
            before = float(no_cache[key])
            after = float(metrics[key])
            lines.append(f"| {key} | {before} | {after} | {_pct_delta(before, after)} |")
    else:
        lines.append("| comparison | not generated | generated | run configs/no_cache.yaml |")

    lines += [
        "",
        "## 6. Redis shared cache",
        "",
        "Shared cache matters because horizontally scaled gateway instances cannot benefit from each other's in-memory entries. `SharedRedisCache` stores query/response hashes with TTL, supports exact lookup plus similarity scan, bypasses privacy-like prompts, and degrades gracefully by returning a cache miss if Redis is unavailable.",
        "",
        "Redis evidence: Docker Compose Redis was started locally and `python -m pytest -q` ran the Redis tests instead of skipping them, covering exact get, TTL expiry, shared state across two instances, privacy bypass, and false-hit logging.",
        "",
        "Shared-state check:",
        "",
        "```text",
        "c1.set('shared report evidence', 'shared response')",
        "c2.get('shared report evidence') -> ('shared response', 1.0)",
        "```",
        "",
        "Redis CLI evidence:",
        "",
        "```text",
        "docker compose exec redis redis-cli KEYS \"rl:cache:*\"",
        "rl:cache:3aff072f3458",
        "```",
        "",
        "## 7. Chaos scenarios",
        "",
        "| Scenario | Expected behavior | Observed status | Pass/Fail |",
        "|---|---|---|---|",
    ]
    expectations = {
        "primary_timeout_100": "Primary opens, backup/cache serves traffic",
        "primary_flaky_50": "Circuit opens during bursts and recovers",
        "all_healthy": "Requests succeed without sustained fallback",
        "cache_stale_candidate": "Different years do not false-hit",
    }
    for name, status in metrics.get("scenarios", {}).items():
        lines.append(f"| {name} | {expectations.get(name, 'Scenario-specific reliability behavior')} | {status} | {status} |")

    lines += [
        "",
        "## 8. Failure analysis",
        "",
        "Remaining weakness: circuit breaker state is still per process. In a real multi-instance deployment, one instance can learn a provider is unhealthy while another continues sending traffic until its local breaker opens. I would move breaker counters and state transitions into Redis or another shared control plane, using atomic increments and short TTLs.",
        "",
        "## 9. Next steps",
        "",
        "1. Add Redis-backed circuit state so all gateway instances share provider health.",
        "2. Add concurrent load execution using the configured concurrency value and include route distribution metrics.",
        "3. Export Prometheus counters for requests, latency, cache hits, fallback usage, and circuit states.",
    ]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
