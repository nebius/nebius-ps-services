from __future__ import annotations

import json
import os
from types import SimpleNamespace as NS

import pytest
import yaml

from nebius_cxcli import ssh_host_identity as identity


@pytest.fixture
def pair():
    class PrivatePair(dict):
        def __repr__(self):
            return "<disposable host identity>"

    return PrivatePair(identity.generate_host_identity())


def instance(pair=None):
    data = "#cloud-config\n"
    if pair is not None:
        data += "# " + identity.MARKER + "\n" + yaml.safe_dump({"ssh_keys": dict(pair)})
    return NS(metadata=NS(resource_version="v1"), spec=NS(cloud_init_user_data=data))


def test_fresh_identity_persists_before_provisioning_and_reuses(tmp_path):
    paths = NS(project_dir=tmp_path)
    first = identity.fresh_identity(
        paths, "project", "nat", namespace="nat-host-keys", preview=False
    )
    files = list((tmp_path / ".nebius-cxcli/nat-host-keys").iterdir())
    assert len(files) == 1 and files[0].suffix == ".key"
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert files[0].parent.stat().st_mode & 0o777 == 0o700
    assert files[0].read_text() == first["ed25519_private"]
    assert (
        identity.fresh_identity(paths, "project", "nat", namespace="nat-host-keys", preview=False)
        == first
    )
    other = identity.fresh_identity(
        paths, "other-project", "nat", namespace="nat-host-keys", preview=False
    )
    assert other["ed25519_public"] != first["ed25519_public"]


def test_preview_does_not_persist_new_identity(tmp_path):
    pair = identity.fresh_identity(
        NS(project_dir=tmp_path), "project", "nat", namespace="nat-host-keys", preview=True
    )
    assert pair["ed25519_public"].startswith("ssh-ed25519 ")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("problem", ["invalid", "mode", "symlink", "hardlink"])
def test_existing_private_identity_is_never_replaced(tmp_path, problem):
    paths = NS(project_dir=tmp_path)
    identity.fresh_identity(paths, "project", "nat", namespace="nat-host-keys", preview=False)
    key = next((tmp_path / ".nebius-cxcli/nat-host-keys").glob("*.key"))
    before = key.read_bytes()
    if problem == "invalid":
        key.write_text("invalid")
        before = b"invalid"
    elif problem == "mode":
        key.chmod(0o644)
    elif problem == "hardlink":
        os.link(key, tmp_path / "link")
    else:
        moved = tmp_path / "moved"
        key.rename(moved)
        key.symlink_to(moved)
    with pytest.raises(RuntimeError):
        identity.fresh_identity(paths, "project", "nat", namespace="nat-host-keys", preview=False)
    assert key.read_bytes() == before


def test_retained_metadata_recovers_exact_pair_without_rotation(pair):
    assert identity.recovered_identity(instance(pair)) == pair
    assert identity.recovered_identity(instance()) == {}


@pytest.mark.parametrize("problem", ["public", "private", "extra", "missing"])
def test_retained_conflicting_metadata_is_rejected(pair, problem):
    broken = dict(pair)
    if problem == "public":
        broken["ed25519_public"] = identity.generate_host_identity()["ed25519_public"]
    elif problem == "private":
        broken["ed25519_private"] = "invalid"
    elif problem == "extra":
        broken["rsa_private"] = "invalid"
    else:
        del broken["ed25519_private"]
    with pytest.raises(RuntimeError):
        identity.recovered_identity(instance(broken))


def test_runtime_input_recovers_current_remote_identity(tmp_path, monkeypatch, pair):
    (tmp_path / "variables.tf").write_text('variable "nat_ssh_host_keys" {}')
    observed = []
    monkeypatch.setattr(
        identity, "compute_instance", lambda *a: observed.append("read") or instance(pair)
    )
    monkeypatch.setattr(
        identity, "fresh_identity", lambda *a, **k: pytest.fail("rotated retained key")
    )
    env = identity.prepare_host_identity(
        NS(project_id="project"),
        instance_id="retained",
        paths=None,
        infra_dir=tmp_path,
        module_name="nat",
        namespace="nat-host-keys",
        preview=False,
    )
    assert json.loads(env["TF_VAR_nat_ssh_host_keys"]) == pair
    assert observed == ["read", "read"]


def test_retained_guest_identity_preserves_empty_input(tmp_path, monkeypatch):
    (tmp_path / "variables.tf").write_text('variable "nat_ssh_host_keys" {}')
    monkeypatch.setattr(identity, "compute_instance", lambda *a: instance())
    env = identity.prepare_host_identity(
        NS(project_id="project"),
        instance_id="retained",
        paths=None,
        infra_dir=tmp_path,
        module_name="nat",
        namespace="nat-host-keys",
        preview=False,
    )
    assert env == {"TF_VAR_nat_ssh_host_keys": "{}"}
    # An already-rendered snapshot without the new variable remains unchanged.
    (tmp_path / "variables.tf").write_text("")
    assert (
        identity.prepare_host_identity(
            NS(project_id="project"),
            instance_id="retained",
            paths=None,
            infra_dir=tmp_path,
            module_name="nat",
            namespace="nat-host-keys",
            preview=False,
        )
        == {}
    )


def test_new_vm_requires_rendered_runtime_input_before_key_generation(tmp_path, monkeypatch):
    (tmp_path / "variables.tf").write_text("")
    monkeypatch.setattr(
        identity, "fresh_identity", lambda *a, **k: pytest.fail("generated before render admission")
    )
    with pytest.raises(RuntimeError, match="render"):
        identity.prepare_host_identity(
            NS(project_id="project"),
            instance_id="",
            paths=None,
            infra_dir=tmp_path,
            module_name="nat",
            namespace="nat-host-keys",
            preview=False,
        )


def test_provider_drift_fails_before_returning_secret_input(tmp_path, monkeypatch, pair):
    (tmp_path / "variables.tf").write_text('variable "nat_ssh_host_keys" {}')
    second = instance(pair)
    second.metadata.resource_version = "v2"
    values = iter([instance(pair), second])
    monkeypatch.setattr(identity, "compute_instance", lambda *a: next(values))
    with pytest.raises(RuntimeError, match="changed"):
        identity.prepare_host_identity(
            NS(project_id="project"),
            instance_id="retained",
            paths=None,
            infra_dir=tmp_path,
            module_name="nat",
            namespace="nat-host-keys",
            preview=False,
        )


def test_rendered_host_keys_are_sensitive_and_never_config_or_tfvars():
    from nebius_cxcli.component_sources import SourceProfile
    from nebius_cxcli.infra_render import (
        _build_module_plans,
        _render_tfvars_json,
        _render_variables_tf,
    )
    from test_nat_gateway import config

    payload = config()
    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    nat = next(plan for plan in plans if plan.component_id == "nat-gateway")
    binding = next(value for value in nat.bindings if value.argument_name == "ssh_host_keys")
    assert binding.sensitive and not binding.include_in_tfvars and binding.default == {}
    assert binding.variable_name not in _render_tfvars_json(plans)
    block = (
        _render_variables_tf(plans).split(f'variable "{binding.variable_name}"')[1].split("\n}")[0]
    )
    assert "sensitive   = true" in block
    payload["infra"]["components"][0]["inputs"]["ssh_host_keys"] = {"bad": "value"}
    with pytest.raises(ValueError, match="runtime-only"):
        _build_module_plans(payload, source_profile=SourceProfile.LOCAL)


def test_deployment_prepares_identity_before_returning_to_plan(monkeypatch, tmp_path):
    from nebius_cxcli import nat_gateway_deploy as deployment
    from test_nat_gateway_alignment import prepared_manager

    manager, _sdk, _record, _target, _live, _effects = prepared_manager(monkeypatch)
    manager.executor.paths.infra_dir = tmp_path
    calls = []
    monkeypatch.setattr(
        deployment,
        "prepare_host_identity",
        lambda *a, **k: calls.append(k) or {"TF_VAR_nat_ssh_host_keys": "{}"},
    )
    manager.prepare()
    assert calls and calls[0]["module_name"] == "nat"
    assert manager.executor.runtime_env["TF_VAR_nat_ssh_host_keys"] == "{}"
    manager.executor.runtime_env = {}
    manager.prepare()
    assert manager.executor.runtime_env["TF_VAR_nat_ssh_host_keys"] == "{}"
    assert len(calls) == 1


@pytest.mark.parametrize("planner", ["source", "stage"])
def test_admission_planners_receive_only_bound_nat_runtime_inputs(monkeypatch, tmp_path, planner):
    import copy

    from nebius_cxcli import deployment_cli
    from nebius_cxcli import nat_gateway_deploy as deployment
    from test_nat_gateway_alignment import prepared_manager

    manager, *_ = prepared_manager(monkeypatch)
    manager.executor.paths.infra_dir = tmp_path
    monkeypatch.setattr(
        deployment,
        "prepare_host_identity",
        lambda *a, **k: {"TF_VAR_nat_ssh_host_keys": "protected-runtime-fixture"},
    )
    manager.prepare()
    manager.executor.runtime_env["UNRELATED_PARENT_CREDENTIAL"] = "must-not-copy"
    child = object.__new__(deployment_cli._CliDeploymentExecutor)
    child.config = copy.deepcopy(manager.executor.config)
    child.manifest = copy.deepcopy(manager.executor.manifest)
    child.paths = NS(infra_dir=tmp_path / planner)
    child._runtime_payload_env = {"CHILD_RUNTIME": "own-value"}
    child.cli = NS(_terraform_runtime_env=lambda config: {})
    token = deployment.CURRENT_NAT.set(manager)
    try:
        for _ in range(2):
            child._prepare_runtime_inputs()
            assert child.runtime_env["TF_VAR_nat_ssh_host_keys"] == "protected-runtime-fixture"
            assert child.runtime_env["CHILD_RUNTIME"] == "own-value"
            assert "UNRELATED_PARENT_CREDENTIAL" not in child.runtime_env
            assert (
                json.loads(child.runtime_env["TF_VAR_" + deployment.ROUTE_OVERRIDES])
                == manager.overrides
            )
    finally:
        deployment.CURRENT_NAT.reset(token)


@pytest.mark.parametrize("scope", ["root", "child"])
@pytest.mark.parametrize("drift", ["configuration", "module", "duplicate"])
def test_runtime_identity_rebinding_rejects_changed_intent(monkeypatch, scope, drift):
    import copy

    from test_nat_gateway_alignment import prepared_manager

    manager, *_ = prepared_manager(monkeypatch)
    manager.prepare()
    executor = manager.executor
    if scope == "child":
        executor = NS(
            config=copy.deepcopy(executor.config), manifest=copy.deepcopy(executor.manifest)
        )
    executor.runtime_env = {}
    if drift == "configuration":
        executor.config["infra"]["components"][0]["inputs"]["ssh_user_name"] = "changed"
    elif drift == "module":
        executor.manifest["render"]["module_sources"][0]["module_name"] = "different_module"
    else:
        modules = executor.manifest["render"]["module_sources"]
        modules.append(copy.deepcopy(modules[0]))
    with pytest.raises(RuntimeError, match="NAT runtime inputs differ"):
        if scope == "root":
            manager.prepare()
        else:
            manager.apply_runtime_inputs(executor)
    assert executor.runtime_env == {}


def test_runtime_identity_requires_prepared_owner(monkeypatch):
    from test_nat_gateway_alignment import prepared_manager

    manager, *_ = prepared_manager(monkeypatch)
    with pytest.raises(RuntimeError, match="prepared deployment owner"):
        manager.apply_runtime_inputs(manager.executor)


def test_interrupted_terraform_restoration_recovers_host_identity_first(monkeypatch, tmp_path):
    from nebius_cxcli import nat_gateway_deploy as deployment
    from test_nat_gateway_alignment import prepared_manager

    manager, _sdk, record, target, live, _effects = prepared_manager(
        monkeypatch, owner="terraform", current="table-nat"
    )
    key = deployment.target_key(target)
    record["targets"] = {key: target}
    record["activation"] = {
        "phase": "restoration_pending",
        "previous_phase": "prepared",
        "attachments": {
            key: {
                "before": "table-original",
                "effective_before": "table-original",
                "desired": "table-nat",
            }
        },
        "probes": {},
    }
    events = []
    monkeypatch.setattr(
        deployment,
        "prepare_host_identity",
        lambda *a, **k: events.append("identity") or {"TF_VAR_nat_ssh_host_keys": "retained"},
    )

    def plan(**kwargs):
        events.append("plan")
        protected = manager.executor.runtime_env.get("TF_VAR_nat_ssh_host_keys") == "retained"
        manager.executor.observation_plan = {
            "resource_changes": [
                {
                    "type": "nebius_vpc_v1_subnet" if protected else "nebius_compute_v1_instance",
                    "change": {
                        "actions": ["update"] if protected else ["delete", "create"],
                        "before": {"id": "subnet-work", "route_table_id": "table-nat"},
                        "after": {"id": "subnet-work", "route_table_id": "table-original"},
                    },
                }
            ]
        }
        return tmp_path / "recovery.plan", {}

    manager.executor._terraform_plan = plan
    manager.executor.cli._sha256_file = lambda path: "digest"
    manager.executor.cli._run_terraform_apply_with_status = lambda *a, **k: live.update(
        route="table-original"
    )
    with pytest.raises(RuntimeError, match="Interrupted NAT activation restored"):
        manager.prepare()
    assert events == ["identity", "plan"]
    assert live["route"] == "table-original"
    assert target["pending"] is None
    assert record["activation"]["phase"] == "restored"


@pytest.mark.parametrize("removed", [False, True])
def test_recovery_never_generates_identity_for_missing_or_removed_vm(monkeypatch, removed):
    from nebius_cxcli import nat_gateway_deploy as deployment
    from test_nat_gateway_alignment import prepared_manager

    manager, _sdk, record, *_ = prepared_manager(monkeypatch)
    record["activation"] = {"phase": "restoration_pending"}
    manager.outputs = lambda: {}
    if removed:
        manager.executor.config["infra"]["components"] = []
        manager.rows = []
    monkeypatch.setattr(
        deployment,
        "prepare_host_identity",
        lambda *a, **k: pytest.fail("generated recovery key"),
    )
    manager._apply_routes({}, "Restore failed NAT activation")
    assert not any(key.endswith("_ssh_host_keys") for key in manager.executor.runtime_env)


def test_recovery_metadata_failure_still_attempts_external_restore_and_cleanup(monkeypatch):
    import copy

    from nebius_cxcli import nat_gateway_deploy as deployment
    from nebius_cxcli import nat_gateway_probes as probes
    from test_nat_gateway_alignment import prepared_manager

    manager, _sdk, record, target, *_ = prepared_manager(
        monkeypatch, owner="terraform", current="table-nat"
    )
    key = deployment.target_key(target)
    external = {**copy.deepcopy(target), "owner": "external", "subnet_id": "subnet-external"}
    record["targets"] = {key: target, "external": external}
    record["activation"] = {
        "phase": "restoration_pending",
        "previous_phase": "prepared",
        "attachments": {
            name: {
                "before": "table-original",
                "effective_before": "table-original",
                "desired": "table-nat",
            }
            for name in record["targets"]
        },
        "probes": {},
    }
    attempts = []

    def unavailable(*a, **k):
        raise RuntimeError("Compute host identity unavailable")

    monkeypatch.setattr(deployment, "prepare_host_identity", unavailable)
    monkeypatch.setattr(manager, "_attach", lambda *a: attempts.append("external"))
    monkeypatch.setattr(probes, "cleanup", lambda *a, **k: attempts.append("cleanup"))
    with pytest.raises(RuntimeError, match="NAT recovery incomplete"):
        manager.prepare()
    assert attempts == ["external", "cleanup"]
    assert record["activation"]["phase"] == "restoration_pending"


@pytest.mark.integration
def test_real_terraform_template_preserves_retained_bytes_and_installs_fresh_pair(tmp_path, pair):
    import re
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("terraform"):
        pytest.skip("Terraform is required for actual cloud-init rendering")
    module = Path(__file__).resolve().parents[3] / "platform-infra/modules/nat-gateway"
    template = module / "cloud-init.tftpl"
    previous = tmp_path / "previous.tftpl"
    previous.write_text(
        re.sub(
            r"%\{ if length\(ssh_host_keys\) > 0 ~\}.*?%\{ endif ~\}\n",
            "",
            template.read_text(),
            flags=re.S,
        )
    )
    params = {
        "ssh_user_name": "test",
        "ssh_public_key": "fixture",
        "ssh_host_keys": {},
        "guest_config": "{}",
        "guest_program": "fixture",
        "security_program": "fixture",
    }

    def render(source, values):
        expression = f"jsonencode(templatefile({json.dumps(str(source))}, {json.dumps(values)}))\n"
        result = subprocess.run(
            ["terraform", "console"],
            input=expression,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=30,
        )
        assert result.returncode == 0, "Terraform template evaluation failed"
        return json.loads(json.loads(result.stdout))

    assert render(template, params) == render(previous, params)
    fresh = yaml.safe_load(render(template, {**params, "ssh_host_keys": pair}))
    assert fresh["ssh_keys"]["ed25519_public"] == pair["ed25519_public"]
    assert (
        identity._pair(fresh["ssh_keys"]["ed25519_private"].encode())["ed25519_public"]
        == pair["ed25519_public"]
    )


@pytest.mark.integration
def test_retained_identity_does_not_trigger_terraform_replacement(tmp_path, pair):
    import re
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("terraform"):
        pytest.skip("Terraform is required for the actual state transition")
    module = Path(__file__).resolve().parents[3] / "platform-infra/modules/nat-gateway"
    expression = re.findall(r"^\s+ssh_host_keys\s+= (.+)$", (module / "main.tf").read_text(), re.M)
    assert len(expression) == 1
    parameters = {
        "ssh_user_name": "test",
        "ssh_public_key": "fixture",
        "guest_config": "{}",
        "guest_program": "fixture",
        "security_program": "fixture",
    }
    arguments = ",\n".join(f"{key} = {json.dumps(value)}" for key, value in parameters.items())

    def root(keys):
        return f"""
variable "ssh_host_keys" {{
  type = map(string)
  sensitive = true
  default = {{}}
}}
locals {{
  cloud_init = templatefile({json.dumps(str(module / "cloud-init.tftpl"))}, {{
    {arguments}
    ssh_host_keys = {keys}
  }})
}}
resource "terraform_data" "revision" {{ input = sha256(local.cloud_init) }}
resource "terraform_data" "boot" {{
  lifecycle {{ replace_triggered_by = [terraform_data.revision] }}
}}
resource "terraform_data" "instance" {{
  lifecycle {{ replace_triggered_by = [terraform_data.revision] }}
}}
"""

    def run(*args, **kwargs):
        result = subprocess.run(
            ["terraform", *args], cwd=tmp_path, capture_output=True, text=True, timeout=30, **kwargs
        )
        assert result.returncode == 0, "Native Terraform fixture failed"
        return result.stdout

    (tmp_path / "main.tf").write_text(root("{}"))
    run("init", "-backend=false", "-input=false")
    run("apply", "-auto-approve", "-input=false")
    (tmp_path / "main.tf").write_text(root(expression[0]))
    run("plan", "-input=false", "-out=retained.plan")
    plan = json.loads(run("show", "-json", "retained.plan"))
    assert all(row["change"]["actions"] == ["no-op"] for row in plan["resource_changes"])
    # Only public field names are declassified; actual key values stay sensitive.
    assert (
        run(
            "console",
            input="issensitive(local.cloud_init)\n",
            env={**os.environ, "TF_VAR_ssh_host_keys": json.dumps(pair)},
        ).strip()
        == "true"
    )


def test_directory_swap_cannot_redirect_private_key_publication(tmp_path, monkeypatch, pair):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    folder = tmp_path / ".nebius-cxcli/nat-host-keys"

    def swap():
        folder.rename(folder.with_name("original"))
        folder.symlink_to(outside, target_is_directory=True)
        return pair

    monkeypatch.setattr(identity, "generate_host_identity", swap)
    with pytest.raises(RuntimeError, match="directory changed"):
        identity.fresh_identity(
            NS(project_dir=tmp_path), "project", "nat", namespace="nat-host-keys", preview=False
        )
    assert list(outside.iterdir()) == []
    assert list(folder.with_name("original").iterdir()) == []


def test_generated_identity_recovers_into_strict_trust_without_logs(tmp_path, monkeypatch, pair):
    from datetime import UTC, datetime

    from nebius.api.nebius.compute import v1

    from nebius_cxcli.ssh_host_enrollment import prepare_ssh_host_trust
    from nebius_cxcli.ssh_trust import ssh_trust_options

    obj = instance(pair)
    obj.metadata = NS(
        id="computeinstance-test",
        parent_id="project-test",
        resource_version="v1",
        created_at=datetime.fromtimestamp(100, UTC),
    )
    obj.spec.boot_disk = NS(existing_disk=NS(id="disk"))
    obj.status = NS(
        state=NS(name="RUNNING"),
        network_interfaces=[
            NS(public_ip_address=NS(allocation_id="allocation", address="192.0.2.1/32"))
        ],
    )
    monkeypatch.setattr(v1, "InstanceServiceClient", lambda sdk: NS(get=None))
    sdk = NS(sdk=None, project_id="project-test", call=lambda *args: obj)
    outputs = dict(
        instance_id="computeinstance-test",
        boot_disk_id="disk",
        public_allocation_id="allocation",
        public_ip="192.0.2.1/32",
    )
    pin = prepare_ssh_host_trust(sdk, outputs, NS(generated_dir=tmp_path), None)
    assert pin.read_text() == "192.0.2.1 " + pair["ed25519_public"] + "\n"
    assert "StrictHostKeyChecking=yes" in ssh_trust_options(pin)
