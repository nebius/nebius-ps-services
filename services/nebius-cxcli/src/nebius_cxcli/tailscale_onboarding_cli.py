"""Guided configuration handoff. Activation belongs to ordinary deployment."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from .deployment_state import _read_regular
from .tailscale_cli_io import Component, Config, Json, emit, handled, load, protected_json
from .tailscale_config import validate
from .tailscale_onboarding import fingerprint, markdown, missing, request

app = typer.Typer(
    help="Prepare an administrator request and configure approved activation.", no_args_is_help=True
)


def assert_rotation_allowed(config_path: Path, component: str | None, row: dict) -> None:
    if row["tailscale"]["stage"] != "active":
        return
    from .tailscale_cli import operation

    with operation(config_path, component) as (_owner, _row, record, _guests):
        for own in record["members"].values():
            for op in [own.get("enrollment"), *own.get("prior_enrollments", [])]:
                if op and not op.get("key_retired") and op["phase"] not in {"revoked", "retired"}:
                    raise RuntimeError(
                        "Finish original enrollment/key cleanup before replacing OAuth credentials"
                    )


@app.command(
    "request", epilog="Example: nebius-cxcli tailscale onboarding request ./config.yaml --json"
)
@handled
def onboarding_request(
    config_path: Config,
    component: Component = None,
    out: Annotated[
        Path | None, typer.Option(help="Owner-only request export directory; keep outside Git.")
    ] = None,
    json_output: Json = False,
) -> None:
    """Print a complete request without credentials, cloud discovery or tailnet API calls."""
    _cli, _config, paths, row = load(config_path, component)
    value = request(row)
    # An existing report is evidence only when its declared intent still matches.
    observed_path = paths.reports_dir / (row["instance_id"] + "-tailscale-onboarding.json")
    if observed_path.is_file() and not observed_path.is_symlink():
        observed = json.loads(_read_regular(observed_path))
        if observed.get("intent_sha256") == value["intent_sha256"]:
            value["infrastructure"] = observed.get("infrastructure", value["infrastructure"])
            value["credential_delivery"].update(
                {
                    k: v
                    for k, v in observed.get("credential_delivery", {}).items()
                    if k == "secret_id"
                }
            )
    if out is not None:
        out = out.expanduser().absolute()
        repo = subprocess.run(
            ["git", "-C", str(paths.project_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if repo.returncode == 0 and out.resolve().is_relative_to(
            Path(repo.stdout.strip()).resolve()
        ):
            raise ValueError("Export administrator requests outside the Git checkout")
        if out.is_symlink():
            raise ValueError("Refusing a symlink export directory")
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        protected_json(out / "tailscale-admin-request.json", value)
        target = out / "tailscale-admin-request.md"
        if target.is_symlink():
            raise ValueError("Refusing a symlink export file")
        fd, temporary = tempfile.mkstemp(prefix=".tailscale-", dir=out)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(markdown(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    emit(value, json_output)


@app.command(
    "configure", epilog="Example: nebius-cxcli tailscale onboarding configure ./config.yaml"
)
@handled
def onboarding_configure(
    config_path: Config,
    component: Component = None,
    approval_reference: Annotated[
        str | None, typer.Option(help="Administrator approval/ticket reference, never a secret.")
    ] = None,
    accepted_fingerprint: Annotated[
        str | None, typer.Option(help="Exact approved administrator-request SHA256.")
    ] = None,
    client_id: Annotated[
        str | None, typer.Option(help="OAuth client identifier for environment/file custody.")
    ] = None,
    client_secret_ref: Annotated[
        str | None, typer.Option(help="env:NAME or file:PATH, never a secret value.")
    ] = None,
    secret_id: Annotated[
        str | None, typer.Option(help="Exact SecretStash secret ID containing the OAuth pair.")
    ] = None,
    secret_version: Annotated[
        str | None, typer.Option(help="Optional immutable SecretStash version ID.")
    ] = None,
    import_to_secretstash: Annotated[
        bool,
        typer.Option(
            help="Import the supplied protected reference into the linked empty container."
        ),
    ] = False,
    import_operation_id: Annotated[
        str, typer.Option(help="Exact provider operation ID for an interrupted import.")
    ] = "",
    nebius_service_account_id: Annotated[
        str | None, typer.Option(help="IAM identity for a Kubernetes consumer.")
    ] = None,
    nebius_token_file: Annotated[
        Path | None, typer.Option(help="Projected JWT path, never token contents.")
    ] = None,
    tailnet: Annotated[str | None, typer.Option(help="Administrator-approved tailnet.")] = None,
    tag: Annotated[
        list[str] | None, typer.Option(help="Repeat each administrator-approved tag.")
    ] = None,
    interactive: Annotated[
        bool,
        typer.Option("--interactive/--no-interactive", help="Guide the administrator handoff."),
    ] = True,
    json_output: Json = False,
) -> None:
    """Save the approved handoff; render and deploy separately to activate."""
    cli, _config, paths, row = load(config_path, component)
    before = paths.config_path.read_bytes()
    payload = cli._load_config_payload(paths.config_path)
    target = next(
        r
        for r in payload["infra"]["components"]
        if r.get("instance_id", r.get("id")) == row["instance_id"]
    )
    candidate = copy.deepcopy(target)
    cfg = candidate["tailscale"]
    cfg["stage"] = "infrastructure"
    if tailnet is not None:
        cfg["tailnet"] = tailnet
    if tag is not None:
        cfg["tags"] = tag
    if interactive:
        cli.console.print(
            "Review the exact administrator-approved settings. No enrollment happens in this wizard."
        )
        for key, hint in (
            ("tailnet", "string"),
            ("tags", "list(string)"),
            ("routes", "list(string)"),
        ):
            answer, stop = cli._prompt_scalar_override(
                "tailscale." + key, cfg.get(key), type_hint=hint, required=True
            )
            if stop or cli._wizard_backtrack_requested(answer):
                return
            cfg[key] = answer
        for key in ("owner", "purpose", "environment"):
            onboarding = cfg.setdefault("onboarding", {})
            answer, stop = cli._prompt_scalar_override(
                "tailscale.onboarding." + key,
                onboarding.get(key, ""),
                type_hint="string",
                required=key != "environment",
            )
            if stop or cli._wizard_backtrack_requested(answer):
                return
            if answer:
                onboarding[key] = answer
        for container, key, hint, default in (
            (cfg, "approval", "string", "external"),
            (cfg.setdefault("policy", {}), "tag_owners", "list(string)", []),
            (cfg["policy"], "auto_approve", "bool", True),
            (cfg["onboarding"], "admin_requirements", "list(string)", []),
        ):
            answer, stop = cli._prompt_scalar_override(
                "tailscale." + key, container.get(key, default), type_hint=hint, required=False
            )
            if stop or cli._wizard_backtrack_requested(answer):
                return
            container[key] = answer
        from .tailscale_wizard import prompt_rules

        rules, stop = prompt_rules(
            cfg.get("policy", {}).get("rules", []), candidate, "tailscale.policy.rules"
        )
        if stop or cli._wizard_backtrack_requested(rules):
            return
        cfg.setdefault("policy", {})["rules"] = rules
    if missing(candidate):
        raise ValueError(
            "Complete administrator request settings: " + ", ".join(missing(candidate))
        )
    value = request(candidate)
    if not json_output:
        emit(value, False)
    if interactive:
        if not typer.confirm(
            "Do these exact settings match the administrator approval?", default=False
        ):
            return
        approval_reference = approval_reference or typer.prompt("Approval reference")
        accepted_fingerprint = accepted_fingerprint or value["intent_sha256"]
    if not approval_reference or accepted_fingerprint != fingerprint(candidate):
        raise ValueError("Supply the approval reference and exact accepted request fingerprint")
    cfg.setdefault("onboarding", {})["accepted"] = {
        "fingerprint": accepted_fingerprint,
        "reference": approval_reference,
    }
    cfg["stage"] = "active"
    auth = cfg.setdefault("auth", {"mode": "oauth"})
    ref = auth.get("secretstash")
    if secret_id:
        ref = ref or {"project_id": candidate["inputs"]["parent_id"]}
        ref["secret_id"] = secret_id
        auth["secretstash"] = ref
    if not ref and any(
        (
            import_to_secretstash,
            import_operation_id,
            secret_version,
            nebius_service_account_id,
            nebius_token_file,
        )
    ):
        raise ValueError(
            "SecretStash options require an exact secret or linked container reference"
        )
    if import_operation_id and not import_to_secretstash:
        raise ValueError("SecretStash import recovery requires --import-to-secretstash")
    if ref:
        if interactive and not ref.get("secret_id"):
            ref["secret_id"] = typer.prompt("SecretStash secret ID from the infrastructure handoff")
        if secret_version:
            ref["version_id"] = secret_version
        auth.pop("client_id", None)
        auth.pop("client_secret_ref", None)
        if nebius_service_account_id or nebius_token_file:
            if not nebius_service_account_id or not nebius_token_file:
                raise ValueError(
                    "Workload identity requires both IAM service-account ID and projected token path"
                )
            ref["nebius_auth"] = {
                "workload_identity": {
                    "service_account_id": nebius_service_account_id,
                    "token_file": str(nebius_token_file),
                }
            }
        if interactive and ref.get("component_instance"):
            import_to_secretstash = import_to_secretstash or typer.confirm(
                "Import an administrator-delivered credential from a protected reference?",
                default=False,
            )
        validate(candidate)
        if import_to_secretstash:
            from .tailscale_api import TailscaleAPI
            from .tailscale_secret_import import import_pair

            if interactive:
                client_id = client_id or typer.prompt("OAuth client ID")
                client_secret_ref = client_secret_ref or typer.prompt(
                    "Protected secret reference (env:NAME or file:PATH)"
                )
            if not client_id or not client_secret_ref:
                raise ValueError("Import requires client ID and a protected secret reference")
            proof_path = paths.reports_dir / (row["instance_id"] + "-tailscale-onboarding.json")
            proof = json.loads(_read_regular(proof_path))
            if (
                proof.get("credential_delivery", {}).get("secret_id") != ref.get("secret_id")
                or proof.get("infrastructure", {}).get("status") != "VERIFIED"
            ):
                raise ValueError("Secret ID does not match the completed infrastructure handoff")
            candidate_api = TailscaleAPI(
                {**cfg, "auth": {"client_id": client_id, "client_secret_ref": client_secret_ref}},
                base_dir=paths.project_dir,
            )

            def validate_pair(identity: str, value: str) -> None:
                candidate_api.authenticate_pair(identity, value)
                candidate_api.request("GET", candidate_api.tailnet + "/devices?fields=default")

            assert_rotation_allowed(config_path, component, row)
            ref["version_id"] = import_pair(
                ref,
                client_id=client_id,
                secret_ref=client_secret_ref,
                base=paths.project_dir,
                operation_id=import_operation_id,
                validate_pair=validate_pair,
            )
    elif auth.get("mode", "oauth") == "auth-key" and not client_id and not client_secret_ref:
        from .tailscale_config import members

        if set(auth.get("member_key_refs", {})) != set(members(candidate)):
            raise ValueError(
                "Configure exact per-member administrator keys before completing the handoff"
            )
    else:
        if interactive:
            client_id = client_id or typer.prompt(
                "OAuth client ID", default=auth.get("client_id", "")
            )
            client_secret_ref = client_secret_ref or typer.prompt(
                "Protected secret reference (env:NAME or file:PATH)",
                default=auth.get("client_secret_ref", ""),
            )
        if not client_id or not client_secret_ref:
            raise ValueError(
                "Supply a SecretStash reference or OAuth ID and protected secret reference"
            )
        auth.update(client_id=client_id, client_secret_ref=client_secret_ref)
    if auth != target["tailscale"].get("auth", {}):
        assert_rotation_allowed(config_path, component, row)
    cfg.setdefault("onboarding", {})["accepted"] = {
        "fingerprint": accepted_fingerprint,
        "reference": approval_reference,
    }
    cfg["stage"] = "active"
    validate(candidate)
    target.clear()
    target.update(candidate)
    cli._write_runtime_payload_config(paths.config_path, payload, expected_bytes=before)
    emit(
        {
            "status": "CONFIGURED",
            "component": row["instance_id"],
            "stage": "active",
            "next": "Render and deploy to activate the existing infrastructure.",
        },
        json_output,
    )
