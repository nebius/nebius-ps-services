from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

import pytest
import yaml

from nebius_cxcli.soperator_runtime_objects import RuntimeObjects
from nebius_cxcli.soperator_sssd_runtime import (
    SSSD_CONFIG_FILE_ENV,
    SSSD_LDAP_CA_FILE_ENV,
    ensure_soperator_sssd_runtime,
)


@pytest.mark.parametrize("input_kind", ["values", "sssd"])
def test_fifo_input_fails_without_waiting_for_a_writer_or_mutating_runtime(tmp_path, input_kind):
    fifo = tmp_path / "private-input"
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from nebius_cxcli.soperator_values import read_soperator_values_file
import nebius_cxcli.soperator_sssd_runtime as sssd

class NoWrites:
    def __init__(self, **kwargs): pass
    def complete(self, *args): return False
    def create(self, *args): raise AssertionError('unexpected runtime mutation')

sssd.RuntimeObjects = NoWrites
try:
    if sys.argv[1] == 'values':
        read_soperator_values_file(Path(sys.argv[2]))
    else:
        sssd.ensure_soperator_sssd_runtime(
            {'apps': {'charts': [{'id': 'soperator', 'instance_id': 'cluster1',
                'enabled': True, 'values': {'sssd': {'enabled': True,
                'sssdConfSecretRefName': 'directory'}}}]}},
            target_ref='cluster1', namespaces={'slurmCluster': 'consumer'},
            extra_env={sssd.SSSD_CONFIG_FILE_ENV + '_CLUSTER1': sys.argv[2]},
            assert_authority=lambda: None,
        )
except (RuntimeError, ValueError) as error:
    assert sys.argv[2] not in str(error)
    print('rejected')
else:
    raise AssertionError('special file was accepted')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, input_kind, str(fifo)],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    assert result.stdout.strip() == "rejected"


def _runtime(
    monkeypatch, *, existing=None, authority=lambda: None, failure=False, namespace_exists=True
):
    objects = dict(existing or {})
    writes = []
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        output = ""
        if "get" in command:
            if "namespace" in command:
                output = "namespace/consumer" if namespace_exists else ""
            else:
                index = command.index("get")
                key = (command[index + 1], command[index + 2])
                output = json.dumps(objects[key]) if key in objects else ""
        else:
            manifest = yaml.safe_load(kwargs["input"])
            writes.append(manifest)
            data = manifest.get("data") or {
                k: base64.b64encode(v.encode()).decode()
                for k, v in manifest.get("stringData", {}).items()
            }
            objects[(manifest["kind"], manifest["metadata"]["name"])] = {"data": data}
        return subprocess.CompletedProcess(
            command, 1 if failure else 0, output, "SENSITIVE echoed manifest"
        )

    monkeypatch.setattr("nebius_cxcli.soperator_runtime_objects.subprocess.run", run)
    runtime = RuntimeObjects(
        extra_env={"NEBIUS_CXCLI_TARGET_KUBE_CONTEXT": "bound-target"}, assert_authority=authority
    )
    return runtime, writes, commands


def test_existing_incomplete_secret_is_rejected_without_writes(monkeypatch):
    runtime, writes, _ = _runtime(
        monkeypatch, existing={("Secret", "backup"): {"data": {"one": ""}}}
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.complete("Secret", "consumer", "backup", ("one", "two"))
    assert writes == []


def test_runtime_writes_are_create_only_and_use_bound_context(monkeypatch):
    assertions = []
    runtime, writes, commands = _runtime(monkeypatch, authority=lambda: assertions.append(True))
    assert runtime.complete("Secret", "consumer", "directory", ("sssd.conf",)) is False
    runtime.create("Secret", "consumer", "directory", {"sssd.conf": "SENSITIVE"})
    assert len(assertions) == 1
    assert writes[0]["stringData"] == {"sssd.conf": "SENSITIVE"}
    assert all(command[:3] == ["kubectl", "--context", "bound-target"] for command in commands)
    assert all("apply" not in command for command in commands)


def test_lost_lease_prevents_write(monkeypatch):
    def lost():
        raise RuntimeError("lease lost")

    runtime, writes, _ = _runtime(monkeypatch, authority=lost)
    with pytest.raises(RuntimeError, match="lease lost"):
        runtime.create("Secret", "consumer", "directory", {"sssd.conf": "SENSITIVE"})
    assert writes == []


def test_lost_lease_after_namespace_creation_prevents_secret_write(monkeypatch):
    assertions = []

    def authority():
        assertions.append(True)
        if len(assertions) == 2:
            raise RuntimeError("lease lost")

    runtime, writes, _ = _runtime(monkeypatch, authority=authority, namespace_exists=False)
    with pytest.raises(RuntimeError, match="lease lost"):
        runtime.create("Secret", "consumer", "directory", {"sssd.conf": "SENSITIVE"})
    assert [obj["kind"] for obj in writes] == ["Namespace"]


def test_kubectl_error_never_echoes_manifest(monkeypatch):
    runtime, _, _ = _runtime(monkeypatch, failure=True)
    with pytest.raises(RuntimeError) as error:
        runtime.complete("Secret", "consumer", "directory", ("sssd.conf",))
    assert "SENSITIVE" not in str(error.value)


def test_context_is_never_inferred_from_current_kubeconfig():
    with pytest.raises(RuntimeError, match="explicitly bound"):
        RuntimeObjects(extra_env={}, assert_authority=lambda: None)


def _payload(ca=""):
    return {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "namespace": "flux-system",
                    "values": {
                        "sssd": {
                            "enabled": True,
                            "sssdConfSecretRefName": "directory",
                            "sssdLdapCAConfigMapRefName": ca,
                        },
                        "nodesets": [{"name": "cpu"}, {"name": "gpu"}],
                    },
                }
            ]
        }
    }


def test_sssd_runtime_reads_file_only_and_deduplicates_shared_objects(tmp_path, monkeypatch):
    _, writes, _ = _runtime(monkeypatch)
    config = tmp_path / "private.conf"
    config.write_text("[sssd]\nservices = nss, pam\n")
    payload = _payload()
    before = json.dumps(payload)
    ensure_soperator_sssd_runtime(
        payload,
        target_ref="cluster1",
        namespaces={"slurmCluster": "consumer", "nodesets": "consumer"},
        extra_env={
            "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT": "bound-target",
            SSSD_CONFIG_FILE_ENV + "_CLUSTER1": str(config),
        },
        assert_authority=lambda: None,
    )
    assert len(writes) == 1
    assert writes[0]["stringData"] == {"sssd.conf": config.read_text()}
    assert writes[0]["metadata"]["namespace"] == "consumer"
    assert json.dumps(payload) == before


def test_sssd_reuses_complete_objects_without_file_access(monkeypatch):
    _, writes, _ = _runtime(
        monkeypatch,
        existing={
            ("Secret", "directory"): {"data": {"sssd.conf": "eA=="}},
            ("ConfigMap", "ldap-ca"): {"data": {"ca.crt": "certificate"}},
        },
    )
    ensure_soperator_sssd_runtime(
        _payload("ldap-ca"),
        target_ref="cluster1",
        namespaces={"slurmCluster": "consumer", "nodesets": "consumer"},
        extra_env={"NEBIUS_CXCLI_TARGET_KUBE_CONTEXT": "bound-target"},
        assert_authority=lambda: None,
    )
    assert writes == []


def test_sssd_ca_failure_has_no_partial_secret_write_or_path_leak(tmp_path, monkeypatch):
    _, writes, _ = _runtime(monkeypatch)
    config = tmp_path / "private.conf"
    config.write_text("config")
    ca = tmp_path / "private-ca.pem"
    ca.write_text("SENSITIVE invalid PEM")
    with pytest.raises(RuntimeError) as error:
        ensure_soperator_sssd_runtime(
            _payload("ldap-ca"),
            target_ref="cluster1",
            namespaces={"slurmCluster": "consumer", "nodesets": "consumer"},
            extra_env={
                "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT": "bound-target",
                SSSD_CONFIG_FILE_ENV + "_CLUSTER1": str(config),
                SSSD_LDAP_CA_FILE_ENV + "_CLUSTER1": str(ca),
            },
            assert_authority=lambda: None,
        )
    assert writes == []
    assert "SENSITIVE" not in str(error.value) and str(tmp_path) not in str(error.value)


def test_sssd_target_never_falls_back_to_global_file(tmp_path, monkeypatch):
    _, writes, _ = _runtime(monkeypatch)
    with pytest.raises(RuntimeError, match="CONFIG_FILE_CLUSTER1"):
        ensure_soperator_sssd_runtime(
            _payload(),
            target_ref="cluster1",
            namespaces={"slurmCluster": "consumer", "nodesets": "consumer"},
            extra_env={
                "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT": "bound-target",
                SSSD_CONFIG_FILE_ENV: str(tmp_path / "ignored"),
            },
            assert_authority=lambda: None,
        )
    assert writes == []
