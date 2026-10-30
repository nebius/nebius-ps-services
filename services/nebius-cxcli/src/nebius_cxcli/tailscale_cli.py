"""Tailnet operations for gateways provisioned by the ordinary cxcli lifecycle."""

from __future__ import annotations

import copy
import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import typer

from .ssh_host_identity import ComputeHostIdentityClient
from .tailscale_api import TailscaleAPI
from .tailscale_cli_io import Component, Config, Json, emit, handled, load, protected_json
from .tailscale_config import desired, validate
from .tailscale_deploy import TailscaleDeployment, outputs, readiness
from .tailscale_onboarding import assert_activation
from .tailscale_onboarding_cli import app as onboarding_app

app = typer.Typer(
    help="Manage customer tailnet enrollment, policy, routes and verification.",
    no_args_is_help=True,
)
auth_app = typer.Typer(
    help="Configure credential references and check delegated access.", no_args_is_help=True
)
policy_app = typer.Typer(
    help="Review and conditionally apply a lossless tailnet policy proposal.", no_args_is_help=True
)
routes_app = typer.Typer(help="Approve exact routes on owned devices.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")
app.add_typer(policy_app, name="policy")
app.add_typer(routes_app, name="routes")
app.add_typer(onboarding_app, name="onboarding")


def credential_api(cli: Any, paths: Any, config: dict[str, Any]) -> TailscaleAPI:
    """Resolve credential custody without changing the reviewed policy intent."""
    from .tailscale_dependencies import resolve_secret

    resolved = copy.deepcopy(config)
    ref = resolved.get("auth", {}).get("secretstash")
    if ref and ref.get("component_instance"):
        ref["secret_id"] = resolve_secret(
            ref, cli.terraform_output_json(paths.infra_dir, initialize=False)
        )
    return TailscaleAPI(resolved, base_dir=paths.project_dir)


@contextmanager
def operation(config_path: Path, component: str | None, *, mutate: bool = False):
    from .deployment_cli import deployment_execution
    from .generated_manifest import load_generated_manifest

    cli, config, paths, row = load(config_path, component)
    if mutate:
        assert_activation(row)
    with ExitStack() as stack:
        lease = (
            stack.enter_context(
                deployment_execution(
                    config=config,
                    paths=paths,
                    target_ref="project",
                    operation_id="Tailscale operation",
                    bootstrap_backend=False,
                )
            )
            if mutate
            else None
        )
        executor = SimpleNamespace(
            cli=cli,
            config=config,
            paths=paths,
            report_paths=paths,
            manifest=load_generated_manifest(paths.generated_dir),
            options=SimpleNamespace(dry_run=not mutate),
            lease=lease,
            runtime_env=cli._terraform_runtime_env(config),
        )
        owner = stack.enter_context(TailscaleDeployment(executor))
        journal = owner.open()
        record = journal.value["gateways"].get(row["instance_id"])
        if (
            not record
            or record.get("tailnet") != row["tailscale"]["tailnet"]
            or record.get("retired")
        ):
            raise RuntimeError(
                "No active matching Tailscale owner; use ordinary render/deploy first"
            )
        output = outputs(owner.raw_outputs(), row["instance_id"])
        for member, binding in output.items():
            known = record["members"].get(member, {})
            if any(
                known.get(k) != binding.get(k)
                for k in ("instance_id", "boot_disk_id", "public_allocation_id")
            ):
                raise RuntimeError(
                    "Cloud member identity differs from the ownership journal; resume deploy"
                )
        sdk = stack.enter_context(ComputeHostIdentityClient(row["inputs"]["parent_id"]))
        guests = owner.guests(row, output, sdk)
        yield owner, row, record, guests


@auth_app.command(
    "configure",
    epilog="Example: nebius-cxcli tailscale auth configure ./config.yaml --client-id CLIENT --client-secret-ref env:TAILSCALE_CLIENT_SECRET",
)
@handled
def auth_configure(
    config_path: Config,
    component: Component = None,
    mode: Annotated[str, typer.Option(help="oauth or auth-key enrollment.")] = "oauth",
    client_id: Annotated[
        str | None, typer.Option(help="OAuth client identifier, not its secret.")
    ] = None,
    client_secret_ref: Annotated[
        str | None, typer.Option(help="env:NAME or file:PATH; never pass a secret value.")
    ] = None,
    secretstash_secret_id: Annotated[
        str | None, typer.Option(help="Exact SecretStash OAuth pair secret ID.")
    ] = None,
    secretstash_project_id: Annotated[
        str | None, typer.Option(help="Project containing the OAuth secret.")
    ] = None,
    secretstash_version: Annotated[
        str | None, typer.Option(help="Optional exact credential version.")
    ] = None,
    nebius_service_account_id: Annotated[
        str | None, typer.Option(help="Nebius IAM identity for a Kubernetes runner.")
    ] = None,
    nebius_token_file: Annotated[
        Path | None, typer.Option(help="Projected Kubernetes JWT path; never its contents.")
    ] = None,
    member_key_ref: Annotated[
        list[str] | None,
        typer.Option(
            help="Repeat MEMBER=env:NAME or MEMBER=file:PATH for administrator-issued keys."
        ),
    ] = None,
    member_key_id: Annotated[
        list[str] | None,
        typer.Option(
            help="Repeat MEMBER=KEY_ID with each administrator key reference; never its secret."
        ),
    ] = None,
    ssh_private_key_file: Annotated[
        Path | None, typer.Option(help="Management SSH key path; default uses the SSH agent.")
    ] = None,
    json_output: Json = False,
) -> None:
    """Persist references in config only; render again after changing them."""
    cli, _config, paths, row = load(config_path, component)
    before = paths.config_path.read_bytes()
    payload = cli._load_config_payload(paths.config_path)
    target = next(
        r
        for r in payload["infra"]["components"]
        if r.get("instance_id", r.get("id")) == row["instance_id"]
    )
    import copy

    auth = copy.deepcopy(target["tailscale"].get("auth", {}))
    auth["mode"] = mode
    if secretstash_secret_id is not None:
        auth["secretstash"] = {
            "project_id": secretstash_project_id or row["inputs"]["parent_id"],
            "secret_id": secretstash_secret_id,
        }
        auth.pop("client_id", None)
        auth.pop("client_secret_ref", None)
    if secretstash_version is not None:
        if not auth.get("secretstash"):
            raise ValueError("A SecretStash version requires an exact secret reference")
        auth["secretstash"]["version_id"] = secretstash_version
    if nebius_service_account_id is not None or nebius_token_file is not None:
        if not auth.get("secretstash") or not nebius_service_account_id or not nebius_token_file:
            raise ValueError(
                "Workload identity requires a SecretStash reference, IAM account and token path"
            )
        auth["secretstash"]["nebius_auth"] = {
            "workload_identity": {
                "service_account_id": nebius_service_account_id,
                "token_file": str(nebius_token_file),
            }
        }
    if client_id is not None:
        auth["client_id"] = client_id
    if client_secret_ref is not None:
        auth["client_secret_ref"] = client_secret_ref
    if member_key_ref is not None:
        pairs = [value.split("=", 1) for value in member_key_ref]
        if any(len(p) != 2 for p in pairs) or len({p[0] for p in pairs}) != len(pairs):
            raise ValueError("Use one MEMBER=REFERENCE per member")
        auth["member_key_refs"] = dict(pairs)
    if member_key_id is not None:
        pairs = [value.split("=", 1) for value in member_key_id]
        if any(len(p) != 2 for p in pairs) or len({p[0] for p in pairs}) != len(pairs):
            raise ValueError("Use one MEMBER=KEY_ID per member")
        auth["member_key_ids"] = dict(pairs)
    if ssh_private_key_file is not None:
        auth["ssh_private_key_file"] = str(ssh_private_key_file)
    from .tailscale_onboarding_cli import assert_rotation_allowed

    if auth != target["tailscale"].get("auth", {}):
        assert_rotation_allowed(config_path, component, row)
    target["tailscale"]["auth"] = auth
    validate(target)
    cli._write_runtime_payload_config(paths.config_path, payload, expected_bytes=before)
    if json_output:
        emit(
            {
                "status": "SAVED",
                "component": row["instance_id"],
                "next": "auth check, render, deploy",
            },
            True,
        )
    else:
        cli.console.print(
            "Credential references saved. Run tailscale auth check, then render and deploy."
        )


@auth_app.command("check", epilog="Example: nebius-cxcli tailscale auth check ./config.yaml --json")
@handled
def auth_check(config_path: Config, component: Component = None, json_output: Json = False) -> None:
    """Check credentials without enrolling devices or changing policy."""
    from .tailscale_config import secret

    _cli, _config, paths, row = load(config_path, component)
    cfg, checks = row["tailscale"], {}
    auth = cfg.get("auth", {})
    if auth.get("client_secret_ref") or auth.get("secretstash"):
        api = credential_api(_cli, paths, cfg)
        api.request("GET", api.tailnet + "/devices?fields=default")
        checks["oauth"] = "authenticated; device read verified; write scopes are checked at use"
    if auth.get("mode", "oauth") == "auth-key":
        for member in ("a", "b")[: row["inputs"].get("replicas", 2)]:
            reference = auth.get("member_key_refs", {}).get(member, "")
            if not secret(reference, base=paths.project_dir).startswith("tskey-auth-"):
                raise ValueError("Expected an administrator-issued auth key")
            checks[member] = (
                "reference readable; validity checked at enrollment; single-use and non-ephemeral capabilities must be verified by the administrator"
            )
    elif not checks:
        raise RuntimeError("Configure scoped OAuth credentials first")
    emit({"status": "PASS", "checks": checks}, json_output)


@policy_app.command(
    "plan",
    epilog="Example: nebius-cxcli tailscale policy plan ./config.yaml --out ./policy-plan.json",
)
@handled
def policy_plan(
    config_path: Config,
    out: Annotated[Path, typer.Option(help="Owner-only proposal file to review.")],
    component: Component = None,
    json_output: Json = False,
) -> None:
    """Validate a bounded proposal while preserving existing HuJSON comments and fields."""
    from .tailscale_policy import make_plan

    cli, _config, paths, row = load(config_path, component)
    cfg = row["tailscale"]
    result = make_plan(credential_api(cli, paths, cfg), cfg, row["instance_id"])
    protected_json(out, result)
    if json_output:
        emit({"status": "PLANNED", "file": str(out), "sha256": result["sha256"]}, True)
    else:
        cli.console.print(
            f"Review {out}; digest {result['sha256']}. No policy was changed.", markup=False
        )


@policy_app.command(
    "apply",
    epilog="Example: nebius-cxcli tailscale policy apply ./config.yaml --plan ./policy-plan.json --sha256 REVIEWED_DIGEST",
)
@handled
def policy_apply(
    config_path: Config,
    plan: Annotated[Path, typer.Option(help="Reviewed proposal JSON.")],
    sha256: Annotated[str, typer.Option(help="Exact digest printed by policy plan.")],
    component: Component = None,
    json_output: Json = False,
) -> None:
    """Apply only the reviewed proposal if its tailnet policy baseline still matches."""
    from .tailscale_policy import apply_plan

    _cli, _config, paths, row = load(config_path, component)
    if plan.is_symlink() or plan.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Unsafe or oversized policy proposal")
    proposal = json.loads(plan.read_text())
    if proposal.get("sha256") != sha256:
        raise ValueError("Reviewed proposal digest does not match")
    cfg = row["tailscale"]
    emit(
        apply_plan(credential_api(_cli, paths, cfg), cfg, row["instance_id"], proposal),
        json_output,
    )


@routes_app.command(
    "approve", epilog="Example: nebius-cxcli tailscale routes approve ./config.yaml --json"
)
@handled
def routes_approve(
    config_path: Config, component: Component = None, json_output: Json = False
) -> None:
    """Approve only exact configured prefixes on journal-owned, identity-checked devices."""
    with operation(config_path, component, mutate=True) as (owner, row, record, guests):
        api = owner.api(row)
        if api is None:
            raise RuntimeError("Route approval requires scoped OAuth credentials")
        result = []
        for member, guest in guests.items():
            owner.open().assert_held()
            observed = guest.call("status")
            if observed.get("node_id") != record["members"][member].get("node_id"):
                raise RuntimeError("Guest node differs from the owned device")
            ready = readiness(api, observed, desired(row, member), approve=True)
            record["members"][member]["phase"] = "READY" if ready else "PENDING"
            owner.open().save()
            result.append({"member": member, "status": record["members"][member]["phase"]})
        emit({"members": result}, json_output)


def observe(config_path: Path, component: str | None, *, detailed: bool) -> dict[str, Any]:
    from .generated_manifest import load_generated_manifest
    from .tailscale_config import members
    from .tailscale_retirement import guest_for

    cli, config, paths, row = load(config_path, component)
    executor = SimpleNamespace(
        cli=cli,
        config=config,
        paths=paths,
        report_paths=paths,
        manifest=load_generated_manifest(paths.generated_dir),
        options=SimpleNamespace(dry_run=True),
        lease=None,
        runtime_env=cli._terraform_runtime_env(config),
    )
    with TailscaleDeployment(executor) as owner:
        record = owner.open().value["gateways"].get(row["instance_id"], {})
        if record and record.get("tailnet") != row["tailscale"]["tailnet"]:
            raise RuntimeError("Tailscale owner tailnet differs from configuration")
        output = outputs(owner.raw_outputs(), row["instance_id"])
        report = []
        with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
            for member in members(row):
                own = record.get("members", {}).get(member, {})
                item = {
                    "member": member,
                    "status": own.get("phase", "PENDING"),
                    "enrollment": {
                        k: own.get("enrollment", {}).get(k) for k in ("id", "phase", "key_id")
                    },
                    "retirement": {
                        k: own.get(k, False) for k in ("key_retired", "device_retired", "vm_absent")
                    },
                }
                try:
                    binding = output.get(member, {})
                    if not binding or any(
                        own.get(k) != binding.get(k)
                        for k in ("instance_id", "boot_disk_id", "public_allocation_id")
                    ):
                        raise RuntimeError("Incomplete binding")
                    current = guest_for(owner, row, binding, sdk).call("status")
                    if row["tailscale"]["stage"] == "infrastructure":
                        from .tailscale_config import VERSION

                        ready = (
                            not record.get("activation_started")
                            and record.get("lifecycle") == "infrastructure-ready"
                            and not current.get("node_id")
                            and not current.get("online")
                            and current.get("forwarding") is True
                            and current.get("version")
                            == row["inputs"].get("tailscale_version", VERSION)
                        )
                        item["status"] = "INFRASTRUCTURE_READY" if ready else "PENDING"
                    else:
                        ready = (
                            not record.get("retiring")
                            and current.get("node_id") == own.get("node_id")
                            and readiness(owner.api(row), current, desired(row, member))
                        )
                        item["status"] = "READY" if ready else "PENDING"
                    if detailed:
                        item["guest"] = current
                except (RuntimeError, OSError, KeyError):
                    item["status"] = "RETIRED" if record.get("retired") else "PENDING"
                    item["reason"] = (
                        "Guest or ownership verification unavailable; inspect enrollment/retirement phase"
                    )
                report.append(item)
        return {
            "component": row["instance_id"],
            "members": report,
            "ha_configured": len(report) == 2,
            "client": "Run tailscale verify from a connected client",
            "next": "Resume deploy, recover the exact enrollment operation, or retry ordinary destroy",
        }


@app.command("status", epilog="Example: nebius-cxcli tailscale status ./config.yaml --json")
@handled
def status(config_path: Config, component: Component = None, json_output: Json = False) -> None:
    """Read fresh cloud, guest and tailnet state; configuration alone is not HA proof."""
    emit(observe(config_path, component, detailed=False), json_output)


@app.command("doctor", epilog="Example: nebius-cxcli tailscale doctor ./config.yaml --json")
@handled
def doctor(config_path: Config, component: Component = None, json_output: Json = False) -> None:
    """Diagnose identity, forwarding, approval, expiry and management reachability."""
    emit(observe(config_path, component, detailed=True), json_output)


@app.command(
    "verify", epilog="Example: nebius-cxcli tailscale verify ./config.yaml --failover --json"
)
@handled
def verify_command(
    config_path: Config,
    component: Component = None,
    failover: Annotated[
        bool,
        typer.Option(
            help="Explicitly stop the selected router with a 120-second guest restore timer."
        ),
    ] = False,
    json_output: Json = False,
) -> None:
    """Verify client HTTPS traffic and selected routes; optionally exercise bounded failover."""
    from .tailscale_verify import verify

    with operation(config_path, component, mutate=failover) as (owner, row, record, guests):
        for member, guest in guests.items():
            current = guest.call("status")
            if current.get("node_id") != record["members"][member].get("node_id") or not readiness(
                owner.api(row), current, desired(row, member)
            ):
                raise RuntimeError("Both configured members must be ready before verification")
            record["members"][member]["phase"] = "READY"
        target = owner.executor.report_paths.reports_dir / "tailscale-client-verification.json"
        protected_json(target, {"status": "PENDING", "reason": "Current verification in progress"})
        result = verify(
            row["tailscale"],
            record["members"],
            base=owner.executor.report_paths.project_dir,
            guests=guests,
            failover=failover,
        )
        protected_json(target, result)
        emit(result, json_output)


@app.command(
    "recover",
    epilog="Example: nebius-cxcli tailscale recover ./config.yaml --lock-owner EXACT_OWNER --writer-stopped",
)
@handled
def recover(
    config_path: Config,
    lock_owner: Annotated[
        str | None, typer.Option(help="Exact owner from the blocked operation's remote lock.")
    ] = None,
    operation_id: Annotated[
        str | None, typer.Option(help="Exact enrollment operation ID from doctor JSON.")
    ] = None,
    member: Annotated[str | None, typer.Option(help="Exact enrollment member, a or b.")] = None,
    replace_unused_key: Annotated[
        bool,
        typer.Option(help="Fence and revoke an unused operation; next deploy may issue a new key."),
    ] = False,
    key_id: Annotated[
        str | None, typer.Option(help="Verified key ID for an ambiguous creation response.")
    ] = None,
    writer_stopped: Annotated[
        bool, typer.Option(help="Confirm the previous CLI and Terraform writers have stopped.")
    ] = False,
    component: Component = None,
    json_output: Json = False,
) -> None:
    """Recover an exact stopped-writer lock or resume a selected enrollment operation."""
    from .deployment_cli import deployment_execution
    from .object_storage_transport import object_storage_transport
    from .tailscale_state import Journal
    from .terraform_backend import backend_settings_from_config

    cli, config, paths, row = load(config_path, component)
    if not writer_stopped:
        raise ValueError("Stop the previous writer and supply --writer-stopped before recovery")
    if not lock_owner and not operation_id:
        raise ValueError("Select --lock-owner or --operation-id with --member")
    if bool(operation_id) != bool(member) or (replace_unused_key or key_id) and not operation_id:
        raise ValueError("Enrollment recovery requires both --operation-id and --member")
    if key_id and not replace_unused_key:
        raise ValueError("--key-id requires --replace-unused-key")
    if operation_id and not replace_unused_key:
        assert_activation(row)
    settings = backend_settings_from_config(config)
    with (
        deployment_execution(
            config=config,
            paths=paths,
            target_ref="project",
            operation_id="Tailscale recovery",
            bootstrap_backend=False,
        ) as lease,
        object_storage_transport(
            settings, extra_env=cli._terraform_runtime_env(config)
        ) as transport,
    ):
        journal = Journal(transport, settings.key, lease.assert_held)
        if lock_owner:
            observed = transport.request("get_object", journal.lock_key, max_bytes=4096)
            if json.loads(observed.body) != {"owner": lock_owner}:
                raise RuntimeError("Recovery does not match the exact stopped writer")
            journal.owner, journal.lock_etag = lock_owner, observed.etag
            journal.release()
        result: dict[str, Any] = {"status": "RELEASED", "next": "Resume ordinary deploy"}
        if operation_id:
            if member is None:
                raise ValueError("Enrollment recovery requires --member")
            from .generated_manifest import load_generated_manifest
            from .tailscale_enrollment import KeyStore, recover_unused, resume
            from .tailscale_retirement import guest_for
            from .tailscale_ssh import asset

            executor = SimpleNamespace(
                cli=cli,
                config=config,
                paths=paths,
                report_paths=paths,
                manifest=load_generated_manifest(paths.generated_dir),
                options=SimpleNamespace(dry_run=False),
                lease=lease,
                runtime_env=cli._terraform_runtime_env(config),
            )
            with TailscaleDeployment(executor, settings=settings) as owner:
                active = owner.open()
                record = active.value["gateways"].get(row["instance_id"], {})
                own = record.get("members", {}).get(member, {})
                op = own.get("enrollment", {})
                if (
                    record.get("tailnet") != row["tailscale"]["tailnet"]
                    or (record.get("retiring") and not replace_unused_key)
                    or record.get("retired")
                    or op.get("id") != operation_id
                ):
                    raise RuntimeError(
                        "Recovery selector does not match an active enrollment operation"
                    )
                output = outputs(owner.raw_outputs(), row["instance_id"]).get(member, {})
                if any(
                    output.get(k) != op["binding"][k]
                    for k in (
                        "instance_id",
                        "boot_disk_id",
                        "public_allocation_id",
                        "network_id",
                        "subnet_id",
                    )
                ):
                    raise RuntimeError(
                        "Recovery cloud binding differs from the enrollment operation"
                    )
                with ComputeHostIdentityClient(row["inputs"]["parent_id"]) as sdk:
                    guest = guest_for(owner, row, output, sdk)
                    guest.install(asset(executor.manifest, row["instance_id"]))
                    store = KeyStore(paths.project_dir)
                    if replace_unused_key:
                        recover_unused(
                            active, own, guest, owner.api(row), store, key_id=key_id or ""
                        )
                        result = {
                            "status": "REVOKED",
                            "operation_id": operation_id,
                            "member": member,
                            "next": "Resume ordinary deploy for a new enrollment operation",
                        }
                    else:
                        from .tailscale_deploy import guest_ready

                        current = guest.call("status")
                        if current.get("routes") and not guest_ready(current, desired(row, member)):
                            raise RuntimeError(
                                "Use ordinary deploy for peer-gated routing or version changes"
                            )
                        enrollment_result = resume(
                            active,
                            own,
                            guest,
                            validate(row),
                            desired(row, member),
                            owner.api(row),
                            store,
                        )
                        result = {
                            "status": "ENROLLED" if own.get("node_id") else "PENDING",
                            "operation_id": operation_id,
                            "member": member,
                            "node_id": enrollment_result.get("node_id", ""),
                            "next": "Resume ordinary deploy",
                        }
        emit(result, json_output)
