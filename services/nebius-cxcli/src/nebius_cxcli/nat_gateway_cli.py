"""NAT observation and explicit recovery controls; never provision infrastructure."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Annotated, Any

import typer

from .nat_gateway import nat_rows, validate_nat_config
from .nat_gateway_deploy import module_outputs, verify_guest
from .nat_gateway_sdk import NatGatewaySDK, NatOperationFailed
from .nat_gateway_state import NatState
from .object_storage_transport import object_storage_transport
from .runtime_config import to_plain_data
from .terraform_backend import backend_settings_from_config, ensure_state_bucket

app = typer.Typer(
    help="Inspect NAT routing, refresh reviewed policies, and recover interrupted ownership.",
    no_args_is_help=True,
)


@contextmanager
def context(config_path: Path, *, readonly: bool = True):
    from . import cli

    config, paths = cli._load_context_readonly(config_path)
    settings = backend_settings_from_config(config)
    ensure_state_bucket(settings, create=False)
    cli._ensure_backend_s3_env_aliases()
    env = cli._terraform_runtime_env(config)
    with object_storage_transport(settings, extra_env=env) as transport:
        state = NatState(transport, settings.key, readonly=readonly)
        yield cli, config, paths, env, state


@app.command(
    "status",
    epilog="Examples: nebius-cxcli nat status ./deployments/tenant/project/config.yaml | nebius-cxcli nat status ./deployments/tenant/project/config.yaml --ssh-known-hosts-file ./trusted_hosts",
)
def status(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help="Project config.yaml; its sibling generated directory contains Terraform outputs.",
        ),
    ],
    ssh_known_hosts_file: Annotated[
        Path | None, typer.Option(help="Also verify guest readiness using these trusted host keys.")
    ] = None,
    ssh_private_key: Annotated[
        Path | None, typer.Option(help="SSH private key file; defaults to the SSH agent.")
    ] = None,
) -> None:
    """Read live gateway and route state. VM health alone does not prove workload egress."""
    try:
        with context(config_path) as (cli, config, paths, env, state):
            outputs = cli.terraform_output_json(paths.infra_dir, extra_env=env, initialize=True)
            rows = []
            for row in nat_rows(to_plain_data(config)):
                record = state.value["gateways"].get(row["instance_id"], {})
                own = module_outputs(outputs, row["instance_id"])
                result: dict[str, Any] = {
                    "instance": row["instance_id"],
                    "phase": record.get("phase", "unprepared"),
                    "guest": "not checked",
                    "workload_egress": "not measured",
                    "routing": [],
                }
                activation = record.get("activation", {})
                result["last_activation"] = {
                    "id": activation.get("id"),
                    "phase": activation.get("phase"),
                }
                result["probe_results"] = [
                    {
                        "subnet_id": probe["subnet_id"],
                        "result": probe.get("result", "inconclusive"),
                        "cleanup": probe.get("cleanup", "pending"),
                    }
                    for probe in activation.get("probes", {}).values()
                ]
                if (
                    activation.get("phase") == "complete"
                    and result["probe_results"]
                    and all(
                        isinstance(p["result"], dict)
                        and p["result"].get("ok") is True
                        and p["cleanup"] == "complete"
                        for p in result["probe_results"]
                    )
                ):
                    result["workload_egress"] = (
                        "verified during last activation; not a continuous health check"
                    )
                if own:
                    inputs = {
                        **row["inputs"],
                        "network_id": record.get("network_id") or row["inputs"].get("network_id"),
                    }
                    binding = row.get("bindings", {}).get("inputs.gateway_subnet_id")
                    if binding:
                        inputs["gateway_subnet_id"] = module_outputs(
                            outputs, binding["source_instance"]
                        )["subnets"][binding["key"]]["id"]
                    with NatGatewaySDK(inputs["parent_id"]) as sdk:
                        result["gateway"] = sdk.gateway(own, inputs)
                        for key, policy in row["routing"]["policies"].items():
                            sdk.workload_policy(
                                own["route_table_ids"][key],
                                inputs["network_id"],
                                policy,
                                own["private_allocation_id"],
                            )
                        result["policies"] = "verified"
                        for target in record.get("targets", {}).values():
                            subnet = sdk.subnet(target["subnet_id"], inputs["network_id"])
                            expected = (
                                own["route_table_ids"][target["policy"]]
                                if row["routing"]["enabled"]
                                else target["original"]
                            )
                            result["routing"].append(
                                {
                                    "subnet_id": subnet.metadata.id,
                                    "explicit_table": subnet.spec.route_table_id,
                                    "effective_table": subnet.status.route_table.id,
                                    "pending": bool(target.get("pending")),
                                    "converged": subnet.spec.route_table_id == expected
                                    and (not expected or subnet.status.route_table.id == expected),
                                }
                            )
                    if ssh_known_hosts_file is not None:
                        result["guest"] = verify_guest(
                            own,
                            inputs,
                            known_hosts=ssh_known_hosts_file,
                            private_key=ssh_private_key,
                        )
                rows.append(result)
            cli.console.print(
                json.dumps({"gateways": rows, "lock": state.read(state.lock_key)}, indent=2),
                markup=False,
            )
    except Exception as exc:
        from . import cli

        cli._exit_with_error(exc)


@app.command(
    "refresh-routes",
    epilog="Examples: nebius-cxcli nat refresh-routes ./deployments/tenant/project/config.yaml | nebius-cxcli nat refresh-routes ./deployments/tenant/project/config.yaml",
)
def refresh_routes(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help="Project config.yaml; its sibling generated directory contains Terraform outputs.",
        ),
    ],
) -> None:
    """Refresh source policy snapshots in config only, after routes have been detached."""
    try:
        with context(config_path) as (cli, config, _paths, _env, state):
            source_bytes = config_path.read_bytes()
            payload = cli._load_config_payload(config_path.resolve())
            updated = copy.deepcopy(payload)
            rows = nat_rows(updated)
            if not rows:
                raise ValueError("No enabled NAT component")
            replacements = {}
            for row in rows:
                record = state.value["gateways"].get(row["instance_id"], {})
                if row["routing"]["enabled"] or record.get("phase", "detached") != "detached":
                    raise RuntimeError(
                        "Set routing.enabled=false and deploy restoration before refreshing routes"
                    )
                network = record.get("network_id") or row["inputs"].get("network_id")
                from .nat_gateway import target_policy_key

                policies = {}
                with NatGatewaySDK(row["inputs"]["parent_id"]) as sdk:
                    for target in row["routing"]["targets"]:
                        policy = row["routing"]["policies"][target["policy"]]
                        key = target_policy_key(target)
                        policies[key] = (
                            sdk.route_policy(policy["source_table_id"], network)
                            if policy["source_table_id"]
                            else policy
                        )
                        target["policy"] = key
                row["routing"]["policies"] = policies
                replacements[row["instance_id"]] = row
            updated["infra"]["components"] = [
                replacements.get(r["instance_id"], r) for r in updated["infra"]["components"]
            ]
            validate_nat_config(updated)
            # Preserve the project's ordinary config serialization and compare before publication.
            if cli._load_config_payload(config_path.resolve()) != payload:
                raise RuntimeError("Configuration changed during route refresh")
            cli._write_runtime_payload_config(
                config_path.resolve(), updated, expected_bytes=source_bytes
            )
            cli.console.print(
                "Source routes refreshed in config only. Review the diff, enable routing, then render and deploy."
            )
    except Exception as exc:
        from . import cli

        cli._exit_with_error(exc)


@app.command(
    "unlock",
    epilog="Examples: nebius-cxcli nat unlock ./deployments/tenant/project/config.yaml --owner LOCK_OWNER | nebius-cxcli nat unlock ./deployments/tenant/project/config.yaml --owner LOCK_OWNER --confirm-writers-stopped",
)
def unlock(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help="Project config.yaml; its sibling generated directory contains Terraform outputs.",
        ),
    ],
    owner: Annotated[str, typer.Option(help="Exact lock owner reported by nat status.")],
    confirm_writers_stopped: Annotated[
        bool,
        typer.Option(
            help="Confirm every previous writer and its Terraform/SDK processes have stopped."
        ),
    ] = False,
) -> None:
    """Conditionally release an abandoned lock; elapsed time never authorizes takeover."""
    try:
        if not confirm_writers_stopped:
            raise ValueError("Stop all prior writers and confirm with --confirm-writers-stopped")
        with context(config_path) as (cli, _config, _paths, _env, state):
            state.unlock(owner)
            cli.console.print(
                "Matching NAT lock released. Pending operation records remain intact."
            )
    except Exception as exc:
        from . import cli

        cli._exit_with_error(exc)


@app.command(
    "reconcile",
    epilog="Examples: nebius-cxcli nat reconcile ./deployments/tenant/project/config.yaml | nebius-cxcli nat reconcile ./deployments/tenant/project/config.yaml --confirm-requests-settled",
)
def reconcile(
    config_path: Annotated[
        Path,
        typer.Argument(
            metavar="CONFIG_YAML",
            help="Project config.yaml; its sibling generated directory contains Terraform outputs.",
        ),
    ],
    confirm_requests_settled: Annotated[
        bool,
        typer.Option(
            help="Confirm prior API requests are terminal, no writer is running, and all probe SSH collectors and descendants have stopped on their original runners."
        ),
    ] = False,
) -> None:
    """Reconcile settled attachment and temporary probe outcomes without mutations."""
    try:
        if not confirm_requests_settled:
            raise ValueError(
                "Use --confirm-requests-settled only after prior API requests are terminal, writers "
                "and probe SSH collectors (including descendants) have stopped on their original "
                "runners; observation alone cannot prove this"
            )
        with context(config_path, readonly=False) as (cli, _config, _paths, _env, state):
            state.acquire()
            try:
                for record in state.value["gateways"].values():
                    with NatGatewaySDK(record["project_id"]) as sdk:
                        from .nat_gateway_probe_resources import ProbeResources

                        for probe in record.get("activation", {}).get("probes", {}).values():
                            ProbeResources(sdk, probe, state.save, state.assert_held).reconcile(
                                ssh_collectors_stopped=confirm_requests_settled
                            )
                        for target in record["targets"].values():
                            pending = target.get("pending")
                            if not pending or target["owner"] != "external":
                                continue
                            if pending.get("operation_id"):
                                # Reconciliation still requires an allowed observed attachment.
                                with suppress(NatOperationFailed):
                                    sdk.wait_operation(pending["operation_id"])
                            actual = sdk.subnet(
                                target["subnet_id"], record["network_id"]
                            ).spec.route_table_id
                            if actual == pending["desired"]:
                                target["installed"] = actual if actual != target["original"] else ""
                            elif actual != pending["expected"]:
                                raise RuntimeError(
                                    "NAT attachment changed outside the pending operation; preserve the record and resolve ownership"
                                )
                            target["pending"] = None
                            state.save()
            finally:
                state.release()
            cli.console.print(
                "Pending attachments and probe outcomes reconciled without resource mutations. Rerun deploy for restoration and cleanup."
            )
    except Exception as exc:
        from . import cli

        cli._exit_with_error(exc)
