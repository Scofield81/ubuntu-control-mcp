"""Belepesi pont: `python -m ubuntu_control_mcp` vagy a 'ubuntu-control-mcp' parancs."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ubuntu-control-mcp",
        description="Ubuntu Control MCP szerver - Linux desktop agent MCP.",
    )
    parser.add_argument("--version", action="version", version=f"ubuntu-control-mcp {__version__}")
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Kilistazza a regisztralt toolokat es kilep (nem indit szervert).",
    )
    args = parser.parse_args()

    from .server import mcp, run

    if args.list_tools:
        try:
            tools = sorted(mcp._tool_manager._tools)  # type: ignore[attr-defined]
        except Exception:
            tools = []
        print(f"{len(tools)} tool:")
        for name in tools:
            print(f"  - {name}")
        sys.exit(0)

    run()


if __name__ == "__main__":
    main()
