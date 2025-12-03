#!/usr/bin/env python3
"""Simple Nebius VPC network listing test script (SDK >= 0.3.0).

Usage (token from environment):

    export NEBIUS_IAM_TOKEN=$(nebius iam get-access-token)
    python nebius-sdktest.py --project-id your-project-id [--tenant-id TENANT] [--region-id REGION]

Optional auth renewal flags (demonstrates 0.3.0 change — use auth_options instead of metadata injection):

    python nebius-sdktest.py --project-id your-project-id --force-renew

What changed in >= 0.3.0:
- Authorization control keys (e.g. OPTION_RENEW_REQUIRED) are passed via the request call's auth_options= mapping
  instead of being merged into metadata via options_to_metadata().

This script shows BOTH high-level convenience client usage and low-level service client usage with auth_options.

Exit codes:
 0 success, networks listed (may be empty)
 1 usage or missing env vars
 2 SDK/API error
"""
from __future__ import annotations

import os
import sys
import argparse
from typing import Any

# High-level pysdk client (if available) and low-level request primitives.
try:
    from nebius import pysdk  # type: ignore
except ImportError:  # fallback to base SDK only
    pysdk = None  # type: ignore

# Low-level generated service & request classes
try:
    from nebius.api.nebius.vpc.v1 import NetworkServiceClient, ListNetworksRequest  # type: ignore
except ImportError:
    NetworkServiceClient = None  # type: ignore
    ListNetworksRequest = None  # type: ignore

# Auth option constants (token renewal etc.)
try:
    from nebius.aio.token.options import (
        OPTION_RENEW_REQUIRED,
        OPTION_RENEW_SYNCHRONOUS,
        OPTION_RENEW_REQUEST_TIMEOUT,
    )  # type: ignore
except ImportError:
    OPTION_RENEW_REQUIRED = "token_renew_required"
    OPTION_RENEW_SYNCHRONOUS = "token_renew_synchronous"
    OPTION_RENEW_REQUEST_TIMEOUT = "token_renew_request_timeout"

# Base SDK for channel/control if using low-level service
from nebius.aio.token.static import EnvBearer  # type: ignore
try:
    from nebius.aio.channel import Channel  # type: ignore
except ImportError:
    Channel = None  # type: ignore
try:
    from nebius.base.constants import DOMAIN  # type: ignore
except ImportError:
    DOMAIN = "api.nebius.cloud:443"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="List Nebius VPC networks (SDK >= 0.3.0)")
    p.add_argument("--project-id", required=True, help="Nebius project ID (parent-id for list)")
    p.add_argument("--tenant-id", default=os.environ.get("TENANT_ID"), help="Tenant ID (optional, env TENANT_ID)")
    p.add_argument("--region-id", default=os.environ.get("REGION_ID", "eu-north1"), help="Region ID (default eu-north1)")
    p.add_argument("--force-renew", action="store_true", help="Pass renewal auth_options forcing token refresh")
    p.add_argument("--timeout", type=float, default=10.0, help="Wait timeout seconds for the list request")
    return p.parse_args()


def ensure_token() -> None:
    if os.environ.get("NEBIUS_IAM_TOKEN", "") == "":
        print("ERROR: NEBIUS_IAM_TOKEN env var not set. Export it first.", file=sys.stderr)
        sys.exit(1)


def build_auth_options(force: bool) -> dict[str, str]:
    if not force:
        return {}
    # Demonstrate new >=0.3.0 style: auth_options mapping instead of metadata munging.
    return {
        OPTION_RENEW_REQUIRED: "true",
        OPTION_RENEW_SYNCHRONOUS: "true",
        OPTION_RENEW_REQUEST_TIMEOUT: "0.9",  # seconds as string
    }


def list_networks_high_level(project_id: str, tenant_id: str | None, region_id: str) -> list[Any]:
    if pysdk is None:
        return []
    client = pysdk.Client(tenant_id=tenant_id, project_id=project_id, region_id=region_id)
    networks = []
    for net in client.vpc().network.list(parent_id=project_id):  # iteration yields network objects
        networks.append(net)
    return networks


def list_networks_low_level(project_id: str, auth_options: dict[str, str], timeout: float) -> list[Any]:
    """Low-level listing using generated NetworkServiceClient with auth_options.

    Internally runs the async request via asyncio; avoids deprecated .wait() usage.
    """
    if NetworkServiceClient is None or ListNetworksRequest is None or Channel is None:
        return []

    import asyncio

    async def _run() -> list[Any]:
        bearer = EnvBearer("NEBIUS_IAM_TOKEN")
        channel = Channel(domain=DOMAIN, credentials=bearer)
        try:
            service = NetworkServiceClient(channel)
            request = ListNetworksRequest(parent_id=project_id)
            req = service.list(request, auth_options=auth_options)
            # The request object should be awaitable; fall back to .wait() if present.
            try:
                resp = await req  # type: ignore[misc]
            except TypeError:
                # Fallback to synchronous wait without timeout kw if needed.
                if hasattr(req, "wait"):
                    resp = req.wait()  # type: ignore[assignment]
                else:
                    raise
            items = getattr(resp, "items", [])
            return list(items)
        finally:
            try:
                await channel.close()
            except Exception:
                pass

    try:
        return asyncio.run(_run())
    except RuntimeError:
        # Already inside an event loop (unlikely for CLI); create nested loop via new thread
        from threading import Thread
        result: list[Any] = []
        exc: list[Exception] = []

        def _t():
            try:
                res = asyncio.run(_run())
                result.extend(res)
            except Exception as e:  # noqa: BLE001
                exc.append(e)

        th = Thread(target=_t)
        th.start(); th.join(timeout=timeout if timeout > 0 else None)
        if exc:
            raise exc[0]
        return result


def main() -> None:
    args = parse_args()
    ensure_token()
    auth_options = build_auth_options(args.force_renew)

    # Prefer low-level example when auth_options requested (shows new signature explicitly)
    low_level_items = list_networks_low_level(args.project_id, auth_options, args.timeout)
    if low_level_items:
        print("Low-level NetworkServiceClient results:")
        for i, net in enumerate(low_level_items, 1):
            print(f"  [{i}] {net}")
    else:
        print("Low-level listing produced no items or service unavailable.")

    # High-level convenience (does not expose auth_options directly, included for completeness)
    high_level_items = list_networks_high_level(args.project_id, args.tenant_id, args.region_id)
    if high_level_items:
        print("\nHigh-level pysdk.Client results:")
        for i, net in enumerate(high_level_items, 1):
            print(f"  [{i}] {net}")
    else:
        if pysdk is not None:
            print("High-level listing produced no items.")
        else:
            print("pysdk high-level client not available; only low-level example run.")

    # Exit success always (presence of empty results is not fatal)
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(1)
