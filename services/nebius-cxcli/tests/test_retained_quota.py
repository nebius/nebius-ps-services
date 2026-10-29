from __future__ import annotations

import shutil
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from nebius_cxcli.retained_quota import planned_retained_quota_credit, retained_allocation_quota


def allocation_fixture(component="wireguard-gw"):
    row = {
        "id": component,
        "instance_id": "gateway",
        "enabled": True,
        "inputs": {"parent_id": "project", "create_public_ip_allocation": True},
    }
    config = {
        "client_info": {"nebius": {"project_id": "project", "region_id": "region"}},
        "infra": {"components": [row]},
    }
    manifest = {
        "runtime_config": deepcopy(config),
        "render": {
            "module_sources": [
                {"component_id": component, "instance_id": "gateway", "module_name": "gateway"}
            ]
        },
    }
    before = {
        "id": "allocation-id",
        "parent_id": "project",
        "ipv4_public": {"subnet_id": "subnet"},
        "ipv4_private": None,
    }
    plan = {
        "resource_changes": [
            {
                "address": "module.gateway.nebius_vpc_v1_allocation.public",
                "mode": "managed",
                "type": "nebius_vpc_v1_allocation",
                "change": {
                    "actions": ["no-op"],
                    "before": before,
                    "after": deepcopy(before),
                    "after_unknown": {},
                },
            }
        ]
    }
    return config, manifest, plan


def credits(tmp_path, config, manifest, plan):
    from unittest.mock import patch

    @contextmanager
    def prepared(_infra, _manifest, env):
        yield dict(env or {})

    with patch("nebius_cxcli.deployment_cli.quota_plan_runtime_env", prepared):
        return _credits(tmp_path, config, manifest, plan)


def _credits(tmp_path, config, manifest, plan):
    return planned_retained_quota_credit(
        config,
        tmp_path,
        manifest,
        runtime_env={},
        plan_runner=lambda *a, **kw: kw["plan_file"].write_bytes(b"protected plan"),
        plan_reader=lambda *a, **kw: plan,
        warn=lambda message: None,
    )


def test_retained_wireguard_address_does_not_require_new_quota(tmp_path):
    config, manifest, plan = allocation_fixture()
    retained = credits(tmp_path, config, manifest, plan)
    assert {item.quota_name: item.required for item in retained} == {
        "vpc.allocation.count": 1,
        "vpc.ipv4-address.public.count": 1,
    }
    assert all(item.instance_id == "gateway" for item in retained)


def test_unresolved_gateway_does_not_discard_independent_filesystem_credit(tmp_path, monkeypatch):
    from nebius_cxcli import deployment_cli
    from test_sfs_quota import inputs, manifest, plan

    config = inputs()
    gateway, gateway_manifest, _ = allocation_fixture()
    config["infra"]["components"] += gateway["infra"]["components"]
    generated = manifest(config)
    generated["render"]["module_sources"] += gateway_manifest["render"]["module_sources"]
    calls = []

    @contextmanager
    def unresolved(*args):
        raise RuntimeError("WireGuard bootstrap unresolved")
        yield  # pragma: no cover

    def make_plan(*args, **kwargs):
        assert kwargs["targets"] == ("module.storage",)
        calls.append(kwargs["plan_file"])
        kwargs["plan_file"].write_bytes(b"protected plan")

    monkeypatch.setattr(deployment_cli, "quota_plan_runtime_env", unresolved)
    retained = planned_retained_quota_credit(
        config,
        tmp_path,
        generated,
        runtime_env={},
        plan_runner=make_plan,
        plan_reader=lambda *args, **kwargs: plan(),
        warn=lambda message: None,
    )
    assert {item.component_id for item in retained} == {"sfs"}
    assert sum(item.required for item in retained if ".size." in item.quota_name) == 2048 * 1024**3
    assert len(calls) == 1 and not calls[0].exists()


def test_retained_nat_public_and_private_allocations_credit_separate_quotas():
    from collections import Counter

    config, manifest, plan = allocation_fixture("nat-gateway")
    private = deepcopy(plan["resource_changes"][0])
    private["address"] = "module.gateway.nebius_vpc_v1_allocation.private"
    for key in ("before", "after"):
        private["change"][key].update(
            id="private-allocation", ipv4_public=None, ipv4_private={"subnet_id": "subnet"}
        )
    plan["resource_changes"].append(private)
    result = retained_allocation_quota(config, manifest["render"]["module_sources"], plan)
    counts = Counter()
    for item in result:
        counts[item.quota_name] += item.required
    assert counts == {"vpc.allocation.count": 2, "vpc.ipv4-address.public.count": 1}


@pytest.mark.parametrize("failure", [None, "prepare", "plan"])
def test_native_quota_preview_owns_frozen_inputs_and_unwinds(tmp_path, monkeypatch, failure):
    from nebius_cxcli import cli, deployment_cli, nat_gateway_deploy
    from nebius_cxcli.deployment_recovery import is_deployment_preview
    from nebius_cxcli.runtime_config import to_plain_data
    from test_nat_gateway import config
    from test_sfs_quota import manifest as generated_manifest

    frozen = config()
    manifest = generated_manifest(frozen)
    paths = cli.resolve_project_paths(tmp_path / "config.yaml")
    original = {"EXISTING": "preserved"}
    observed = []

    def runtime_env(candidate):
        assert to_plain_data(candidate) == frozen
        assert nat_gateway_deploy.CURRENT_NAT.get() is not None
        assert is_deployment_preview()
        return {"TF_VAR_cxcli_nat_execution_authorized": "true"}

    def prepare(owner):
        assert owner.executor.options.dry_run and owner.executor.lease is None
        assert owner.executor.manifest is manifest
        observed.append("prepare")
        if failure == "prepare":
            raise RuntimeError("preparation failed")

    monkeypatch.setattr(cli, "_terraform_runtime_env", runtime_env)
    monkeypatch.setattr(nat_gateway_deploy.NatDeployment, "prepare", prepare)
    assert nat_gateway_deploy.CURRENT_NAT.get() is None
    assert not is_deployment_preview()

    def run():
        with deployment_cli.quota_plan_runtime_env(paths.infra_dir, manifest, original) as env:
            assert env == {**original, "TF_VAR_cxcli_nat_execution_authorized": "true"}
            assert is_deployment_preview()
            if failure == "plan":
                raise RuntimeError("planner failed")

    if failure:
        with pytest.raises(RuntimeError):
            run()
    else:
        run()
    assert observed == ["prepare"]
    assert original == {"EXISTING": "preserved"}
    assert nat_gateway_deploy.CURRENT_NAT.get() is None
    assert not is_deployment_preview()


def test_quota_plan_reuses_existing_nat_owner_without_preparing_again(monkeypatch, tmp_path):
    from nebius_cxcli import deployment_cli, nat_gateway_deploy
    from test_nat_gateway import config

    owner = object()
    token = nat_gateway_deploy.CURRENT_NAT.set(owner)

    def unexpected(*args, **kwargs):
        pytest.fail("Existing NAT owner must not be nested or prepared again")

    monkeypatch.setattr(nat_gateway_deploy, "NatDeployment", unexpected)
    try:
        original = {"TF_VAR_cxcli_nat_execution_authorized": "true"}
        with deployment_cli.quota_plan_runtime_env(
            tmp_path, {"runtime_config": config()}, original
        ) as env:
            assert env == original and env is not original
            assert nat_gateway_deploy.CURRENT_NAT.get() is owner
    finally:
        nat_gateway_deploy.CURRENT_NAT.reset(token)


def test_quota_preview_rejects_pending_activation_before_recovery(monkeypatch):
    from types import SimpleNamespace

    from nebius_cxcli.nat_gateway_deploy import NatDeployment
    from test_nat_gateway import config

    owner = NatDeployment(SimpleNamespace(config=config(), options=SimpleNamespace(dry_run=True)))
    state = SimpleNamespace(value={"gateways": {"nat": {"activation": {"phase": "probe"}}}})
    monkeypatch.setattr(owner, "_open", lambda: state)
    monkeypatch.setattr(
        owner, "restore_activations", lambda: pytest.fail("Preview cannot restore live routes")
    )
    with pytest.raises(RuntimeError, match="requires deploy recovery before preview"):
        owner.prepare()


@pytest.mark.parametrize("actions", [["create"], ["delete", "create"], ["create", "delete"]])
def test_new_or_replaced_allocation_receives_no_retained_credit(tmp_path, actions):
    config, manifest, plan = allocation_fixture()
    plan["resource_changes"][0]["change"]["actions"] = actions
    assert not credits(tmp_path, config, manifest, plan)


@pytest.mark.parametrize(
    "mutation", ["id", "project", "unknown", "kind", "source", "module", "duplicate"]
)
def test_unproven_allocation_receives_no_retained_credit(tmp_path, mutation):
    config, manifest, plan = allocation_fixture()
    change = plan["resource_changes"][0]["change"]
    if mutation == "id":
        change["after"]["id"] = "different"
    elif mutation == "project":
        change["after"]["parent_id"] = "other"
    elif mutation == "unknown":
        change["after_unknown"]["ipv4_public"] = {"subnet_id": True}
    elif mutation == "kind":
        change["after"]["ipv4_public"] = None
        change["after"]["ipv4_private"] = {"subnet_id": "subnet"}
    elif mutation == "source":
        config["infra"]["components"][0]["inputs"]["name"] = "changed"
    elif mutation == "module":
        manifest["render"]["module_sources"].append(
            deepcopy(manifest["render"]["module_sources"][0])
        )
    else:
        plan["resource_changes"].append(deepcopy(plan["resource_changes"][0]))
    assert not credits(tmp_path, config, manifest, plan)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("terraform") is None, reason="Terraform executable unavailable")
def test_quota_planner_uses_runtime_owner_for_native_terraform_guard(tmp_path, monkeypatch):
    from nebius_cxcli import deployment_cli, terraform_ops

    config, manifest, plan = allocation_fixture()
    storage = {
        "id": "sfs",
        "instance_id": "storage",
        "enabled": True,
        "inputs": {"parent_id": "project", "size_gib": 2048},
    }
    config["infra"]["components"].append(storage)
    manifest["runtime_config"] = deepcopy(config)
    (tmp_path / "main.tf").write_text("""
variable "cxcli_nat_execution_authorized" {
  type = bool
  default = false
}
resource "terraform_data" "guard" {
  lifecycle {
    precondition {
      condition = var.cxcli_nat_execution_authorized
      error_message = "NAT lifecycle authorization required"
    }
  }
}
""")
    terraform_ops.terraform_init(tmp_path, quiet=True)
    observed = []

    @contextmanager
    def owner(*args):
        observed.append("owner")
        yield {"TF_VAR_cxcli_nat_execution_authorized": "true"}

    monkeypatch.setattr(deployment_cli, "quota_plan_runtime_env", owner, raising=False)

    def read_plan(infra_dir, **kwargs):
        actual = terraform_ops.terraform_show_json(infra_dir, **kwargs)
        assert actual["resource_changes"][0]["address"] == "terraform_data.guard"
        observed.append(kwargs["plan_file"])
        return plan

    planned_retained_quota_credit(
        config,
        tmp_path,
        manifest,
        runtime_env={},
        plan_runner=terraform_ops.terraform_plan,
        plan_reader=read_plan,
        warn=lambda message: None,
    )
    assert observed[0] == "owner"
    assert isinstance(observed[1], Path) and not observed[1].exists()
