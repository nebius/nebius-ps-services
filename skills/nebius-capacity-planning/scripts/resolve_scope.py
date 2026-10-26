#!/usr/bin/env python3
"""Normalize capacity-skill selectors offline; never read credentials or call cloud APIs.

Pass selector tokens only, after resolving any explicit current-user tenant.
Design references and natural-language assessment requests stay with the skill.
"""

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation


class Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        seen = getattr(namespace, "_seen", set())
        if self.dest in seen:
            parser.error("selectors and modes must not be repeated")
        seen.add(self.dest)
        namespace._seen = seen
        setattr(namespace, self.dest, self.const if self.nargs == 0 else values)


def text_value(value):
    if not value or value != value.strip() or len(value) > 256:
        raise argparse.ArgumentTypeError("expected nonempty text up to 256 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("control characters are not allowed")
    return value


def multiplier_value(value):
    try:
        if len(value) > 128:
            raise ValueError
        number = Decimal(value)
        if not number.is_finite() or not 1 <= number <= 2**63 - 1:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError(
            "multiplier must be finite, at least 1 and bounded"
        ) from None
    return str(number)


def resolve_scope(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(token == "--project" or token.startswith("--project=") for token in argv):
        parser.error(
            "--project was removed; use --tenant and --region; project checks belong to provisioning"
        )
    parser.add_argument("--tenant", action=Once, type=text_value)
    parser.add_argument("--region", action=Once, type=text_value, default="all")
    parser.add_argument("--profile", action=Once, type=text_value)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--quota-check", dest="mode", action=Once, nargs=0, const="check"
    )
    modes.add_argument(
        "--quota-request", dest="mode", action=Once, nargs=0, const="request"
    )
    parser.add_argument(
        "--quota-multiplier", action=Once, type=multiplier_value, default="1"
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,127}", args.region):
        parser.error(
            "region must be a region identifier or all; verify support from current evidence"
        )
    if args.mode and not args.tenant:
        parser.error(
            "quota modes require a user-selected tenant; profile is not tenant authority"
        )
    mode = args.mode or ("check" if args.tenant else "design")
    common = ["--tenant-id", args.tenant] if args.tenant else []
    if args.tenant and args.region != "all":
        common += ["--region", args.region]
    if args.tenant and args.profile:
        common += ["--profile", args.profile]
    return {
        "tenant": args.tenant,
        "region": args.region,
        "profile": args.profile,
        "mode": mode,
        "multiplier": args.quota_multiplier,
        "inspection": "tenant" if args.tenant else "public-docs",
        "quota_args": [*common, "--raw", "--json"] if args.tenant else [],
        "capacity_args": [*common, "--json"] if args.tenant else [],
        "request_scope_ready": mode == "request" and args.region != "all",
        "boundary": "Scope only, not authorization; verify region, identity, evidence and request guards separately.",
    }


if __name__ == "__main__":
    print(json.dumps(resolve_scope(), indent=2))
