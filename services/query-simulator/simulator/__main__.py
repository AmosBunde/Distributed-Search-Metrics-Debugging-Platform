"""Command line entry point: `python -m simulator --qps 500 --scenario error_spike`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

from .runner import SimulationRunner
from .scenarios import SCENARIOS, get_scenario


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulator",
        description="Generate realistic search traffic against the telemetry collector.",
    )
    parser.add_argument("--qps", type=float, default=100.0, help="Target events per second")
    parser.add_argument(
        "--scenario",
        default="baseline",
        choices=sorted(SCENARIOS),
        help="Traffic pattern to run",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Seconds to run for (default: the scenario's own length)",
    )
    parser.add_argument(
        "--collector",
        default=os.environ.get("COLLECTOR_URL", "http://localhost:8001"),
        help="Base URL of the telemetry collector",
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed for reproducible traffic")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    return parser


async def run(args: argparse.Namespace) -> int:
    scenario = get_scenario(args.scenario)
    endpoint = f"{args.collector.rstrip('/')}/api/v1/telemetry/batch"

    async with httpx.AsyncClient(
        timeout=30.0, headers={"x-client-id": "query-simulator"}
    ) as client:
        try:
            health = await client.get(f"{args.collector.rstrip('/')}/health")
            health.raise_for_status()
        except Exception as exc:
            print(f"Collector is not reachable at {args.collector}: {exc}", file=sys.stderr)
            print("Start the stack first with: make dev", file=sys.stderr)
            return 1

        runner = SimulationRunner(
            client=client,
            endpoint=endpoint,
            scenario=scenario,
            qps=args.qps,
            duration_seconds=args.duration,
            seed=args.seed,
        )
        stats = await runner.run()

    print(f"\n{scenario.name}: {stats.summary(args.qps)}")
    if stats.failed_requests:
        print(f"warning: {stats.failed_requests} request(s) failed", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    from search_metrics_common import configure_logging

    args = build_parser().parse_args(argv)
    configure_logging("query-simulator", os.environ.get("LOG_LEVEL", "INFO"))

    if args.list:
        for name in sorted(SCENARIOS):
            scenario = SCENARIOS[name]
            print(f"{name:<14} {scenario.description}")
            for phase in scenario.phases:
                print(
                    f"{'':<16}{phase.name}: {phase.duration_seconds}s, "
                    f"qps x{phase.qps_multiplier}, latency x{phase.latency_multiplier}, "
                    f"errors {phase.error_rate:.0%}"
                )
        return 0

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
