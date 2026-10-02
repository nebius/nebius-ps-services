"""CLI entrypoint for nebius-cxcli."""

from __future__ import annotations

import sys


def main() -> None:
    if sys.argv[1:2] == ["mk8s-token"]:
        from .mk8s_exec import app

        app()
    else:
        from .cli import main as cli_main

        cli_main()


if __name__ == "__main__":
    main()
