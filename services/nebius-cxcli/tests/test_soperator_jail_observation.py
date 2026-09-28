from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_jail_observation as observation
from nebius_cxcli import soperator_jail_protection as protection


def test_mandatory_mounts_do_not_probe_directories():
    values = {"jailPersistentMounts": [{"mountPath": "/home"}]}
    assert (
        observation.observe_protected_directories(
            SimpleNamespace(), values, kube_context="ctx", extra_env={}
        )
        == []
    )


@pytest.mark.parametrize("namespaces", [[], ["first", "second"]])
def test_directory_observation_requires_exact_namespace(namespaces):
    cli = SimpleNamespace(_soperator_upgrade_live_slurmcluster_namespaces=lambda **kw: namespaces)
    with pytest.raises(RuntimeError, match="one exact"):
        observation.observe_protected_directories(
            cli,
            {"jailPersistentMounts": [{"mountPath": "/opt/models"}]},
            kube_context="ctx",
            extra_env={},
        )


@pytest.mark.parametrize("output,returncode", [("31\n", 0), ("", 0), ("bad\n", 0), ("31\n", 1)])
def test_directory_probe_preserves_bindings_and_rejects_invalid_inodes(
    monkeypatch, output, returncode
):
    values = {"jailPersistentMounts": [{"mountPath": "/opt/models"}]}
    binding = {"mountPath": "/opt/models", "pvName": "owned-pv", "pvcName": "owned-pvc"}
    calls = []
    env = {"KUBECONFIG": "private-fixture"}

    def run(namespace, args, **kwargs):
        assert namespace == "soperator"
        assert kwargs["kube_context"] == "ctx" and kwargs["extra_env"] is env
        calls.append(args)
        if args[0] == "get":
            assert kwargs["check"] is True
            return SimpleNamespace(stdout='{"metadata":{"uid":"owned"}}')
        assert args[:6] == ["exec", "login", "-c", "slurm", "--", "sh"]
        assert kwargs["check"] is False
        return SimpleNamespace(stdout=output, returncode=returncode)

    def bindings(actual, *, pod, read_pvc, read_pv):
        assert actual is values
        assert pod["metadata"]["uid"] == "owned"
        assert read_pvc("owned-pvc")["metadata"]["uid"] == "owned"
        assert read_pv("owned-pv")["metadata"]["uid"] == "owned"
        return "slurm", [binding]

    monkeypatch.setattr(protection, "observed_directory_bindings", bindings)
    cli = SimpleNamespace(
        _SOPERATOR_UPGRADE_LOGIN_POD="login",
        _soperator_upgrade_live_slurmcluster_namespaces=lambda **kw: ["soperator"],
        _run_soperator_upgrade_kubectl=run,
    )
    if output == "31\n" and returncode == 0:
        assert observation.observe_protected_directories(
            cli, values, kube_context="ctx", extra_env=env
        ) == [{**binding, "inode": "31"}]
    else:
        with pytest.raises(ValueError, match="without symlink traversal"):
            observation.observe_protected_directories(
                cli, values, kube_context="ctx", extra_env=env
            )
    assert [args[1] for args in calls[:3]] == ["pod", "pvc", "pv"]
