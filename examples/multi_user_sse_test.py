#!/usr/bin/env python
"""
Run concurrent multi-user SSE tests against /api/generate-cad-stream.

Each virtual user performs two turns:
1. "create sheet 200x200x2 R12 T16"
2. "ok" using the exact session_id returned by turn 1

After all users complete, the script fetches /api/monitor/performance
and /api/monitor/sizing and prints the sizing recommendation.

Usage:
    python examples/multi_user_sse_test.py \\
        --base-url https://dfm-api-preprod.tolery.io \\
        --users 10 \\
        --token <JWT> \\
        --report-json results.json

Token source:
    --token <JWT>
    or API_SECRET_TOKEN / API_TOKEN environment variable
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp


FIRST_PROMPT = "create sheet 200x200x2 R12 T16"
CONFIRM_PROMPT = "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TurnResult:
    message: str
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    final_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    events: int = 0
    steps: List[str] = field(default_factory=list)
    duration_s: float = 0.0


@dataclass
class UserResult:
    user_index: int
    first: TurnResult
    confirm: Optional[TurnResult] = None


# ─────────────────────────────────────────────────────────────────────────────
# SSE turn runner
# ─────────────────────────────────────────────────────────────────────────────

def _build_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/generate-cad-stream"


async def run_sse_turn(
    session: aiohttp.ClientSession,
    base_url: str,
    token: str,
    message: str,
    session_id: Optional[str],
    user_index: int,
    turn_name: str,
    material_choice: str = "STEEL",
    timeout_s: int = 900,
) -> TurnResult:
    result = TurnResult(message=message, session_id=session_id)
    started = time.perf_counter()
    params: Dict[str, Any] = {
        "message": message,
        "is_edit_request": "false",
        "material_choice": material_choice,
        "token": token,
    }
    if session_id:
        params["session_id"] = session_id

    url = _build_url(base_url)
    print(f"[user {user_index:02d}] {turn_name} start | session={session_id or '-'} | message={message!r}")

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout_s)) as response:
            if response.status != 200:
                body = await response.text()
                result.error = f"HTTP {response.status}: {body[:300]}"
                return result

            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue

                payload = line[len("data:"):].strip()
                if not payload:
                    continue

                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    print(f"[user {user_index:02d}] {turn_name} non-json: {payload[:160]}")
                    continue

                result.events += 1

                if data.get("request_id"):
                    result.request_id = data["request_id"]

                if data.get("session_id"):
                    result.session_id = data["session_id"]

                if data.get("step"):
                    step = data["step"]
                    if not result.steps or result.steps[-1] != step:
                        result.steps.append(step)
                    progress = data.get("overall_percentage", data.get("progress", 0))
                    print(f"[user {user_index:02d}] {turn_name} step={step} progress={progress}%")

                if data.get("final_response"):
                    result.final_response = data["final_response"]
                    if data["final_response"].get("session_id"):
                        result.session_id = data["final_response"]["session_id"]
                    break

                if data.get("error"):
                    result.error = data["error"]
                    break

    except Exception as exc:
        result.error = str(exc)
    finally:
        result.duration_s = time.perf_counter() - started

    cost = result.final_response.get("total_cost_usd") if result.final_response else None
    print(
        f"[user {user_index:02d}] {turn_name} done | "
        f"session={result.session_id or '-'} | events={result.events} | "
        f"duration={result.duration_s:.1f}s | cost={cost if cost is not None else '-'} | "
        f"error={result.error or '-'}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Virtual user
# ─────────────────────────────────────────────────────────────────────────────

async def run_virtual_user(
    session: aiohttp.ClientSession,
    base_url: str,
    token: str,
    user_index: int,
    material_choice: str,
    timeout_s: int,
) -> UserResult:
    first = await run_sse_turn(
        session=session, base_url=base_url, token=token,
        message=FIRST_PROMPT, session_id=None,
        user_index=user_index, turn_name="turn1",
        material_choice=material_choice, timeout_s=timeout_s,
    )

    if first.error or not first.session_id:
        if not first.error:
            first.error = "No session_id received from first turn"
        return UserResult(user_index=user_index, first=first)

    confirm = await run_sse_turn(
        session=session, base_url=base_url, token=token,
        message=CONFIRM_PROMPT, session_id=first.session_id,
        user_index=user_index, turn_name="turn2",
        material_choice=material_choice, timeout_s=timeout_s,
    )
    return UserResult(user_index=user_index, first=first, confirm=confirm)


# ─────────────────────────────────────────────────────────────────────────────
# Fetch monitor data after test
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_monitor(
    session: aiohttp.ClientSession,
    base_url: str,
    token: str,
    safety_factor: float = 1.6,
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    base = base_url.rstrip("/")
    out: Dict[str, Any] = {}

    for endpoint, key in [
        (f"{base}/api/monitor/performance", "performance"),
        (f"{base}/api/monitor/sizing?ram_safety_factor={safety_factor}", "sizing"),
        (f"{base}/api/monitor/overview", "overview"),
    ]:
        try:
            async with session.get(endpoint, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    out[key] = await resp.json()
                else:
                    out[key] = {"error": f"HTTP {resp.status}"}
        except Exception as exc:
            out[key] = {"error": str(exc)}

    return out


def _print_sizing(sizing: Dict[str, Any]) -> None:
    sep = "─" * 56
    print(f"\n{sep}")
    print("  🖥️  SERVER SIZING RECOMMENDATION")
    print(sep)
    rec = sizing.get("recommendation", {})
    conf = sizing.get("confidence", "unknown")
    obs = sizing.get("observed", {})

    print(f"  Confidence       : {conf.upper()}")
    print(f"  Workers          : {rec.get('workers', '–')}")
    print(f"  CPU Cores        : {rec.get('cpu_cores', '–')}")
    print(f"  RAM / Worker     : {rec.get('ram_per_worker_mb', '–')} MB  ({rec.get('ram_per_worker_gb', '–')} GB)")
    print(f"  Total RAM        : {rec.get('total_ram_mb', '–')} MB  ({rec.get('total_ram_gb', '–')} GB)")
    max_tput = rec.get("max_throughput_req_per_min")
    if max_tput:
        print(f"  Max Throughput   : ~{max_tput:.1f} req/min")
    print()
    print("  Observed metrics:")
    print(f"    Peak concurrent : {obs.get('peak_concurrent_requests', '–')}")
    print(f"    Peak RAM RSS    : {obs.get('peak_rss_mb', '–')} MB")
    print(f"    Avg RAM RSS     : {obs.get('avg_rss_mb', '–')} MB")
    print(f"    Peak CPU %      : {obs.get('peak_cpu_percent', '–')}%")
    print(f"    P50 latency     : {obs.get('p50_latency_ms', '–')} ms")
    print(f"    P95 latency     : {obs.get('p95_latency_ms', '–')} ms")
    print(f"    P99 latency     : {obs.get('p99_latency_ms', '–')} ms")
    print(f"    Req/min         : {obs.get('req_per_min', '–')}")

    warns = sizing.get("warnings", [])
    if warns:
        print()
        print("  ⚠️  Warnings:")
        for w in warns:
            print(f"    • {w}")

    notes = sizing.get("notes", [])
    if notes:
        print()
        print("  ℹ️  Notes:")
        for n in notes:
            print(f"    • {n}")

    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main_async(args: argparse.Namespace) -> int:
    token = args.token or os.getenv("API_SECRET_TOKEN") or os.getenv("API_TOKEN") or ""
    if not token:
        print("WARNING: No token. Pass --token or set API_SECRET_TOKEN/API_TOKEN.")

    connector = aiohttp.TCPConnector(limit=max(args.users * 2, 20), force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        started = time.perf_counter()
        tasks = [
            run_virtual_user(
                session=session, base_url=args.base_url, token=token,
                user_index=i + 1, material_choice=args.material, timeout_s=args.timeout,
            )
            for i in range(args.users)
        ]
        results = await asyncio.gather(*tasks)
        total_s = time.perf_counter() - started

        # ── Summary ──
        print("\n=== Multi-user SSE summary ===")
        ok_count = 0
        total_cost = 0.0
        for item in results:
            final_turn = item.confirm or item.first
            ok = not item.first.error and (item.confirm is not None and not item.confirm.error)
            ok_count += 1 if ok else 0
            for turn in [item.first, item.confirm]:
                if turn and turn.final_response:
                    total_cost += float(turn.final_response.get("total_cost_usd") or 0.0)
            print(
                f"  user {item.user_index:02d} | "
                f"session={final_turn.session_id or '-'} | "
                f"turn1={item.first.duration_s:.1f}s/{item.first.error or 'ok'} | "
                f"turn2={(item.confirm.duration_s if item.confirm else 0):.1f}s/"
                f"{(item.confirm.error if item.confirm else 'skipped') or 'ok'}"
            )

        print(f"\nUsers completed both turns : {ok_count}/{len(results)}")
        print(f"Total wall time            : {total_s:.1f}s")
        print(f"Returned total cost        : ${total_cost:.6f}")

        # Wait 2 s so monitor can ingest last events
        print("\nWaiting 2 s for monitor to ingest final events…")
        await asyncio.sleep(2)

        # ── Fetch monitor data ──
        print("Fetching sizing recommendation from monitor…")
        monitor = await fetch_monitor(session, args.base_url, token, args.safety_factor)

        sizing = monitor.get("sizing", {})
        if "error" not in sizing:
            _print_sizing(sizing)
        else:
            print(f"\nCould not fetch sizing: {sizing.get('error')}")

        # ── Optional JSON report ──
        if args.report_json:
            report = {
                "run_at": datetime.utcnow().isoformat() + "Z",
                "args": {
                    "base_url": args.base_url,
                    "users": args.users,
                    "material": args.material,
                    "safety_factor": args.safety_factor,
                },
                "summary": {
                    "ok_count": ok_count,
                    "total_users": len(results),
                    "total_wall_s": round(total_s, 2),
                    "total_cost_usd": round(total_cost, 6),
                },
                "monitor": monitor,
                "user_results": [
                    {
                        "user_index": r.user_index,
                        "first": {
                            "duration_s": round(r.first.duration_s, 2),
                            "error": r.first.error,
                            "session_id": r.first.session_id,
                            "steps": r.first.steps,
                            "events": r.first.events,
                            "cost_usd": (r.first.final_response or {}).get("total_cost_usd"),
                        },
                        "confirm": {
                            "duration_s": round(r.confirm.duration_s, 2),
                            "error": r.confirm.error,
                            "steps": r.confirm.steps,
                            "events": r.confirm.events,
                            "cost_usd": (r.confirm.final_response or {}).get("total_cost_usd"),
                        } if r.confirm else None,
                    }
                    for r in results
                ],
            }
            with open(args.report_json, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\nReport saved → {args.report_json}")

    return 0 if ok_count == len(results) else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run N concurrent two-turn SSE CAD users and print sizing advice.")
    p.add_argument("--base-url", default="http://localhost:8124", help="API base URL")
    p.add_argument("--users", type=int, default=5, help="Number of concurrent virtual users (default 5)")
    p.add_argument("--token", default=None, help="API token. Defaults to API_SECRET_TOKEN/API_TOKEN env var.")
    p.add_argument("--material", default="STEEL", help="Material choice query param")
    p.add_argument("--timeout", type=int, default=900, help="Timeout per SSE turn in seconds")
    p.add_argument("--safety-factor", type=float, default=1.6, help="RAM safety factor for sizing (default 1.6)")
    p.add_argument("--report-json", default=None, metavar="FILE", help="Save full report to JSON file")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
