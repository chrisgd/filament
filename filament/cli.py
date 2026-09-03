"""The command-line entry point.

Parses arguments, loads config, assembles the runtime (model client, tool
registry, session), hands them to the agent loop, and prints the final result.
The CLI knows nothing about HTTP, tool internals, or backend wire formats.
"""

from __future__ import annotations

import argparse
import sys

import httpx

from .agent import run_agent
from .config import load_config
from .interactive import run_interactive
from .model_clients import ModelResponseError, build_client
from .session import new_session
from .tools import build_registry

# main entry point for filament agent
def main(argv: list[str] | None = None) -> int:
    # this just outputs help info if you run it with --help or -h
    parser = argparse.ArgumentParser(
        prog="filament",
        description="Run a task through the Filament agent loop.",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="natural-language task description; omit to start interactive mode",
    )
    parser.add_argument(
        "--backend",
        choices=["rosie", "anthropic"],
        help="override the FILAMENT_BACKEND setting for this run (which defaults to rosie)",
    )
    args = parser.parse_args(argv)

    # loads up the configuration
    config = load_config()
    if args.backend is not None:
        config.backend = args.backend

    try:
        # create the client
        client = build_client(config)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    # then build the registry
    registry = build_registry()
    # now create the new session
    session = new_session()

    # no task on the command line -> interactive mode (see @specs/SPEC-interactive.md)
    if args.task is None:
        try:
            exit_code = run_interactive(
                client, registry, session, config.backend
            )
        finally:
            session.close()
        print(f"\n[transcript: {session.path}]", file=sys.stderr)
        return exit_code

    try:
        # the loop is basically executed through run_agent,
        # which takes the client, registry, and session as arguments
        # and returns the final result when done
        result = run_agent(
            args.task, client, registry, session, config.backend
        )
    except (httpx.HTTPError, ModelResponseError) as exc:
        # a transport failure, an HTTP error status, or a reply the client
        # could not translate is reported as a clean line, not surfaced to
        # the user as an unhandled traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        if isinstance(exc, httpx.HTTPStatusError):
            # the status line only says e.g. "400 Bad Request"; the body is
            # where the backend says why
            body = exc.response.text.strip()
            if body:
                print(f"response body: {body}", file=sys.stderr)
        return 1
    finally:
        session.close()

    # print out the result and the session ID so we can look it up
    print(result)
    print(f"\n[transcript: {session.path}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
