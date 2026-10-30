"""Supported network reconciliation, credential-preserving refresh, and verification."""

from __future__ import annotations

import configparser
import os
import tempfile
from pathlib import Path

from .wireguard_access import client_access
from .wireguard_commands import load_wireguard_context
from .wireguard_networking import network_asset, remote_network
from .wireguard_secret_api import wireguard_api


def existing_backend_auth(config) -> None:
    from . import cli
    from .deployment_recovery import deployment_preview

    # These day-2 operations never create a backend or expand IAM permissions.
    with deployment_preview(True):
        cli._ensure_runtime_auth_material(config, need_terraform=True)
    cli._ensure_backend_s3_env_aliases()


def reconcile(config_path: Path, selector: str | None, credentials_file: str | None) -> None:
    from . import cli
    from .config_model import to_runtime_payload
    from .deployment_cli import deployment_execution
    from .generated_manifest import load_generated_manifest, source_config_digest
    from .object_storage_transport import object_storage_transport
    from .runtime_config import wrap_runtime_config
    from .terraform_backend import backend_settings_from_config
    from .wireguard_foundation import assert_ownership
    from .wireguard_journal import WireGuardJournal
    from .wireguard_networking import reconcile_guest

    payload, paths, component, row, project = load_wireguard_context(config_path, selector)
    config = wrap_runtime_config(to_runtime_payload(payload))
    manifest = load_generated_manifest(paths.generated_dir, read_only=True)
    if manifest["render"]["source_config_sha256"] != source_config_digest(config_path):
        raise RuntimeError("Source configuration changed; rerender before network reconciliation")
    asset = network_asset(manifest, component.instance_id)
    settings = backend_settings_from_config(config)
    existing_backend_auth(config)
    with (
        deployment_execution(
            config=config,
            paths=paths,
            target_ref="project",
            operation_id="WireGuard network reconciliation",
            bootstrap_backend=False,
        ) as lease,
        wireguard_api(credentials_file=credentials_file) as api,
        object_storage_transport(
            settings, extra_env=cli._terraform_runtime_env(config)
        ) as transport,
    ):
        api.admit_backend(settings)
        journal = WireGuardJournal(transport, project, component.instance_id)
        journal.acquire()
        try:
            assert_ownership(journal.value, settings, row)
            record = journal.value
            if (
                record.get("phase") != "ready"
                or record.get("generation") != row["access"]["generation"]
                or record.get("in_flight")
                or record.get("rotation", {}).get("phase") not in {None, "complete"}
            ):
                raise RuntimeError("Networking repair requires an idle, owned READY gateway")
            connection = record["connection"]
            api.verify_instance_binding(connection, record)
            from .wireguard_access import decode_connection

            _, current = api.payload(row["access"]["connection_secret_id"])
            if decode_connection(current) != connection:
                raise RuntimeError("Current gateway connection differs from its ownership journal")
            _, pair = api.payload(connection["login_secret_id"], connection["login_version_id"])
            lease.assert_held()
            record["network_revision"] = reconcile_guest(connection, pair, asset)
            api.verify_instance_binding(connection, record)
            journal.save()
        finally:
            journal.release()


def refresh(
    config_path: Path, selector: str | None, client_path: Path, credentials_file: str | None
) -> int:
    from .wireguard_verification import local_mtu_limit, read_client

    _payload, _paths, component, row, project = load_wireguard_context(config_path, selector)
    client = read_client(client_path)
    with (
        wireguard_api(credentials_file=credentials_file) as api,
        client_access(
            api, project=project, component=component.instance_id, access=row["access"]
        ) as ssh,
    ):
        response = remote_network(ssh, "refresh-client", {"public_key": client["public_key"]})
    remote = configparser.ConfigParser(interpolation=None)
    local = configparser.ConfigParser(interpolation=None)
    remote.read_string(response["config"])
    local.read_string(client["text"])
    if any(
        local[s][k] != remote[s][k]
        for s, k in (
            ("Interface", "PrivateKey"),
            ("Interface", "Address"),
            ("Peer", "PublicKey"),
            ("Peer", "PresharedKey"),
        )
    ):
        raise RuntimeError("Refresh would change client identity; no local file was changed")
    mtu = min(int(remote["Interface"]["MTU"]), local_mtu_limit(client))
    if mtu < 1280:
        raise RuntimeError("Local uplink cannot support the minimum WireGuard MTU")
    # Preserve all locally selected routes/DNS/options and credentials byte for byte.
    lines, section, inserted = [], "", False
    for line in client["text"].splitlines():
        if line.strip().startswith("["):
            section = line.strip()
        if section == "[Interface]" and line.partition("=")[0].strip().lower() == "mtu":
            continue
        lines.append(line)
        if section == "[Interface]" and not inserted:
            lines.append(f"MTU = {mtu}")
            inserted = True
    with tempfile.NamedTemporaryFile(dir=client_path.parent, delete=False) as out:
        staging = Path(out.name)
        try:
            os.fchmod(out.fileno(), 0o600)
            out.write(("\n".join(lines) + "\n").encode())
            out.flush()
            os.fsync(out.fileno())
            if read_client(client_path)["text"] != client["text"]:
                raise RuntimeError("Client file changed concurrently; retry refresh")
            os.replace(staging, client_path)
        finally:
            staging.unlink(missing_ok=True)
    return mtu


def verify(
    config_path: Path,
    selector: str | None,
    client_path: Path,
    credentials_file: str | None,
    *,
    target: str | None = None,
    kubeconfig: Path | None = None,
    context: str | None = None,
) -> dict:
    from . import cli
    from .generated_manifest import load_generated_manifest, runtime_config_from_manifest
    from .wireguard_verification import VerificationPending, probe, read_client, write_report

    _payload, paths, component, row, project = load_wireguard_context(config_path, selector)
    write_report(
        paths.reports_dir,
        [{"status": "PENDING", "reason": "Client verification prerequisites have not completed"}],
    )
    manifest = load_generated_manifest(paths.generated_dir, read_only=True)
    config = runtime_config_from_manifest(manifest)
    existing_backend_auth(config)
    targets = [
        t
        for t in manifest["execution"]["targets"]
        if t.get("component_id") == "mk8s" and (target is None or t["target_ref"] == target)
    ]
    if len(targets) != 1:
        raise VerificationPending("Select exactly one managed Kubernetes target with --target")
    outputs = cli.terraform_output_json(
        paths.infra_dir, extra_env=cli._terraform_runtime_env(config)
    )
    output = outputs[targets[0]["cluster_id_output_name"]]
    if (
        not isinstance(output, dict)
        or not isinstance(output.get("value"), str)
        or not output["value"]
    ):
        raise RuntimeError("Selected Kubernetes target has no managed cluster ID output")
    cluster_id = output["value"]
    spec = cli._mk8s_cluster_handoff_spec(
        config, cluster_id=cluster_id, access="internal", require_renewable_auth=True
    )
    with (
        wireguard_api(credentials_file=credentials_file) as api,
        client_access(
            api, project=project, component=component.instance_id, access=row["access"]
        ) as ssh,
    ):
        result = probe(
            read_client(client_path),
            ssh,
            server=spec.server,
            ca=spec.ca_pem,
            kubeconfig=kubeconfig,
            context=context,
        )
    return write_report(paths.reports_dir, [result])
