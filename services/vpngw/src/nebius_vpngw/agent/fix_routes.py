from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .routing_guard import enforce_routing_invariants

CONFIG_PATH = Path("/etc/nebius-vpngw/config-resolved.yaml")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce VPN gateway routing invariants")
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Path to resolved gateway config YAML",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config_path = Path(args.config)

    if not config_path.exists():
        print(f"[FixRoutes] Config not found: {config_path}; skipping")
        raise SystemExit(0)

    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[FixRoutes] Failed to load config: {e}", file=sys.stderr)
        raise SystemExit(2)

    if not isinstance(cfg, dict) or not cfg:
        print("[FixRoutes] Config is empty or invalid; skipping", file=sys.stderr)
        raise SystemExit(2)

    try:
        enforce_routing_invariants(cfg)
    except Exception as e:
        print(
            f"[FixRoutes] Failed to enforce routing invariants: {e}",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
