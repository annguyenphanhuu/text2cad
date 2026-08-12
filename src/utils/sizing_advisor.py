"""
Sizing Advisor — calculates recommended server resources from live RuntimeMonitor data.

Usage:
    from src.utils.sizing_advisor import SizingAdvisor, DEFAULT_TARGETS
    advice = SizingAdvisor().recommend(perf_summary, safety=DEFAULT_TARGETS)
"""
from __future__ import annotations

import math
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Default engineering targets (tuneable)
# ---------------------------------------------------------------------------
DEFAULT_TARGETS = {
    # RAM headroom factor above observed peak RSS per worker
    "ram_safety_factor": 1.6,
    # CPU utilization budget per core (keep below this %)
    "cpu_utilization_target_pct": 70.0,
    # Target request concurrency utilization (< 100% so headroom exists)
    "concurrency_utilization_target": 0.70,
    # Minimum number of workers to recommend
    "min_workers": 1,
    # OS + framework baseline overhead added on top of worker RAM
    "os_overhead_mb": 512,
    # P95 latency budget in ms. If exceeded, mark as warning.
    "p95_latency_budget_ms": 120_000,  # 2 minutes
    # Minimum completed requests before we trust sizing data
    "min_sample_count": 3,
}


def _ceil(v: float) -> int:
    return max(1, math.ceil(v))


class SizingAdvisor:
    """Stateless sizing advisor. Pass in a performance summary dict."""

    def recommend(
        self,
        perf: Dict[str, Any],
        safety: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        cfg = {**DEFAULT_TARGETS, **(safety or {})}
        warnings: list[str] = []
        notes: list[str] = []

        sample_count: int = perf.get("sample_count", 0)
        if sample_count < cfg["min_sample_count"]:
            warnings.append(
                f"Only {sample_count} completed request(s) observed. "
                f"Run more load tests (≥ {cfg['min_sample_count']}) for accurate sizing."
            )

        # ------------------------------------------------------------------ #
        # Extract observed metrics
        # ------------------------------------------------------------------ #
        lat = perf.get("latency_ms", {})
        p95_ms: float = lat.get("p95", 0.0)
        p99_ms: float = lat.get("p99", 0.0)
        avg_ms: float = lat.get("avg", 0.0)

        ram = perf.get("ram_mb", {})
        monitor_scope: str = perf.get("monitor_scope") or ram.get("monitor_scope") or "process"
        peak_rss_mb: float = ram.get("peak_rss", 0.0) or 0.0
        avg_rss_mb: float = ram.get("avg_rss", 0.0) or 0.0

        cpu = perf.get("cpu", {})
        avg_cpu_pct: float = cpu.get("avg_process_percent", 0.0) or 0.0
        peak_cpu_pct: float = (cpu.get("peak") or {}).get("process_cpu_percent", 0.0) or 0.0
        cpu_count: int = cpu.get("cpu_count", 1) or 1

        concurrency = perf.get("concurrency", {})
        peak_concurrent: int = concurrency.get("peak_concurrent_requests", 1) or 1

        throughput = perf.get("throughput", {})
        req_per_min: float = throughput.get("req_per_min_in", 0.0) or 0.0

        # ------------------------------------------------------------------ #
        # Workers calculation
        # ------------------------------------------------------------------ #
        # Method: how many workers needed so each one handles ≤ concurrency_target?
        workers_for_concurrency = _ceil(peak_concurrent / cfg["concurrency_utilization_target"])

        # Also make sure we have enough CPU capacity
        if avg_cpu_pct > 0:
            # Estimate CPU cores needed: peak_cpu_pct is per process; scale up
            cpu_cores_needed = _ceil(peak_cpu_pct / cfg["cpu_utilization_target_pct"] * cpu_count)
        else:
            cpu_cores_needed = workers_for_concurrency
            notes.append("CPU data insufficient (all zeros). CPU estimate derived from concurrency.")

        recommended_workers = max(
            cfg["min_workers"],
            workers_for_concurrency,
        )

        # ------------------------------------------------------------------ #
        # RAM calculation
        # ------------------------------------------------------------------ #
        # Per-worker RAM = peak RSS × safety factor (each worker is a separate process)
        if monitor_scope == "system":
            # Whole-server peak already includes OS, API, local workers, Docker
            # overhead, cache, and file IO observed during the load test. Do not
            # multiply that whole-server number by worker count.
            total_ram_mb = round(peak_rss_mb * cfg["ram_safety_factor"], 0) if peak_rss_mb > 0 else 512.0
            ram_per_worker_mb = round(total_ram_mb / recommended_workers, 0)
            notes.append(
                "RAM sizing uses whole-server observed peak, so total RAM is not multiplied by workers."
            )
        else:
            ram_per_worker_mb = round(peak_rss_mb * cfg["ram_safety_factor"], 0) if peak_rss_mb > 0 else 512.0
            total_ram_mb = ram_per_worker_mb * recommended_workers + cfg["os_overhead_mb"]
        total_ram_gb = round(total_ram_mb / 1024, 2)
        ram_per_worker_gb = round(ram_per_worker_mb / 1024, 2)

        # ------------------------------------------------------------------ #
        # CPU cores calculation
        # ------------------------------------------------------------------ #
        recommended_cpu_cores = max(recommended_workers, cpu_cores_needed)

        # ------------------------------------------------------------------ #
        # Latency warnings
        # ------------------------------------------------------------------ #
        if p95_ms > cfg["p95_latency_budget_ms"]:
            warnings.append(
                f"P95 latency ({p95_ms / 1000:.0f}s) exceeds budget "
                f"({cfg['p95_latency_budget_ms'] / 1000:.0f}s). "
                "Consider optimizing slow steps or adding more workers."
            )

        if p99_ms > 0 and p99_ms > p95_ms * 3:
            warnings.append(
                f"P99 ({p99_ms / 1000:.1f}s) is 3× P95 — high tail latency detected. "
                "Investigate outlier requests."
            )

        # ------------------------------------------------------------------ #
        # Throughput capacity estimate
        # ------------------------------------------------------------------ #
        # Theoretical max throughput given workers and avg latency
        if avg_ms > 0:
            max_req_per_min = round(recommended_workers * (60_000 / avg_ms), 1)
        else:
            max_req_per_min = None

        if max_req_per_min and req_per_min > max_req_per_min * 0.9:
            warnings.append(
                f"Current throughput ({req_per_min:.1f} req/min) is near theoretical max "
                f"({max_req_per_min:.1f} req/min). Add more workers."
            )

        # ------------------------------------------------------------------ #
        # Confidence score
        # ------------------------------------------------------------------ #
        confidence = "low"
        if sample_count >= 10:
            confidence = "high"
        elif sample_count >= cfg["min_sample_count"]:
            confidence = "medium"

        # ------------------------------------------------------------------ #
        # Summary
        # ------------------------------------------------------------------ #
        return {
            "confidence": confidence,
            "observed": {
                "sample_count": sample_count,
                "peak_concurrent_requests": peak_concurrent,
                "peak_rss_mb": peak_rss_mb,
                "avg_rss_mb": avg_rss_mb,
                "peak_cpu_percent": peak_cpu_pct,
                "avg_cpu_percent": avg_cpu_pct,
                "p50_latency_ms": lat.get("p50", 0),
                "p95_latency_ms": p95_ms,
                "p99_latency_ms": p99_ms,
                "avg_latency_ms": avg_ms,
                "req_per_min": req_per_min,
                "monitor_scope": monitor_scope,
            },
            "recommendation": {
                "workers": recommended_workers,
                "cpu_cores": recommended_cpu_cores,
                "ram_per_worker_mb": int(ram_per_worker_mb),
                "ram_per_worker_gb": ram_per_worker_gb,
                "total_ram_gb": total_ram_gb,
                "total_ram_mb": int(total_ram_mb),
                "max_throughput_req_per_min": max_req_per_min,
            },
            "targets_used": {
                "ram_safety_factor": cfg["ram_safety_factor"],
                "cpu_utilization_target_pct": cfg["cpu_utilization_target_pct"],
                "concurrency_utilization_target": cfg["concurrency_utilization_target"],
                "p95_latency_budget_ms": cfg["p95_latency_budget_ms"],
            },
            "warnings": warnings,
            "notes": notes,
        }


# Singleton instance
sizing_advisor = SizingAdvisor()
