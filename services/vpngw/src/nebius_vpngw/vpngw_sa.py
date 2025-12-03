from __future__ import annotations

"""
Service Account management for Nebius VPNGW.

This module provides a thin wrapper to ensure a Service Account exists with
Editor permissions and to obtain an access token for use with the Nebius SDK.

Notes:
- SDK method names and resources may vary by version; this scaffold attempts to
  initialize the SDK using either explicit tenant/project/region or the local
  Nebius CLI config (default profile) when those are not provided.
- Replace placeholder calls with concrete IAM APIs from `nebius.pysdk` when wiring
  to the actual backend.
"""

import typing as t
import subprocess
import json
import shlex


def _init_client(tenant_id: str | None, project_id: str | None, region_id: str | None):
    """Initialize Nebius SDK client.

    Prefers PyPI SDK (`nebius.sdk.SDK`); falls back to legacy `pysdk.Client`.
    Returns a tuple (client, used_cli_config: bool).
    """
    sdk = None
    pysdk = None
    try:
        import nebius.sdk as _sdk  # type: ignore
        sdk = _sdk
    except Exception:
        try:
            from nebius import pysdk as _pysdk  # type: ignore
            pysdk = _pysdk
        except Exception as e:  # pragma: no cover - runtime import guard
            raise RuntimeError(
                "Nebius SDK not installed. Ensure 'nebius' is in dependencies and available in this environment."
            ) from e

    # Try explicit context first
    if tenant_id and project_id and region_id:
        try:
            if sdk is not None:
                try:
                    client = sdk.SDK(tenant_id=tenant_id, project_id=project_id, region_id=region_id)
                except TypeError:
                    client = sdk.SDK()
            else:
                client = pysdk.Client(tenant_id=tenant_id, project_id=project_id, region_id=region_id)  # type: ignore
            return client, False
        except Exception:
            # fall back to CLI/default config
            pass

    # Fallback: try default initialization without inheriting CLI profile parent-id
    try:
        if sdk is not None:
            # PyPI SDK may read CLI config implicitly via default constructor
            client = sdk.SDK()
            return client, True
        else:
            # Initialize legacy client without CLI config to avoid profile defaults
            client = pysdk.Client()  # type: ignore
            return client, True
    except Exception as e:
        raise RuntimeError(
            "Failed to initialize Nebius SDK client (CLI/default). Ensure Nebius CLI is configured or SDK has defaults."
        ) from e


def ensure_service_account_and_token(
    sa_name: str,
    tenant_id: str | None,
    project_id: str | None,
    region_id: str | None,
) -> t.Optional[str]:
    """Ensure a Service Account exists with Editor permissions and return a token.

    If initialization via explicit context fails, falls back to Nebius CLI config.
    Returns an access token string if available, otherwise None (caller may fall back
    to CLI-configured auth in the SDK).
    """
    client, used_cli = _init_client(tenant_id, project_id, region_id)

    # The exact IAM APIs depend on pysdk; scaffold with defensive guards.
    try:
        iam = getattr(client, "iam")()
    except Exception as e:
        # IAM not available in this SDK instance; operate without SA provisioning.
        print(f"[SA] IAM client not available: {e}")
        return None

    sa = None
    try:
        sa_ops = getattr(iam, "service_account")
        # Try to find by name; fall back to create
        if hasattr(sa_ops, "get_by_name"):
            try:
                sa = sa_ops.get_by_name(name=sa_name, project_id=project_id)
            except Exception:
                sa = None
        if sa is None and hasattr(sa_ops, "create"):
            print(f"[SA] Creating Service Account '{sa_name}' in project {project_id}...")
            sa = sa_ops.create(name=sa_name, project_id=project_id, description="Nebius VPNGW orchestrator")
    except Exception as e:
        print(f"[SA] Failed to ensure Service Account: {e}")
        sa = None

    # Grant Editor role if possible
    try:
        if sa is not None:
            roles = getattr(iam, "roles", None) or getattr(iam, "role", None)
            bindings = getattr(iam, "bindings", None) or getattr(iam, "role_binding", None)
            editor_role_id = "roles/editor"
            if bindings and hasattr(bindings, "grant"):
                print(f"[SA] Granting role {editor_role_id} to {sa_name}...")
                bindings.grant(principal=sa, role_id=editor_role_id, project_id=project_id)
    except Exception as e:
        print(f"[SA] Failed to grant Editor role: {e}")

    # Issue an access token
    token: t.Optional[str] = None
    try:
        if sa is not None:
            tokens = getattr(iam, "tokens", None) or getattr(iam, "access_token", None)
            if tokens and hasattr(tokens, "create_for_service_account"):
                token_obj = tokens.create_for_service_account(service_account_id=getattr(sa, "id", None))
                token = getattr(token_obj, "access_token", None) or getattr(token_obj, "token", None)
    except Exception as e:
        print(f"[SA] Failed to create token for SA: {e}")

    if token:
        return token

    # If token was not retrievable, rely on client auth (CLI config or default creds).
    if used_cli:
        print("[SA] Using Nebius CLI configured credentials (no explicit SA token).")
    return None


def get_cli_token() -> t.Optional[str]:
    """Attempt to read an IAM token from Nebius CLI config via SDK's Config reader.

    Returns a token string if discoverable, else None.
    """
    try:
        from nebius.aio.cli_config import Config  # type: ignore
    except Exception:
        return None

    try:
        cfg = Config(no_parent_id=True)
    except Exception:
        return None

    # Try common attribute/method names defensively
    try:
        if hasattr(cfg, "token"):
            tok = cfg.token
            if callable(tok):
                tok = tok()  # type: ignore[call-arg]
            return tok  # type: ignore[return-value]
    except Exception:
        pass
    try:
        if hasattr(cfg, "access_token"):
            at = cfg.access_token
            if callable(at):
                at = at()  # type: ignore[call-arg]
            return at  # type: ignore[return-value]
    except Exception:
        pass
    # Some configs expose a dict-like interface
    try:
        tok = getattr(cfg, "get", None)
        if callable(tok):
            val = tok("token")  # type: ignore[call-arg]
            if val:
                return val
    except Exception:
        pass
    return None


def ensure_cli_access_token() -> t.Optional[str]:
    """Obtain an IAM access token using Nebius CLI config.

    Order:
    1) Try SDK Config reader (fast path)
    2) Try SDK IAM API via client initialized with Config
    3) Fallback to invoking Nebius CLI: `nebius iam get-access-token`
    """
    # 1) Config reader fast path
    tok = get_cli_token()
    if tok:
        return tok

    # 2) SDK IAM API via CLI Config reader (disable parent-id)
    try:
        import nebius.sdk as sdk  # type: ignore
        from nebius.aio.cli_config import Config  # type: ignore
        client = sdk.SDK(config_reader=Config(no_parent_id=True))
        iam = getattr(client, "iam")()
        tokens = getattr(iam, "access_token", None) or getattr(iam, "tokens", None)
        if tokens is not None:
            # Try common creation/get patterns
            if hasattr(tokens, "create_for_user"):
                obj = tokens.create_for_user()
            elif hasattr(tokens, "create"):
                # Some SDKs expose generic create for current principal
                try:
                    obj = tokens.create()
                except TypeError:
                    obj = tokens.create({})
            elif hasattr(tokens, "get"):
                obj = tokens.get()
            else:
                obj = None
            if obj is not None:
                return getattr(obj, "access_token", None) or getattr(obj, "token", None)
    except Exception:
        pass

    # 3) CLI fallback
    try:
        # Prefer JSON for robust parsing
        cmd = "nebius iam get-access-token --format json"
        res = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                # common fields: access_token or token
                return data.get("access_token") or data.get("token")
            except Exception:
                # stdout may be the token directly
                out = res.stdout.strip()
                if out:
                    return out
        else:
            # Retry without JSON
            cmd2 = "nebius iam get-access-token"
            res2 = subprocess.run(shlex.split(cmd2), capture_output=True, text=True)
            if res2.returncode == 0:
                out2 = res2.stdout.strip()
                if out2:
                    return out2
    except Exception:
        pass
    return None
