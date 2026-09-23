"""Command line: ``eip-complexity run CONFIG`` and ``eip-complexity render RUN``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from eip_complexity import TOOL_NAME, TOOL_VERSION
from eip_complexity.errors import ComplexityError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description=__doc__)
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="evaluate every EIP in a TOML config with Jev")
    run_parser.add_argument("config", type=Path, help="path to the run configuration (TOML)")

    render_parser = subparsers.add_parser("render", help="regenerate index.html from an existing run (no Jev, git or template access)")
    render_parser.add_argument("run", type=Path, help="path to run.json or to the output directory containing it")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    logging.getLogger(TOOL_NAME).setLevel(logging.INFO)  # keep SDK/HTTP chatter at warning level
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            from eip_complexity.run import run

            print(run(args.config))
        elif args.command == "render":
            from eip_complexity.render import render_run

            print(render_run(args.run))
    except ComplexityError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0
