import copy

import pytest
import yaml

from nebius_cxcli.soperator_checks_login import bind_checks_login, native_login_commands
from nebius_cxcli.soperator_install_checks_repair import login_repair_candidate
from nebius_cxcli.soperator_install_render_repair import OUTER_FILE, VALUES_FILE


@pytest.fixture
def native_source(tmp_path):
    chart = tmp_path / "helm/soperator-activechecks"
    (chart / "scripts").mkdir(parents=True)
    checks = {}
    for name, filename in [("create-user-nebius", "create-user.sh"), ("ssh-check", "ssh-check.sh")]:
        checks[name] = {"k8sJobSpec": {"scriptFile": "scripts/" + filename}}
        (chart / "scripts" / filename).write_text(
            'set -e\nssh user@login-0.soperator-login-headless-svc.soperator.svc.cluster.local "$ARG"\n'
        )
    (chart / "values.yaml").write_text(yaml.safe_dump({"checks": checks}))
    return tmp_path


@pytest.fixture
def values():
    return {
        "soperatorActiveChecks": {
            "enabled": True,
            "overrideValues": {"slurmClusterRefName": "lab"},
        },
        "slurmCluster": {"overrideValues": {"clusterName": "lab"}},
    }


def test_binding_preserves_native_bytes_and_is_idempotent(native_source, values):
    before = {p: p.read_bytes() for p in native_source.rglob("*") if p.is_file()}
    original = copy.deepcopy(values)
    bound = bind_checks_login(values, native_source)
    assert bind_checks_login(bound, native_source) == bound
    assert values == original
    assert before == {p: p.read_bytes() for p in before}
    for row in bound["soperatorActiveChecks"]["overrideValues"]["checks"].values():
        command = row["k8sJobSpec"]["jobContainer"]["command"]
        assert command == [
            "bash",
            "-c",
            'set -e\nssh user@login-0.lab-login-headless-svc.soperator.svc.cluster.local "$ARG"\n',
        ]


@pytest.mark.parametrize(
    "spec",
    [
        {"jobContainer": {"command": ["bash", "-c", "other"]}},
        {"jobContainer": {"args": ["other"]}},
        {"scriptFile": "other.sh"},
        {"script": "other"},
    ],
)
def test_binding_rejects_custom_execution(native_source, values, spec):
    values["soperatorActiveChecks"]["overrideValues"]["checks"] = {
        "ssh-check": {"k8sJobSpec": spec}
    }
    with pytest.raises(ValueError, match="custom"):
        bind_checks_login(values, native_source)


def test_binding_rejects_unknown_source_and_invalid_identity(native_source):
    with pytest.raises(ValueError, match="valid cluster"):
        native_login_commands(native_source, "name;command")
    (native_source / "helm/soperator-activechecks/scripts/ssh-check.sh").write_text("changed")
    with pytest.raises(ValueError, match="hostname contract"):
        native_login_commands(native_source, "lab")


def test_absent_overrides_bind_the_same_cluster_reference(native_source, values):
    values["soperatorActiveChecks"]["overrideValues"] = None
    bound = bind_checks_login(values, native_source)
    assert bound["soperatorActiveChecks"]["overrideValues"]["slurmClusterRefName"] == "lab"


def test_interrupted_login_repair_changes_only_matching_values(native_source, values):
    before = {
        VALUES_FILE: yaml.safe_dump({"data": {"values.yaml": yaml.safe_dump(values)}}).encode(),
        OUTER_FILE: yaml.safe_dump(
            {"spec": {"values": values, "postRenderers": [{"keep": True}]}}
        ).encode(),
        "storage": b"unchanged",
    }
    original = copy.deepcopy(before)
    after = login_repair_candidate(before, native_source)
    assert before == original
    assert {key for key in after if after[key] != before[key]} == {VALUES_FILE, OUTER_FILE}
    bound = bind_checks_login(values, native_source)
    assert yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"]) == bound
    assert yaml.safe_load(after[OUTER_FILE])["spec"] == {
        "values": bound,
        "postRenderers": [{"keep": True}],
    }
    with pytest.raises(RuntimeError, match="absent native"):
        login_repair_candidate(after, native_source)
