"""
Runtime monitoring for CAD generation streams.

This module keeps lightweight in-memory metrics for the current API process and host system:
active users, request/session costs, per-step timings, RAM + CPU samples,
throughput counters, and latency percentiles.
It is intentionally DB-free so it can be added without migrations.
"""
from __future__ import annotations

import os
import json
import time
import uuid
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psutil


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _percentile(sorted_values: List[float], p: float) -> float:
    """Return the p-th percentile (0-100) of a *sorted* list."""
    if not sorted_values:
        return 0.0
    idx = (p / 100) * (len(sorted_values) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac, 1)


# ---------------------------------------------------------------------------
# RuntimeMonitor
# ---------------------------------------------------------------------------

class RuntimeMonitor:
    def __init__(
        self,
        max_ram_samples: int = 7200,
        max_completed_requests: int = 500,
        throughput_window_seconds: int = 60,
    ):
        self._lock = threading.RLock()
        self._process = psutil.Process(os.getpid())

        # Prime psutil CPU counter so first real call returns a valid %
        try:
            self._process.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        # RAM / CPU samples
        self._ram_samples: deque = deque(maxlen=max_ram_samples)

        # Requests
        self._active_requests: Dict[str, Dict[str, Any]] = {}
        self._completed_requests: deque = deque(maxlen=max_completed_requests)

        # Session cost accumulators
        self._session_costs: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "session_id": None,
                "user_id": None,
                "total_cost_usd": 0.0,
                "total_tokens": 0,
                "request_count": 0,
                "ram_peak_mb": 0.0,
                "cpu_peak_percent": 0.0,
                "last_step": None,
                "last_duration_ms": None,
                "last_seen_at": None,
                "last_priority": None,
                "max_priority": None,
            }
        )

        # Global peak snapshot.
        #
        # NOTE: The public `process_rss_mb` / `process_cpu_percent` fields are kept
        # for backward compatibility with the existing dashboard, but they now use
        # whole-system values because this deployment runs one project on one server.
        # Raw API-process values are still exposed as `api_process_*`.
        self._global_peak: Dict[str, Any] = {
            "process_rss_mb": 0.0,
            "api_process_rss_mb": 0.0,
            "system_ram_used_mb": 0.0,
            "timestamp": None,
            "active_request_count": 0,
            "active_users": [],
            "active_steps": {},
            "requests": [],
        }

        # Latency: store completed duration_ms values (keep last 1000)
        self._latency_samples: deque = deque(maxlen=1000)

        # Per-step latency: step_name -> deque of duration_ms
        self._step_latency: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))

        # Throughput: timestamps of request starts (for req/min calculation)
        self._request_start_times: deque = deque(maxlen=10000)
        self._request_finish_times: deque = deque(maxlen=10000)
        self._throughput_window = throughput_window_seconds
        self._monitor_log_interval_seconds = 2.0
        self._last_monitor_log_write_ts = 0.0
        self._monitor_log_path = os.path.join(os.getcwd(), "logs", "monitor.jsonl")

        # CPU peak
        self._cpu_peak: Dict[str, Any] = {
            "process_cpu_percent": 0.0,
            "api_process_cpu_percent": 0.0,
            "system_cpu_percent": 0.0,
            "monitor_scope": "system",
            "timestamp": None,
        }

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def new_request_id(self) -> str:
        return f"req_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    def memory_snapshot(self) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        try:
            api_proc_cpu = self._process.cpu_percent(interval=None)
            sys_cpu = psutil.cpu_percent(interval=None)
        except Exception:
            api_proc_cpu = 0.0
            sys_cpu = 0.0
        api_proc_rss_mb = round(self._process.memory_info().rss / (1024 * 1024), 2)
        system_ram_used_mb = round(memory.used / (1024 * 1024), 2)
        return {
            "timestamp": _now_iso(),
            "monitor_scope": "system",
            # Backward-compatible dashboard/sizing fields. In this project they
            # intentionally represent the whole server, not just the API process.
            "process_rss_mb": system_ram_used_mb,
            "process_cpu_percent": round(sys_cpu, 1),
            # Explicit whole-server metrics for UI clarity.
            "server_ram_used_mb": system_ram_used_mb,
            "server_cpu_percent": round(sys_cpu, 1),
            # Explicit raw API process metrics for debugging/comparison.
            "api_process_rss_mb": api_proc_rss_mb,
            "api_process_cpu_percent": round(api_proc_cpu, 1),
            "system_ram_total_mb": round(memory.total / (1024 * 1024), 2),
            "system_ram_used_mb": system_ram_used_mb,
            "system_ram_available_mb": round(memory.available / (1024 * 1024), 2),
            "system_ram_percent": memory.percent,
            "system_cpu_percent": round(sys_cpu, 1),
            "cpu_count": psutil.cpu_count(logical=True) or 1,
        }

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def start_request(
        self,
        request_id: str,
        user_id: str,
        session_id: Optional[str],
        message: str,
        is_edit_request: bool,
        material_choice: Optional[str],
        priority: int = 0,
    ) -> Dict[str, Any]:
        now = _now_iso()
        snap = self.memory_snapshot()
        record = {
            "request_id": request_id,
            "user_id": user_id or "anonymous",
            "session_id": session_id,
            "message_preview": (message or "")[:160],
            "is_edit_request": is_edit_request,
            "material_choice": material_choice,
            "priority": priority,
            "status": "running",
            "current_step": "stream_started",
            "overall_percentage": 0,
            "started_at": now,
            "finished_at": None,
            "last_event_at": now,
            "duration_ms": None,
            "ram_start_mb": snap["process_rss_mb"],
            "ram_peak_mb": snap["process_rss_mb"],
            "ram_end_mb": None,
            "cpu_peak_percent": snap["process_cpu_percent"],
            "total_cost_usd": 0.0,
            "total_tokens": 0,
            "cost_breakdown": None,
            "error": None,
            "steps": [],
            "_current_step": None,
            "_start_time": time.time(),
        }
        with self._lock:
            self._active_requests[request_id] = record
            self._request_start_times.append(time.time())
        self.sample_ram()
        return self._public_request(record)

    def record_update(self, request_id: str, update: Dict[str, Any]) -> None:
        with self._lock:
            record = self._active_requests.get(request_id)
            if not record:
                return

            now = _now_iso()
            record["last_event_at"] = now

            if update.get("session_id"):
                record["session_id"] = update.get("session_id")
                if record.get("user_id") == request_id:
                    record["user_id"] = update.get("session_id")

            if "step" in update:
                self._record_step_update(record, update, now)

            if "final_response" in update:
                final_response = update.get("final_response") or {}
                if final_response.get("session_id"):
                    record["session_id"] = final_response.get("session_id")
                    if record.get("user_id") == request_id:
                        record["user_id"] = final_response.get("session_id")
                total_cost = final_response.get("total_cost_usd") or 0.0
                cost_breakdown = final_response.get("cost_breakdown") or {}
                record["total_cost_usd"] = float(total_cost or 0.0)
                record["cost_breakdown"] = cost_breakdown
                record["total_tokens"] = int(cost_breakdown.get("request_total_tokens") or 0)

            if "error" in update:
                record["status"] = "failed"
                record["error"] = str(update.get("error"))

            snap = self.memory_snapshot()
            record["ram_peak_mb"] = max(record.get("ram_peak_mb") or 0, snap["process_rss_mb"])
            record["cpu_peak_percent"] = max(record.get("cpu_peak_percent") or 0, snap["process_cpu_percent"])

    def finish_request(self, request_id: str, status: str = "completed", error: Optional[str] = None) -> None:
        with self._lock:
            record = self._active_requests.pop(request_id, None)
            if not record:
                return

            now = _now_iso()
            snap = self.memory_snapshot()
            self._close_current_step(record, now, snap["process_rss_mb"])

            if error:
                record["status"] = "failed"
            elif record.get("status") != "failed":
                record["status"] = status

            record["error"] = error or record.get("error")
            record["finished_at"] = now
            elapsed_ms = int((time.time() - record["_start_time"]) * 1000)
            record["duration_ms"] = elapsed_ms
            record["ram_end_mb"] = snap["process_rss_mb"]
            record["ram_peak_mb"] = max(record.get("ram_peak_mb") or 0, snap["process_rss_mb"])
            record["cpu_peak_percent"] = max(record.get("cpu_peak_percent") or 0, snap["process_cpu_percent"])

            public_record = self._public_request(record)
            self._completed_requests.appendleft(public_record)
            self._accumulate_session_cost(public_record)

            # Track latency
            if record.get("status") == "completed":
                self._latency_samples.append(elapsed_ms)
                self._request_finish_times.append(time.time())

            # Track per-step latency from completed steps
            for step in record.get("steps", []):
                step_name = step.get("step_name") or "unknown"
                dur = step.get("duration_ms")
                if dur is not None:
                    self._step_latency[step_name].append(dur)

    def mark_disconnected(self, request_id: str) -> None:
        self.finish_request(request_id, status="client_disconnected")

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def sample_ram(self) -> Dict[str, Any]:
        snap = self.memory_snapshot()
        with self._lock:
            active_records = list(self._active_requests.values())
            active_users = sorted({r.get("user_id") or "anonymous" for r in active_records})
            active_steps: Dict[str, int] = defaultdict(int)
            active_server_counts: Dict[str, int] = defaultdict(int)
            request_rows = []
            for record in active_records:
                step = record.get("current_step") or "unknown"
                server_name = self._server_for_step(step)
                active_steps[step] += 1
                active_server_counts[server_name] += 1
                request_rows.append({
                    "request_id": record.get("request_id"),
                    "user_id": record.get("user_id"),
                    "session_id": record.get("session_id"),
                    "priority": record.get("priority", 0),
                    "current_step": step,
                    "server": server_name,
                    "overall_percentage": record.get("overall_percentage", 0),
                    "status": record.get("status"),
                })

            sample = {
                **snap,
                "active_request_count": len(active_records),
                "active_server_counts": {
                    "chatbot_server": active_server_counts.get("chatbot_server", 0),
                    "freecad_server": active_server_counts.get("freecad_server", 0),
                },
                "active_user_count": len(active_users),
                "active_users": active_users,
                "active_steps": dict(active_steps),
            }
            self._ram_samples.append(sample)
            self._write_monitor_log_sample(sample)

            # Update global peak (by RSS)
            if sample["process_rss_mb"] > self._global_peak.get("process_rss_mb", 0):
                self._global_peak = {
                    "process_rss_mb": sample["process_rss_mb"],
                    "api_process_rss_mb": sample.get("api_process_rss_mb", 0.0),
                    "system_ram_used_mb": sample.get("system_ram_used_mb", sample["process_rss_mb"]),
                    "server_ram_used_mb": sample.get("server_ram_used_mb", sample["process_rss_mb"]),
                    "monitor_scope": sample.get("monitor_scope", "system"),
                    "timestamp": sample["timestamp"],
                    "active_request_count": sample["active_request_count"],
                    "active_server_counts": sample["active_server_counts"],
                    "active_user_count": sample["active_user_count"],
                    "active_users": active_users,
                    "active_steps": dict(active_steps),
                    "requests": request_rows,
                }

            # Update CPU peak
            if sample["process_cpu_percent"] > self._cpu_peak.get("process_cpu_percent", 0):
                self._cpu_peak = {
                    "process_cpu_percent": sample["process_cpu_percent"],
                    "api_process_cpu_percent": sample.get("api_process_cpu_percent", 0.0),
                    "system_cpu_percent": sample["system_cpu_percent"],
                    "server_cpu_percent": sample.get("server_cpu_percent", sample["process_cpu_percent"]),
                    "monitor_scope": sample.get("monitor_scope", "system"),
                    "timestamp": sample["timestamp"],
                }

        return sample

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_overview(self) -> Dict[str, Any]:
        sample = self.sample_ram()
        with self._lock:
            active_records = [self._public_request(r) for r in self._active_requests.values()]
            completed_records = list(self._completed_requests)
            total_cost = sum(r.get("total_cost_usd") or 0.0 for r in completed_records)
            active_users = sorted({r.get("user_id") or "anonymous" for r in active_records})
            return {
                "timestamp": sample["timestamp"],
                "active_user_count": len(active_users),
                "active_request_count": len(active_records),
                "completed_request_count": len(completed_records),
                "total_recorded_cost_usd": round(total_cost, 6),
                "current_ram": sample,
                "active_server_counts": sample.get("active_server_counts", {"chatbot_server": 0, "freecad_server": 0}),
                "global_ram_peak": self._global_peak,
                "cpu_peak": self._cpu_peak,
                "top_ram_samples": self.get_top_ram_samples(limit=10),
                "active_requests": active_records,
                "recent_completed_requests": completed_records[:20],
                "sessions": self.get_sessions(limit=50),
                "session_groups": self.get_session_groups(limit=30),
            }

    def get_ram_series(self, limit: int = 300) -> Dict[str, Any]:
        self.sample_ram()
        with self._lock:
            samples = list(self._ram_samples)[-limit:]
            return {
                "samples": samples,
                "global_ram_peak": self._global_peak,
                "cpu_peak": self._cpu_peak,
                "top_ram_samples": self.get_top_ram_samples(limit=10),
            }

    def get_requests(self, limit: int = 100) -> Dict[str, Any]:
        with self._lock:
            return {
                "active": [self._public_request(r) for r in self._active_requests.values()],
                "completed": list(self._completed_requests)[:limit],
            }

    def get_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        sessions = list(self._session_costs.values())
        sessions.sort(key=lambda item: item.get("last_seen_at") or "", reverse=True)
        return sessions[:limit]

    def get_top_ram_samples(self, limit: int = 10) -> List[Dict[str, Any]]:
        samples = list(self._ram_samples)
        samples.sort(key=lambda item: item.get("process_rss_mb") or 0.0, reverse=True)
        return samples[:limit]

    def get_performance_summary(self) -> Dict[str, Any]:
        """Return latency percentiles, throughput, and CPU stats."""
        with self._lock:
            # --- Latency ---
            latencies = sorted(self._latency_samples)
            p50 = _percentile(latencies, 50)
            p90 = _percentile(latencies, 90)
            p95 = _percentile(latencies, 95)
            p99 = _percentile(latencies, 99)
            avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
            min_latency = latencies[0] if latencies else 0.0
            max_latency = latencies[-1] if latencies else 0.0

            # --- Throughput (req/min over last window) ---
            now_ts = time.time()
            window = self._throughput_window
            recent_starts = [t for t in self._request_start_times if now_ts - t <= window]
            recent_finishes = [t for t in self._request_finish_times if now_ts - t <= window]
            req_per_min_in = round(len(recent_starts) / (window / 60), 2)
            req_per_min_out = round(len(recent_finishes) / (window / 60), 2)

            # --- Per-step latency ---
            step_stats = {}
            for step_name, dur_deque in self._step_latency.items():
                durs = sorted(dur_deque)
                if not durs:
                    continue
                step_stats[step_name] = {
                    "count": len(durs),
                    "avg_ms": round(sum(durs) / len(durs), 1),
                    "p50_ms": _percentile(durs, 50),
                    "p95_ms": _percentile(durs, 95),
                    "p99_ms": _percentile(durs, 99),
                    "max_ms": durs[-1],
                }

            # --- CPU ---
            cpu_samples = [s.get("process_cpu_percent", 0) for s in self._ram_samples]
            sys_cpu_samples = [s.get("system_cpu_percent", 0) for s in self._ram_samples]
            avg_cpu = round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else 0.0
            avg_sys_cpu = round(sum(sys_cpu_samples) / len(sys_cpu_samples), 1) if sys_cpu_samples else 0.0

            # --- RAM ---
            rss_samples = [s.get("process_rss_mb", 0) for s in self._ram_samples]
            avg_rss = round(sum(rss_samples) / len(rss_samples), 2) if rss_samples else 0.0
            peak_rss = max(rss_samples) if rss_samples else 0.0

            # --- Concurrent peak ---
            concurrent_samples = [s.get("active_request_count", 0) for s in self._ram_samples]
            peak_concurrent = max(concurrent_samples) if concurrent_samples else 0

            return {
                "sample_count": len(latencies),
                "monitor_scope": "system",
                "latency_ms": {
                    "avg": avg_latency,
                    "min": min_latency,
                    "p50": p50,
                    "p90": p90,
                    "p95": p95,
                    "p99": p99,
                    "max": max_latency,
                },
                "throughput": {
                    "req_per_min_in": req_per_min_in,
                    "req_per_min_out": req_per_min_out,
                    "window_seconds": window,
                },
                "cpu": {
                    "avg_process_percent": avg_cpu,
                    "avg_system_percent": avg_sys_cpu,
                    "peak": self._cpu_peak,
                    "cpu_count": psutil.cpu_count(logical=True) or 1,
                },
                "ram_mb": {
                    "avg_rss": avg_rss,
                    "peak_rss": peak_rss,
                    "global_peak": self._global_peak,
                    "monitor_scope": "system",
                },
                "concurrency": {
                    "peak_concurrent_requests": peak_concurrent,
                    "current_active": len(self._active_requests),
                },
                "step_latency": step_stats,
            }

    def get_export_data(self) -> Dict[str, Any]:
        """Export monitor log samples in the same compact format as logs/monitor.jsonl."""
        with self._lock:
            current_sample = self.sample_ram()
            samples = self._read_monitor_log_samples()
            current_record = self._compact_monitor_log_record(current_sample)
            if not samples or samples[-1].get("timestamp") != current_record.get("timestamp"):
                samples.append(current_record)

            return {
                "exported_at": _now_iso(),
                "sample_interval_seconds": 2,
                "source": "logs/monitor.jsonl",
                "samples": samples,
            }

    def get_session_groups(self, limit: int = 50) -> List[Dict[str, Any]]:
        active_records = [self._public_request(r) for r in self._active_requests.values()]
        completed_records = list(self._completed_requests)
        groups: Dict[str, Dict[str, Any]] = {}

        def ensure_group(session_id: str, user_id: str = None) -> Dict[str, Any]:
            if session_id not in groups:
                groups[session_id] = {
                    "session_id": session_id,
                    "user_id": user_id or "anonymous",
                    "status": "completed",
                    "request_count": 0,
                    "active_request_count": 0,
                    "completed_request_count": 0,
                    "total_duration_ms": 0,
                    "total_cost_usd": 0.0,
                    "total_tokens": 0,
                    "ram_peak_mb": 0.0,
                    "cpu_peak_percent": 0.0,
                    "last_priority": None,
                    "max_priority": None,
                    "active_priority": None,
                    "priority_tracker": {
                        "active": [],
                        "completed": [],
                        "highest_active": None,
                        "highest_seen": None,
                    },
                    "current_step": None,
                    "last_seen_at": None,
                    "requests": [],
                }
            return groups[session_id]

        def add_record(record: Dict[str, Any], is_active: bool) -> None:
            session_id = record.get("session_id") or record.get("request_id")
            group = ensure_group(session_id, record.get("user_id"))
            group["user_id"] = record.get("user_id") or group["user_id"]
            group["request_count"] += 1
            group["active_request_count"] += 1 if is_active else 0
            group["completed_request_count"] += 0 if is_active else 1
            group["total_duration_ms"] += int(record.get("duration_ms") or 0)
            group["total_cost_usd"] += float(record.get("total_cost_usd") or 0.0)
            group["total_tokens"] += int(record.get("total_tokens") or 0)
            priority = record.get("priority")
            if priority is not None:
                try:
                    priority = int(priority)
                except (TypeError, ValueError):
                    priority = None
            if priority is not None:
                bucket = "active" if is_active else "completed"
                group["priority_tracker"][bucket].append(priority)
                group["last_priority"] = priority
                if is_active:
                    group["active_priority"] = priority
                group["max_priority"] = (
                    priority
                    if group.get("max_priority") is None
                    else max(int(group["max_priority"]), priority)
                )
            group["ram_peak_mb"] = max(float(group["ram_peak_mb"] or 0.0), float(record.get("ram_peak_mb") or 0.0))
            group["cpu_peak_percent"] = max(
                float(group["cpu_peak_percent"] or 0.0),
                float(record.get("cpu_peak_percent") or 0.0),
            )
            group["last_seen_at"] = max(
                [v for v in [group.get("last_seen_at"), record.get("finished_at"), record.get("last_event_at")] if v],
                default=None,
            )
            if is_active:
                group["status"] = "running"
                group["current_step"] = record.get("current_step")
            elif not group.get("current_step"):
                group["current_step"] = record.get("current_step")

            group["requests"].append({
                "request_id": record.get("request_id"),
                "status": record.get("status"),
                "priority": record.get("priority", 0),
                "current_step": record.get("current_step"),
                "overall_percentage": record.get("overall_percentage", 0),
                "duration_ms": record.get("duration_ms"),
                "total_cost_usd": record.get("total_cost_usd", 0.0),
                "total_tokens": record.get("total_tokens", 0),
                "ram_peak_mb": record.get("ram_peak_mb", 0.0),
                "cpu_peak_percent": record.get("cpu_peak_percent", 0.0),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
                "message_preview": record.get("message_preview"),
                "error": record.get("error"),
                "steps": record.get("steps", [])[-8:],
            })

        for record in completed_records:
            add_record(record, is_active=False)
        for record in active_records:
            add_record(record, is_active=True)

        result = list(groups.values())
        for group in result:
            group["total_cost_usd"] = round(group["total_cost_usd"], 6)
            group["ram_peak_mb"] = round(group["ram_peak_mb"], 2)
            group["cpu_peak_percent"] = round(group["cpu_peak_percent"], 1)
            active_priorities = group["priority_tracker"]["active"]
            completed_priorities = group["priority_tracker"]["completed"]
            group["priority_tracker"]["highest_active"] = max(active_priorities) if active_priorities else None
            group["priority_tracker"]["highest_seen"] = max(
                active_priorities + completed_priorities
            ) if active_priorities or completed_priorities else None
            group["requests"].sort(key=lambda item: item.get("started_at") or "")

        result.sort(key=lambda item: item.get("last_seen_at") or "", reverse=True)
        return result[:limit]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _server_for_step(self, step_name: str) -> str:
        return "freecad_server" if step_name == "export" else "chatbot_server"

    def _compact_monitor_log_record(self, sample: Dict[str, Any]) -> Dict[str, Any]:
        counts = sample.get("active_server_counts") or {}
        return {
            "timestamp": sample.get("timestamp"),
            "shared_server": {
                "cpu_percent": sample.get("server_cpu_percent", sample.get("system_cpu_percent", 0.0)),
                "ram_used_mb": sample.get("server_ram_used_mb", sample.get("system_ram_used_mb", 0.0)),
                "ram_total_mb": sample.get("system_ram_total_mb", 0.0),
                "ram_percent": sample.get("system_ram_percent", 0.0),
                "active_requests_total": sample.get("active_request_count", 0),
            },
            "chatbot_server": {
                "active_requests": counts.get("chatbot_server", 0),
            },
            "freecad_server": {
                "active_requests": counts.get("freecad_server", 0),
            },
        }

    def _read_monitor_log_samples(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self._monitor_log_path):
            return []

        samples: List[Dict[str, Any]] = []
        try:
            with open(self._monitor_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        samples.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return []

        return samples

    def _write_monitor_log_sample(self, sample: Dict[str, Any]) -> None:
        now_ts = time.time()
        if now_ts - self._last_monitor_log_write_ts < self._monitor_log_interval_seconds:
            return

        record = self._compact_monitor_log_record(sample)

        try:
            os.makedirs(os.path.dirname(self._monitor_log_path), exist_ok=True)
            with open(self._monitor_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._last_monitor_log_write_ts = now_ts
        except Exception:
            # Monitoring logs must never break API requests.
            pass

    def _record_step_update(self, record: Dict[str, Any], update: Dict[str, Any], now: str) -> None:
        step_name = update.get("step") or "unknown"
        snap = self.memory_snapshot()
        current = record.get("_current_step")

        if not current or current.get("step_name") != step_name:
            self._close_current_step(record, now, snap["process_rss_mb"])
            current = {
                "step_name": step_name,
                "status": update.get("status", "Processing..."),
                "started_at": now,
                "finished_at": None,
                "duration_ms": None,
                "is_complete": False,
                "overall_percentage": update.get("overall_percentage", update.get("progress", 0)) or 0,
                "ram_start_mb": snap["process_rss_mb"],
                "ram_peak_mb": snap["process_rss_mb"],
                "ram_end_mb": None,
            }
            record["steps"].append(current)
            record["_current_step"] = current

        current["status"] = update.get("status", current.get("status"))
        current["overall_percentage"] = update.get(
            "overall_percentage",
            update.get("progress", current.get("overall_percentage", 0)),
        ) or 0
        current["ram_peak_mb"] = max(current.get("ram_peak_mb") or 0, snap["process_rss_mb"])

        record["current_step"] = step_name
        record["overall_percentage"] = current["overall_percentage"]

        if update.get("is_complete") is True:
            self._close_current_step(record, now, snap["process_rss_mb"])

    def _close_current_step(self, record: Dict[str, Any], now: str, ram_end_mb: float) -> None:
        current = record.get("_current_step")
        if not current or current.get("finished_at"):
            return
        current["finished_at"] = now
        current["is_complete"] = True
        current["ram_end_mb"] = ram_end_mb
        current["ram_peak_mb"] = max(current.get("ram_peak_mb") or 0, ram_end_mb)
        try:
            started = datetime.fromisoformat(current["started_at"].replace("Z", "+00:00"))
            finished = datetime.fromisoformat(now.replace("Z", "+00:00"))
            current["duration_ms"] = int((finished - started).total_seconds() * 1000)
        except Exception:
            current["duration_ms"] = None

    def _accumulate_session_cost(self, record: Dict[str, Any]) -> None:
        session_id = record.get("session_id")
        if not session_id:
            return
        session = self._session_costs[session_id]
        session["session_id"] = session_id
        session["user_id"] = record.get("user_id")
        session["total_cost_usd"] = round(
            float(session.get("total_cost_usd") or 0.0) + float(record.get("total_cost_usd") or 0.0),
            6,
        )
        session["total_tokens"] = int(session.get("total_tokens") or 0) + int(record.get("total_tokens") or 0)
        session["request_count"] = int(session.get("request_count") or 0) + 1
        session["ram_peak_mb"] = round(
            max(float(session.get("ram_peak_mb") or 0.0), float(record.get("ram_peak_mb") or 0.0)),
            2,
        )
        session["cpu_peak_percent"] = round(
            max(float(session.get("cpu_peak_percent") or 0.0), float(record.get("cpu_peak_percent") or 0.0)),
            1,
        )
        session["last_step"] = record.get("current_step")
        session["last_duration_ms"] = record.get("duration_ms")
        session["last_seen_at"] = record.get("finished_at") or record.get("last_event_at")
        priority = record.get("priority")
        if priority is not None:
            try:
                priority = int(priority)
            except (TypeError, ValueError):
                priority = None
        if priority is not None:
            session["last_priority"] = priority
            session["max_priority"] = (
                priority
                if session.get("max_priority") is None
                else max(int(session["max_priority"]), priority)
            )

    def _public_request(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in record.items() if not key.startswith("_")}


runtime_monitor = RuntimeMonitor()
