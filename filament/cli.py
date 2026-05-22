"""The command-line entry point.

Parses arguments, loads config, assembles the runtime (model client, tool
registry, session), hands them to the agent loop, and prints the final result.
The CLI knows nothing about HTTP, tool internals, or backend wire formats.
"""

from __future__ import annotations

import argparse
import sys

from .agent import run_agent
from .config import load_config
from .model_clients import build_client
from .session import new_session
from .tools import build_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="filament",
        description="Run a task through the Filament agent loop.",
    )
    parser.add_argument("task", help="natural-language task description")
    parser.add_argument(
        "--backend",
        choices=["rosie", "anthropic"],
        help="override the FILAMENT_BACKEND setting for this run",
    )
    args = parser.parse_args(argv)

    config = load_config()
    if args.backend is not None:
        config.backend = args.backend

    try:
        client = build_client(config)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    registry = build_registry()
    session = new_session()
    try:
        result = run_agent(
            args.task, client, registry, session, config.backend
        )
    finally:
        session.close()

    print(result)
    print(f"\n[transcript: {session.path}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
