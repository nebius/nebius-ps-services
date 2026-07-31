from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from nebius_cxcli import soperator_controller_fencing as fencing
from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_controller_fencing import (
    CONTROLLER_AUTHORITY_LEASE,
    CONTROLLER_CENSUS_SCHEMA,
    CONTROLLER_FENCE_LABEL,
    CONTROLLER_FENCE_SCHEMA,
    CONTROLLER_INSPECTOR_NAMESPACE,
    ControllerFenceTarget,
    ControllerProcessBinding,
    ControllerRuntimeCensusTarget,
    controller_authority_lease,
    controller_fence_marker_sha256,
    controller_inspector_namespace_objects,
    controller_runtime_census_pod,
    controller_runtime_fence_pod,
    parse_controller_runtime_census_evidence,
    parse_controller_runtime_fence_evidence,
    validate_controller_authority_lease,
)


def _target() -> ControllerFenceTarget:
    return ControllerFenceTarget(
        node_name="controller-node-0",
        node_uid="node-uid-0",
        state_markers=("pvc-uid-0", "/mnt/controller-spool/current"),
    )


def _node(*, uid: str = "node-uid-0", resource_version: str = "11") -> dict[str, object]:
    return {
        "metadata": {
            "name": "controller-node-0",
            "uid": uid,
            "resourceVersion": resource_version,
        },
        "spec": {"providerID": "nebius://compute/controller-node-0"},
        "status": {"nodeInfo": {"systemUUID": "system-uuid-controller-node-0"}},
    }


def _process_binding(container_id: str = "a" * 64) -> ControllerProcessBinding:
    return ControllerProcessBinding(
        pod_namespace="soperator",
        pod_name="controller-0",
        pod_uid="controller-pod-uid",
        container_name="slurmctld",
        container_id=f"containerd://{container_id}",
        image_id="registry.example/slurm@sha256:" + "b" * 64,
    )


def _census_target(
    *bindings: ControllerProcessBinding,
) -> ControllerRuntimeCensusTarget:
    return ControllerRuntimeCensusTarget(
        node_name="controller-node-0",
        node_uid="node-uid-0",
        node_resource_version="11",
        provider_id="nebius://compute/controller-node-0",
        system_uuid="system-uuid-controller-node-0",
        expected_processes=bindings,
    )


def test_runtime_fence_pod_uses_dedicated_privileged_host_pid_boundary() -> None:
    pod = controller_runtime_fence_pod(
        campaign_fingerprint="a" * 64,
        authority_epoch="bridge-target-abcdef",
        image="registry.example/controller@sha256:" + "b" * 64,
        target=_target(),
    )

    assert pod["spec"]["nodeName"] == "controller-node-0"
    assert pod["spec"]["hostPID"] is True
    assert pod["metadata"]["namespace"] == CONTROLLER_INSPECTOR_NAMESPACE
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert "volumes" not in pod["spec"]
    container = pod["spec"]["containers"][0]
    assert container["securityContext"] == {
        "privileged": True,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": False,
        "runAsUser": 0,
    }
    assert "securityContext" not in pod["spec"]
    script = container["command"][2]
    assert 'for process in "$proc_root"/[0-9]*' in script
    assert "slurmctld_count" in script
    assert "writable_state_mount_count" in script
    assert "ignored_mountinfo_field" in script
    assert "| awk" not in script
    assert "hostPath" not in repr(pod)
    assert pod["metadata"]["labels"][CONTROLLER_FENCE_LABEL] == "true"
    assert container["resources"] == {
        "requests": {"cpu": "100m", "memory": "16Mi"},
        "limits": {"cpu": "1", "memory": "64Mi"},
    }


def test_inspector_namespace_is_dedicated_privileged_and_network_denied() -> None:
    namespace, policy = controller_inspector_namespace_objects(
        campaign_fingerprint="a" * 64,
        cluster_id="cluster-1",
    )

    assert namespace["metadata"]["name"] == CONTROLLER_INSPECTOR_NAMESPACE
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "privileged"
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/warn"] == ("restricted")
    assert policy["metadata"]["namespace"] == CONTROLLER_INSPECTOR_NAMESPACE
    assert policy["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }


@pytest.mark.parametrize(
    "marker",
    ("/mnt/controller spool/current", r"/mnt/controller\040spool/current", "\tstate"),
)
def test_runtime_fence_rejects_mountinfo_escaped_state_markers(marker: str) -> None:
    with pytest.raises(ValueError, match="unescaped mountinfo tokens"):
        ControllerFenceTarget(
            node_name="controller-node-0",
            node_uid="node-uid-0",
            state_markers=(marker,),
        )


def test_runtime_census_pod_binds_host_pid_scan_to_exact_cri_identity() -> None:
    target = _census_target(_process_binding())
    pod = controller_runtime_census_pod(
        campaign_fingerprint="c" * 64,
        purpose="census-controller",
        attempt_id="d" * 32,
        image="registry.example/inspector@sha256:" + "e" * 64,
        target=target,
    )

    assert pod["spec"]["nodeName"] == target.node_name
    assert pod["spec"]["hostPID"] is True
    assert pod["metadata"]["namespace"] == CONTROLLER_INSPECTOR_NAMESPACE
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert "volumes" not in pod["spec"]
    assert "securityContext" not in pod["spec"]
    container = pod["spec"]["containers"][0]
    assert container["resources"] == {
        "requests": {"cpu": "100m", "memory": "16Mi"},
        "limits": {"cpu": "1", "memory": "64Mi"},
    }
    assert container["securityContext"] == {
        "privileged": True,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": False,
        "runAsUser": 0,
    }
    script = container["command"][2]
    assert 'for process in "$proc_root"/[0-9]*' in script
    assert 'cat "$process/cgroup"' in script
    assert _process_binding().runtime_id in script
    assert "unexpected_process_count" in script


def _write_fake_process(
    proc_root: Path,
    *,
    pid: int,
    comm: str,
    exe: str,
    cgroup: str,
    mountinfo: str | None = None,
    cmdline: bytes | None = None,
    ppid: int = 1,
) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    (process / "comm").write_text(f"{comm}\n", encoding="utf-8")
    (process / "cmdline").write_bytes(cmdline or f"{exe}\0-D\0".encode())
    (process / "cgroup").write_text(f"0::{cgroup}\n", encoding="utf-8")
    (process / "status").write_text(
        f"Name:\t{comm}\nState:\tS (sleeping)\nPid:\t{pid}\nPPid:\t{ppid}\n",
        encoding="utf-8",
    )
    (process / "exe").symlink_to(exe)
    if mountinfo is not None:
        (process / "mountinfo").write_text(mountinfo, encoding="utf-8")


def _write_cat_fault_shim(
    tmp_path: Path,
    *,
    vanished_pid: int | None = None,
    vanished_mountinfo_pid: int | None = None,
    unreadable_pid: int | None = None,
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cases: list[str] = []
    if vanished_pid is not None:
        cases.append(f'  */{vanished_pid}/comm) rm -rf -- "${{1%/comm}}"; exit 1 ;;')
    if vanished_mountinfo_pid is not None:
        cases.append(
            f'  */{vanished_mountinfo_pid}/mountinfo) rm -rf -- "${{1%/mountinfo}}"; exit 1 ;;'
        )
    if unreadable_pid is not None:
        cases.append(f"  */{unreadable_pid}/comm) exit 1 ;;")
    shim = bin_dir / "cat"
    shim.write_text(
        '#!/bin/sh\ncase "$1" in\n' + "\n".join(cases) + '\nesac\nexec /bin/cat "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return bin_dir


def _write_stable_non_controller_process(proc_root: Path) -> None:
    _write_fake_process(
        proc_root,
        pid=1,
        comm="sh",
        exe="/bin/sh",
        cgroup="/system.slice/inspector-test.service",
        mountinfo="36 29 0:32 / / rw,relatime - ext4 /dev/root rw\n",
    )


def test_runtime_census_detects_generic_host_or_config_script_controller(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_fake_process(
        proc_root,
        pid=101,
        comm="slurmctld",
        exe="/usr/sbin/slurmctld",
        cgroup="/system.slice/customer-controller.service",
    )
    target = _census_target()
    script = fencing._controller_runtime_census_script(  # noqa: SLF001
        target,
        proc_root=str(proc_root),
    )

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-ec", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "slurmctld_count=1" in result.stdout
    assert "unexpected_process_count=1" in result.stdout
    with pytest.raises(ValueError, match="exact process/container exclusivity"):
        parse_controller_runtime_census_evidence(result.stdout, target=target)


def test_runtime_census_accepts_config_script_child_only_with_exact_cri_binding(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    binding = _process_binding()
    _write_fake_process(
        proc_root,
        pid=202,
        comm="slurmctld",
        exe="/opt/slurm/sbin/slurmctld",
        cgroup=f"/kubepods.slice/cri-containerd-{binding.runtime_id}.scope",
    )
    target = _census_target(binding)
    script = fencing._controller_runtime_census_script(  # noqa: SLF001
        target,
        proc_root=str(proc_root),
    )

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-ec", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    evidence = parse_controller_runtime_census_evidence(result.stdout, target=target)
    assert evidence.exclusive is True
    assert evidence.as_payload()["schema"] == CONTROLLER_CENSUS_SCHEMA


def test_runtime_census_ignores_exact_slurmscriptd_child_of_expected_controller(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    binding = _process_binding()
    cgroup = f"/kubepods.slice/cri-containerd-{binding.runtime_id}.scope"
    _write_fake_process(
        proc_root,
        pid=202,
        comm="slurmctld",
        exe="/usr/sbin/slurmctld",
        cgroup=cgroup,
    )
    _write_fake_process(
        proc_root,
        pid=203,
        ppid=202,
        comm="slurmscriptd",
        exe="/usr/sbin/slurmctld",
        cmdline=b"slurmctld: slurmscriptd\0",
        cgroup=cgroup,
    )
    target = _census_target(binding)

    result = subprocess.run(  # noqa: S603
        [
            "/bin/sh",
            "-ec",
            fencing._controller_runtime_census_script(  # noqa: SLF001
                target,
                proc_root=str(proc_root),
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "slurmctld_count=1" in result.stdout
    assert "ambiguous_process_count=0" in result.stdout


def test_runtime_census_rejects_changed_slurmscriptd_process_title(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    binding = _process_binding()
    cgroup = f"/kubepods.slice/cri-containerd-{binding.runtime_id}.scope"
    _write_fake_process(
        proc_root,
        pid=202,
        comm="slurmctld",
        exe="/usr/sbin/slurmctld",
        cgroup=cgroup,
    )
    _write_fake_process(
        proc_root,
        pid=203,
        ppid=202,
        comm="slurmscriptd",
        exe="/usr/sbin/slurmctld",
        cmdline=b"slurmctld: changed-helper\0",
        cgroup=cgroup,
    )
    target = _census_target(binding)

    result = subprocess.run(  # noqa: S603
        [
            "/bin/sh",
            "-ec",
            fencing._controller_runtime_census_script(  # noqa: SLF001
                target,
                proc_root=str(proc_root),
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "slurmctld_count=2" in result.stdout
    assert "ambiguous_process_count=1" in result.stdout


def test_runtime_census_rejects_container_id_prefix_collision(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    binding = _process_binding("a" * 32)
    _write_fake_process(
        proc_root,
        pid=203,
        comm="slurmctld",
        exe="/opt/slurm/sbin/slurmctld",
        cgroup=f"/kubepods.slice/cri-containerd-{binding.runtime_id}{'b' * 32}.scope",
    )
    target = _census_target(binding)
    script = fencing._controller_runtime_census_script(  # noqa: SLF001
        target,
        proc_root=str(proc_root),
    )

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-ec", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unexpected_process_count=1" in result.stdout
    assert "missing_expected_process_count=1" in result.stdout


@pytest.mark.parametrize("inspector", ["census", "fence"])
def test_runtime_inspector_ignores_pid_that_vanishes_during_comm_read(
    tmp_path: Path,
    inspector: str,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_stable_non_controller_process(proc_root)
    _write_fake_process(
        proc_root,
        pid=303,
        comm="short-lived",
        exe="/bin/sh",
        cgroup="/system.slice/short-lived.service",
        mountinfo="36 29 0:32 / / rw,relatime - ext4 /dev/root rw\n",
    )
    bin_dir = _write_cat_fault_shim(tmp_path, vanished_pid=303)
    if inspector == "census":
        script = fencing._controller_runtime_census_script(  # noqa: SLF001
            _census_target(),
            proc_root=str(proc_root),
        )
    else:
        script = fencing._controller_runtime_fence_script(  # noqa: SLF001
            _target(),
            proc_root=str(proc_root),
        )

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-ec", script],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/bin:/usr/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert "unreadable_process_count=0" in result.stdout
    assert not (proc_root / "303").exists()


@pytest.mark.parametrize("inspector", ["census", "fence"])
def test_runtime_inspector_rejects_persistent_unreadable_pid(
    tmp_path: Path,
    inspector: str,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_stable_non_controller_process(proc_root)
    _write_fake_process(
        proc_root,
        pid=404,
        comm="persistent",
        exe="/bin/sh",
        cgroup="/system.slice/persistent.service",
        mountinfo="36 29 0:32 / / rw,relatime - ext4 /dev/root rw\n",
    )
    bin_dir = _write_cat_fault_shim(tmp_path, unreadable_pid=404)
    if inspector == "census":
        script = fencing._controller_runtime_census_script(  # noqa: SLF001
            _census_target(),
            proc_root=str(proc_root),
        )
    else:
        script = fencing._controller_runtime_fence_script(  # noqa: SLF001
            _target(),
            proc_root=str(proc_root),
        )

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-ec", script],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/bin:/usr/bin"},
    )

    assert result.returncode != 0
    assert "unreadable_process_count=1" in result.stdout
    assert (proc_root / "404").is_dir()


def test_runtime_fence_ignores_pid_that_vanishes_during_mountinfo_read(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_stable_non_controller_process(proc_root)
    _write_fake_process(
        proc_root,
        pid=305,
        comm="short-lived",
        exe="/bin/sh",
        cgroup="/system.slice/short-lived.service",
        mountinfo="36 29 0:32 / / rw,relatime - ext4 /dev/root rw\n",
    )
    bin_dir = _write_cat_fault_shim(tmp_path, vanished_mountinfo_pid=305)
    script = fencing._controller_runtime_fence_script(  # noqa: SLF001
        _target(),
        proc_root=str(proc_root),
    )

    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-ec", script],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/bin:/usr/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert "unreadable_process_count=0" in result.stdout
    assert not (proc_root / "305").exists()


def test_runtime_fence_evidence_is_exact_and_fail_closed() -> None:
    target = _target()
    output = "\n".join(
        (
            f"schema={CONTROLLER_FENCE_SCHEMA}",
            f"node_name={target.node_name}",
            f"node_uid={target.node_uid}",
            "slurmctld_count=0",
            "writable_state_mount_count=0",
            "inspected_process_count=42",
            "unreadable_process_count=0",
            f"marker_sha256={controller_fence_marker_sha256(target.state_markers)}",
        )
    )

    evidence = parse_controller_runtime_fence_evidence(output, target=target)

    assert evidence.fenced is True
    assert evidence.as_payload()["fenced"] is True

    for field, value in (
        ("slurmctld_count", "1"),
        ("writable_state_mount_count", "1"),
        ("inspected_process_count", "0"),
        ("unreadable_process_count", "1"),
    ):
        changed = output.replace(f"{field}={getattr(evidence, field)}", f"{field}={value}")
        with pytest.raises(ValueError, match="did not prove"):
            parse_controller_runtime_fence_evidence(changed, target=target)


def test_authority_lease_binds_campaign_epoch_owner_and_immutable_identity() -> None:
    desired = controller_authority_lease(
        namespace="cxcli-soperator-upgrade-bridge",
        campaign_fingerprint="c" * 64,
        cluster_id="cluster-1",
        authority_epoch="target-singleton-abcdef",
        owner="target-singleton",
    )
    assert desired["metadata"]["name"] == CONTROLLER_AUTHORITY_LEASE
    live = copy.deepcopy(desired)
    live["metadata"].update({"uid": "lease-uid", "resourceVersion": "9"})

    binding = validate_controller_authority_lease(
        live,
        namespace="cxcli-soperator-upgrade-bridge",
        campaign_fingerprint="c" * 64,
        cluster_id="cluster-1",
        authority_epoch="target-singleton-abcdef",
        owner="target-singleton",
    )

    assert binding == {
        "uid": "lease-uid",
        "resource_version": "9",
        "holder_identity": "target-singleton-abcdef:target-singleton",
    }
    live["spec"]["holderIdentity"] = "bridge-target-abcdef:bridge-target"
    with pytest.raises(ValueError, match="drifted"):
        validate_controller_authority_lease(
            live,
            namespace="cxcli-soperator-upgrade-bridge",
            campaign_fingerprint="c" * 64,
            cluster_id="cluster-1",
            authority_epoch="target-singleton-abcdef",
            owner="target-singleton",
        )


def test_authority_lease_transition_reconciles_a_lost_patch_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: None,
    )
    namespace = "cxcli-soperator-upgrade-bridge"
    campaign = "d" * 64
    lease = controller_authority_lease(
        namespace=namespace,
        campaign_fingerprint=campaign,
        cluster_id="cluster-1",
        authority_epoch="bridge-target-aaaaaaaaaaaa",
        owner="bridge-target",
    )
    lease["metadata"].update({"uid": "lease-uid", "resourceVersion": "7"})
    patch_calls = 0
    node_census = [
        {
            "node_name": "controller-node-0",
            "node_uid": "node-uid-0",
            "node_resource_version": "11",
            "provider_id": "nebius://compute/controller-node-0",
            "system_uuid": "system-uuid-controller-node-0",
        }
    ]

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> migration.SoperatorMigrationCommandResult:
        nonlocal patch_calls
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if "get" in command:
            if command[command.index("get") + 1] == "nodes":
                return migration.SoperatorMigrationCommandResult(
                    command,
                    0,
                    json.dumps({"items": [_node()]}),
                    "",
                )
            return migration.SoperatorMigrationCommandResult(
                command,
                0,
                json.dumps(lease),
                "",
            )
        assert "patch" in command
        patch_calls += 1
        patch = json.loads(command[command.index("-p") + 1])
        lease["metadata"]["annotations"]["nebius.ai/cxcli-authority-epoch"] = patch[-2]["value"]
        lease["spec"]["holderIdentity"] = patch[-1]["value"]
        lease["metadata"]["resourceVersion"] = "8"
        raise TimeoutError("response lost after API accepted patch")

    journal: dict[str, object] = {
        "namespace": namespace,
        "campaign_fingerprint": campaign,
        "cluster_id": "cluster-1",
        "authority_lease_transitions": [],
    }
    with pytest.raises(TimeoutError, match="response lost"):
        migration._transition_controller_authority_lease(  # noqa: SLF001
            journal=journal,
            from_epoch="bridge-target-aaaaaaaaaaaa",
            from_owner="bridge-target",
            to_epoch="target-aaaaaaaaaaaa",
            to_owner="target-singleton",
            kube_context="test-context",
            command_runner=runner,
            checkpoint_writer=lambda: None,
            runtime_node_census=node_census,
        )

    binding = migration._transition_controller_authority_lease(  # noqa: SLF001
        journal=journal,
        from_epoch="bridge-target-aaaaaaaaaaaa",
        from_owner="bridge-target",
        to_epoch="target-aaaaaaaaaaaa",
        to_owner="target-singleton",
        kube_context="test-context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
        runtime_node_census=node_census,
    )

    assert patch_calls == 1
    assert binding["holder_identity"] == "target-aaaaaaaaaaaa:target-singleton"
    transition = journal["authority_lease_transitions"][0]
    assert transition["state"] == "accepted"
    assert transition["accepted_by_live_reconciliation"] is True


def test_authority_lease_cas_revalidates_node_provider_identity_immediately_before_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: None,
    )
    namespace = "cxcli-soperator-upgrade-bridge"
    lease = controller_authority_lease(
        namespace=namespace,
        campaign_fingerprint="f" * 64,
        cluster_id="cluster-1",
        authority_epoch="bridge-source-aaaaaaaaaaaa",
        owner="bridge-source",
    )
    lease["metadata"].update({"uid": "lease-uid", "resourceVersion": "7"})
    patch_calls = 0

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> migration.SoperatorMigrationCommandResult:
        nonlocal patch_calls
        del input_text, timeout_seconds, check
        command = tuple(str(item) for item in args)
        if "patch" in command:
            patch_calls += 1
            return migration.SoperatorMigrationCommandResult(command, 0, "", "")
        resource = command[command.index("get") + 1]
        payload = {"items": [_node(uid="replacement-node-uid")]} if resource == "nodes" else lease
        return migration.SoperatorMigrationCommandResult(command, 0, json.dumps(payload), "")

    with pytest.raises(RuntimeError, match="Node/provider identity changed"):
        migration._transition_controller_authority_lease(  # noqa: SLF001
            journal={
                "namespace": namespace,
                "campaign_fingerprint": "f" * 64,
                "cluster_id": "cluster-1",
                "authority_lease_transitions": [],
            },
            from_epoch="bridge-source-aaaaaaaaaaaa",
            from_owner="bridge-source",
            to_epoch="bridge-target-aaaaaaaaaaaa",
            to_owner="bridge-target",
            kube_context="test-context",
            command_runner=runner,
            checkpoint_writer=lambda: None,
            runtime_node_census=(
                {
                    "node_name": "controller-node-0",
                    "node_uid": "node-uid-0",
                    "node_resource_version": "11",
                    "provider_id": "nebius://compute/controller-node-0",
                    "system_uuid": "system-uuid-controller-node-0",
                },
            ),
        )
    assert patch_calls == 0


@pytest.mark.parametrize("live_names", (("controller-node-0", "extra-node"), ()))
def test_authority_node_revalidation_rejects_full_set_additions_and_removals(
    monkeypatch: pytest.MonkeyPatch,
    live_names: tuple[str, ...],
) -> None:
    def node(name: str) -> dict[str, object]:
        return {
            "metadata": {"name": name, "uid": f"uid-{name}", "resourceVersion": "12"},
            "spec": {"providerID": f"nebius://compute/{name}"},
            "status": {"nodeInfo": {"systemUUID": f"system-{name}"}},
        }

    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"items": [node(name) for name in live_names]},
    )
    census = (
        {
            "node_name": "controller-node-0",
            "node_uid": "uid-controller-node-0",
            "node_resource_version": "11",
            "provider_id": "nebius://compute/controller-node-0",
            "system_uuid": "system-controller-node-0",
            "node_identity_sha256": migration._controller_runtime_node_identity_sha256(  # noqa: SLF001
                {
                    "node_uid": "uid-controller-node-0",
                    "provider_id": "nebius://compute/controller-node-0",
                    "system_uuid": "system-controller-node-0",
                }
            ),
        },
    )

    with pytest.raises(RuntimeError, match="Node membership changed"):
        migration._revalidate_controller_runtime_census_nodes(  # noqa: SLF001
            node_census=census,
            kube_context="test-context",
            command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected runner"),
        )


def test_runtime_fence_node_revalidation_accepts_unrelated_live_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def node(name: str) -> dict[str, object]:
        return {
            "metadata": {"name": name, "uid": f"uid-{name}", "resourceVersion": "12"},
            "spec": {"providerID": f"nebius://compute/{name}"},
            "status": {"nodeInfo": {"systemUUID": f"system-{name}"}},
        }

    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"items": [node("controller-node-0"), node("unrelated-worker")]},
    )
    fence = (
        {
            "node_name": "controller-node-0",
            "node_uid": "uid-controller-node-0",
            "node_resource_version": "11",
            "provider_id": "nebius://compute/controller-node-0",
            "system_uuid": "system-controller-node-0",
            "node_identity_sha256": migration._controller_runtime_node_identity_sha256(  # noqa: SLF001
                {
                    "node_uid": "uid-controller-node-0",
                    "provider_id": "nebius://compute/controller-node-0",
                    "system_uuid": "system-controller-node-0",
                }
            ),
        },
    )

    observed = migration._revalidate_controller_runtime_fence_nodes(  # noqa: SLF001
        node_fence=fence,
        kube_context="test-context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected runner"),
    )

    assert [item["node_name"] for item in observed] == ["controller-node-0"]


def test_runtime_census_partial_bulk_apply_always_deletes_exact_inspector_pods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _node()
    applied_names: list[str] = []
    calls: list[tuple[str, ...]] = []

    def json_from_command(
        _runner: object,
        args: Sequence[str],
        **_kwargs: object,
    ) -> dict[str, object]:
        command = tuple(str(item) for item in args)
        if "pods" in command:
            return {"items": []}
        if "nodes" in command:
            return {"items": [node]}
        raise AssertionError(command)

    def partial_apply(**kwargs: object) -> None:
        objects = kwargs["objects"]
        assert isinstance(objects, tuple)
        applied_names.extend(str(item["metadata"]["name"]) for item in objects)
        raise RuntimeError("partial bulk apply")

    def runner(
        args: Sequence[str],
        **_kwargs: object,
    ) -> migration.SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        calls.append(command)
        return migration.SoperatorMigrationCommandResult(command, 0, "", "")

    monkeypatch.setattr(migration, "_json_from_command", json_from_command)
    monkeypatch.setattr(migration, "_kubectl_apply_objects", partial_apply)
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (False, {}),
    )

    with pytest.raises(RuntimeError, match="partial bulk apply"):
        migration._prove_controller_runtime_process_census(  # noqa: SLF001
            expected_pods=(),
            proof_label="partial-apply-cleanup",
            campaign_fingerprint="f" * 64,
            kube_context="test-context",
            command_runner=runner,
        )

    assert len(applied_names) == 1
    delete = next(command for command in calls if "delete" in command)
    assert delete[delete.index("-n") + 1] == CONTROLLER_INSPECTOR_NAMESPACE
    assert applied_names[0] in delete
    assert "--wait=true" in delete


def test_runtime_census_log_read_retries_transient_malformed_transport_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _census_target()
    valid = "\n".join(
        (
            f"schema={CONTROLLER_CENSUS_SCHEMA}",
            f"node_name={target.node_name}",
            f"node_uid={target.node_uid}",
            "expected_process_count=0",
            "slurmctld_count=0",
            "matched_expected_process_count=0",
            "unexpected_process_count=0",
            "missing_expected_process_count=0",
            "ambiguous_process_count=0",
            "inspected_process_count=1",
            "unreadable_process_count=0",
            f"expected_bindings_sha256={target.expected_bindings_sha256}",
            "",
        )
    )
    outputs = iter(("transient proxy output", valid))
    commands: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        **_kwargs: object,
    ) -> migration.SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        commands.append(command)
        return migration.SoperatorMigrationCommandResult(command, 0, next(outputs), "")

    monkeypatch.setattr(migration.time, "sleep", lambda _seconds: None)

    evidence = migration._collect_controller_runtime_census_evidence(  # noqa: SLF001
        pod_name="census-pod",
        target=target,
        proof_label="test census",
        kube_context="test-context",
        command_runner=runner,
    )

    assert evidence.exclusive is True
    assert len(commands) == 2
    assert all("--request-timeout=20s" in command for command in commands)


def test_fresh_runtime_fence_attempt_does_not_reuse_verified_resume_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target()
    namespace = "cxcli-soperator-upgrade-bridge"
    journal: dict[str, object] = {
        "namespace": namespace,
        "campaign_fingerprint": "e" * 64,
        "runtime_fence_proofs": [],
    }
    applied_pods: dict[str, dict[str, object]] = {}
    slurmctld_count = 0
    node_get_count = 0
    replace_node_on_get = 0
    commands: list[tuple[str, ...]] = []

    def apply_objects(**kwargs: object) -> None:
        objects = kwargs["objects"]
        assert isinstance(objects, tuple)
        pod = copy.deepcopy(objects[0])
        metadata = pod["metadata"]
        assert isinstance(metadata, dict)
        pod_name = str(metadata["name"])
        metadata["uid"] = f"uid-{len(applied_pods)}"
        pod["status"] = {
            "phase": "Running",
            "containerStatuses": [{"name": "inspector", "ready": True}],
        }
        applied_pods[pod_name] = pod

    def json_from_command(
        _runner: object,
        args: Sequence[str],
        **_kwargs: object,
    ) -> dict[str, object]:
        nonlocal node_get_count
        command = tuple(args)
        if "node" in command:
            node_get_count += 1
            if node_get_count == replace_node_on_get:
                return _node(uid="replacement-node-uid", resource_version="99")
            return _node(resource_version=str(10 + node_get_count))
        pod_name = command[command.index("pod") + 1]
        return copy.deepcopy(applied_pods[pod_name])

    def runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> migration.SoperatorMigrationCommandResult:
        del input_text, timeout_seconds, check
        command = tuple(args)
        commands.append(command)
        if "logs" not in command:
            return migration.SoperatorMigrationCommandResult(command, 0, "", "")
        output = "\n".join(
            (
                f"schema={CONTROLLER_FENCE_SCHEMA}",
                f"node_name={target.node_name}",
                f"node_uid={target.node_uid}",
                f"slurmctld_count={slurmctld_count}",
                "writable_state_mount_count=0",
                "inspected_process_count=42",
                "unreadable_process_count=0",
                f"marker_sha256={controller_fence_marker_sha256(target.state_markers)}",
            )
        )
        return migration.SoperatorMigrationCommandResult(command, 0, output, "")

    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (False, {}),
    )
    monkeypatch.setattr(migration, "_kubectl_apply_objects", apply_objects)
    monkeypatch.setattr(migration, "_json_from_command", json_from_command)

    first = migration._prove_controller_runtime_fence(  # noqa: SLF001
        journal=journal,
        boundary="writer-pre-scale",
        targets=(target,),
        image="registry.example/controller@sha256:" + "f" * 64,
        kube_context="test-context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
        fresh_attempt=True,
    )
    assert first[0]["slurmctld_count"] == 0
    assert first[0]["node_resource_version_before"] == "11"
    assert first[0]["node_resource_version_after"] == "12"

    replace_node_on_get = node_get_count + 2
    with pytest.raises(RuntimeError, match="exact non-deleting Node"):
        migration._prove_controller_runtime_fence(  # noqa: SLF001
            journal=journal,
            boundary="writer-pre-authority-node-race",
            targets=(target,),
            image="registry.example/controller@sha256:" + "f" * 64,
            kube_context="test-context",
            command_runner=runner,
            checkpoint_writer=lambda: None,
            fresh_attempt=True,
        )
    replace_node_on_get = 0

    slurmctld_count = 1
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="did not prove process and writable-mount absence",
    ):
        migration._prove_controller_runtime_fence(  # noqa: SLF001
            journal=journal,
            boundary="writer-pre-scale",
            targets=(target,),
            image="registry.example/controller@sha256:" + "f" * 64,
            kube_context="test-context",
            command_runner=runner,
            checkpoint_writer=lambda: None,
            fresh_attempt=True,
        )

    slurmctld_count = 0
    resumed = migration._prove_controller_runtime_fence(  # noqa: SLF001
        journal=journal,
        boundary="writer-pre-scale",
        targets=(target,),
        image="registry.example/controller@sha256:" + "f" * 64,
        kube_context="test-context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
        fresh_attempt=True,
    )
    assert resumed[0]["slurmctld_count"] == 0

    proofs = journal["runtime_fence_proofs"]
    assert isinstance(proofs, list)
    assert len(proofs) == 3
    assert {proof["purpose"] for proof in proofs} == {
        "writer-pre-scale",
        "writer-pre-authority-node-race",
    }
    assert len({proof["attempt_id"] for proof in proofs}) == 3
    assert len({proof["boundary"] for proof in proofs}) == 3
    assert len(applied_pods) == 3

    creation_waits = [
        command for command in commands if "wait" in command and "--for=create" in command
    ]
    assert len(creation_waits) == 4
    assert all("--timeout=2m" in command for command in creation_waits)


def test_runtime_fence_batches_independent_node_inspectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = (
        _target(),
        ControllerFenceTarget(
            node_name="controller-node-1",
            node_uid="node-uid-1",
            state_markers=("pvc-uid-0", "/mnt/controller-spool/current"),
        ),
    )
    journal: dict[str, object] = {
        "namespace": "cxcli-soperator-upgrade-bridge",
        "campaign_fingerprint": "e" * 64,
        "runtime_fence_proofs": [],
    }
    applied_pods: dict[str, dict[str, object]] = {}
    applied_batches: list[tuple[str, ...]] = []
    commands: list[tuple[str, ...]] = []

    def node(node_name: str) -> dict[str, object]:
        suffix = node_name.rsplit("-", 1)[-1]
        return {
            "metadata": {
                "name": node_name,
                "uid": f"node-uid-{suffix}",
                "resourceVersion": "11",
            },
            "spec": {"providerID": f"nebius://compute/{node_name}"},
            "status": {"nodeInfo": {"systemUUID": f"system-uuid-{node_name}"}},
        }

    def apply_objects(**kwargs: object) -> None:
        objects = kwargs["objects"]
        assert isinstance(objects, tuple)
        applied_batches.append(tuple(str(item["metadata"]["name"]) for item in objects))
        for item in objects:
            pod = copy.deepcopy(item)
            metadata = pod["metadata"]
            assert isinstance(metadata, dict)
            pod_name = str(metadata["name"])
            metadata["uid"] = f"uid-{pod_name}"
            pod["status"] = {
                "phase": "Running",
                "containerStatuses": [{"name": "inspector", "ready": True}],
            }
            applied_pods[pod_name] = pod

    def json_from_command(
        _runner: object,
        args: Sequence[str],
        **_kwargs: object,
    ) -> dict[str, object]:
        command = tuple(args)
        if "node" in command:
            return node(command[command.index("node") + 1])
        pod_name = command[command.index("pod") + 1]
        return copy.deepcopy(applied_pods[pod_name])

    def runner(
        args: Sequence[str],
        **_kwargs: object,
    ) -> migration.SoperatorMigrationCommandResult:
        command = tuple(str(item) for item in args)
        commands.append(command)
        if "logs" not in command:
            return migration.SoperatorMigrationCommandResult(command, 0, "", "")
        pod_name = command[command.index("logs") + 1].removeprefix("pod/")
        pod = applied_pods[pod_name]
        node_name = str(pod["spec"]["nodeName"])
        target = next(item for item in targets if item.node_name == node_name)
        output = "\n".join(
            (
                f"schema={CONTROLLER_FENCE_SCHEMA}",
                f"node_name={target.node_name}",
                f"node_uid={target.node_uid}",
                "slurmctld_count=0",
                "writable_state_mount_count=0",
                "inspected_process_count=42",
                "unreadable_process_count=0",
                f"marker_sha256={controller_fence_marker_sha256(target.state_markers)}",
            )
        )
        return migration.SoperatorMigrationCommandResult(command, 0, output, "")

    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (False, {}),
    )
    monkeypatch.setattr(migration, "_kubectl_apply_objects", apply_objects)
    monkeypatch.setattr(migration, "_json_from_command", json_from_command)

    results = migration._prove_controller_runtime_fence(  # noqa: SLF001
        journal=journal,
        boundary="cluster-wide-pre-scale",
        targets=targets,
        image="registry.example/controller@sha256:" + "f" * 64,
        kube_context="test-context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
        fresh_attempt=True,
    )

    assert {item["node_name"] for item in results} == {
        "controller-node-0",
        "controller-node-1",
    }
    assert len(applied_batches) == 1
    assert len(applied_batches[0]) == 2
    creation_waits = [
        command for command in commands if "wait" in command and "--for=create" in command
    ]
    readiness_waits = [
        command for command in commands if "wait" in command and "--for=condition=Ready" in command
    ]
    assert len(creation_waits) == 1
    assert len(readiness_waits) == 1
    assert sum(item.startswith("pod/") for item in creation_waits[0]) == 2
    assert sum(item.startswith("pod/") for item in readiness_waits[0]) == 2
