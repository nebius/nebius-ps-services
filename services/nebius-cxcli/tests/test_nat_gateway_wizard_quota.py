from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_wizard as wizard
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.nat_gateway_quota import plan_requirements
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
from nebius_cxcli.vpc_identity import VpcIdentitySnapshot, VpcResourceIdentity
from test_nat_gateway import config


def test_cpu_filter_uses_resources_not_platform_names(monkeypatch):
    lookup = ProviderOptionLookup()
    lookup._compute_platform_preset_cache = {
        ("project-test", "future-platform"): (NS(gpu_count=0, vcpu_count=4),),
        ("project-test", "cpu-misleading"): (NS(gpu_count=1, vcpu_count=8),),
    }
    inventory = (OptionChoice("future-platform", "CPU"), OptionChoice("cpu-misleading", "GPU"))
    monkeypatch.setattr(lookup, "_resolve_project_compute_platform_inventory", lambda _: inventory)
    choices = lookup.resolve(
        provider="compute_platforms",
        args={"project_id": "project-test", "cpu_only": True},
        payload={},
        field_path="inputs.platform",
    )
    assert [choice.value for choice in choices] == ["future-platform"]
    unfiltered = lookup.resolve(
        provider="compute_platforms",
        args={"project_id": "project-test"},
        payload={},
        field_path="inputs.platform",
    )
    assert len(unfiltered) == 2  # CPU-only cache cannot poison another component.


def test_cpu_only_choices_cannot_fall_back_to_unverified_plugins(monkeypatch):
    from nebius_cxcli import provider_options

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_resolve_compute_platforms", lambda **kw: ())
    monkeypatch.setattr(
        provider_options,
        "_load_option_plugins",
        lambda *a: pytest.fail("Unverified plugin fallback"),
    )
    assert (
        lookup.resolve(
            provider="compute_platforms",
            args={"cpu_only": True},
            payload={},
            field_path="inputs.platform",
        )
        == []
    )


def test_plan_includes_shared_mk8s_worker_growth():
    template = {
        "resources": {"platform": "future-cpu", "preset": "4"},
        "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": 20},
        "network_interfaces": [{"public_ip_address": {}}],
    }
    plan = {
        "resource_changes": [
            {
                "type": "nebius_mk8s_v1_node_group",
                "address": "module.k8s.workers",
                "change": {
                    "actions": ["update"],
                    "before": {"fixed_node_count": 2, "template": template},
                    "after": {"fixed_node_count": 3, "template": template},
                },
            }
        ]
    }
    requirements = plan_requirements(config(), plan, sdk_factory=QuotaSDK)
    assert {r.quota_name: r.required for r in requirements} == {
        "compute.instance.count": 1,
        "compute.instance.non-gpu.vcpu": 4,
        "compute.disk.count": 1,
        "compute.disk.size.network-ssd": 20 * 1024**3,
        "vpc.ipv4-address.public.count": 1,
    }


class WizardSDK:
    def __init__(self, project):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cpu_preset(self, *args):
        return {"vcpu_count": 4, "gpu_count": 0}

    def image_choices(self, *args):
        return [{"id": "image-test", "name": "Ubuntu", "minimum_gib": 25}]

    def image(self, *args, **kwargs):
        return NS()

    def network_cidrs(self, *args):
        return ["10.0.0.0/8"]

    def address_pool(self, identity, **kwargs):
        from nebius_cxcli.nat_gateway_address_space import AddressBlock, AddressPool, IPv4

        return AddressPool(identity, (AddressBlock(IPv4("10.0.0.0/8")),))

    def route_policy(self, *args):
        return config()["infra"]["components"][0]["routing"]["policies"]["default"]

    def subnet_info(self, *args):
        return {
            "policy": self.route_policy(),
            "public_ip_capable": True,
            "direct_egress": True,
            "reason": "",
        }

    def subnets(self, *args):
        return [
            NS(
                metadata=NS(id="subnet-work", labels={}),
                status=NS(route_table=NS(id="table-original")),
            )
        ]

    def network(self, *args):
        return NS(
            status=NS(default_route_table_id="table-original"),
            spec=NS(ipv4_private_pools=NS(pools=[NS(id="pool")])),
        )


class Lookup:
    def vpc_identity(self, payload):
        if len(payload["infra"]["components"]) == 1:
            return VpcIdentitySnapshot()
        return VpcIdentitySnapshot(
            "available",
            {"network": VpcResourceIdentity("network-test", "project-test", "network-test")},
            {
                ("network", "work"): VpcResourceIdentity(
                    "subnet-work", "project-test", "network-test"
                )
            },
        )

    def vpc_discovery_complete(self):
        return True

    def record_vpc_diagnostics(self, *args):
        pass

    def vpc_diagnostics(self, *args):
        return ()

    def refresh(self):
        pass

    def resolve(self, *, provider, **kwargs):
        value = {
            "compute_platforms": "future-platform",
            "compute_platform_presets": "6vcpu-24gb",
            "compute_boot_disk_types": "NETWORK_SSD",
            "project_networks": "network-test",
            "project_subnets": "subnet-work",
        }[provider]
        return [
            OptionChoice(
                value,
                value,
                metadata={
                    "name": "network",
                    "network_id": "network-test",
                    "parent_id": "project-test",
                },
            )
        ]

    def last_error(self):
        return None


def prompt(path, default, *, choices, **kwargs):
    if path.endswith("routing.targets"):
        return [choices[0].value], False
    if choices:
        return next((c.value for c in choices if c.value == default), choices[0].value), False
    return default, False


def test_wizard_live_defaults_image_minimum_and_quota_draft(monkeypatch):
    payload = config()
    original = copy.deepcopy(payload)
    emitted = []
    assessed = []
    monkeypatch.setattr(
        wizard, "assess_live_quotas", lambda candidate, **kw: assessed.append(candidate)
    )
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: ["Quota unresolved"])
    result = wizard.configure_nat(
        payload,
        payload["infra"]["components"][0],
        prefix="nat",
        entry=ComponentEntry("nat-gateway", "infra", "infra.components", "NAT"),
        lookup=Lookup(),
        prompt=prompt,
        is_back=lambda _: False,
        emit=emitted.append,
        sdk_factory=WizardSDK,
    )
    assert result["inputs"]["platform"] == "future-platform"
    assert result["inputs"]["preset"] == "6vcpu-24gb"
    assert result["inputs"]["boot_disk_size_gib"] == 25
    assert result["routing"]["targets"][0]["delegated"] is True
    assert assessed and any("unavailable" in line for line in emitted)
    assert payload == original


def test_wizard_navigation_does_not_partially_change_component():
    payload = config()
    original = copy.deepcopy(payload)
    with pytest.raises(wizard.WizardNavigation):
        wizard.configure_nat(
            payload,
            payload["infra"]["components"][0],
            prefix="nat",
            entry=ComponentEntry("nat-gateway", "infra", "infra.components", "NAT"),
            lookup=Lookup(),
            sdk_factory=WizardSDK,
            prompt=lambda *a, **kw: ("BACK", False),
            is_back=lambda value: value == "BACK",
            emit=lambda _: None,
        )
    assert payload == original


def test_deployment_network_choice_preserves_terraform_ownership(monkeypatch):
    payload = config(owner="terraform")
    monkeypatch.setattr(wizard, "assess_live_quotas", lambda *a, **kw: None)
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: [])
    observed_network_choices = []

    def choose(path, default, **kwargs):
        if path.endswith("inputs.network_id"):
            observed_network_choices.extend(c.value for c in kwargs["choices"])
        return prompt(path, default, **kwargs)

    result = wizard.configure_nat(
        payload,
        payload["infra"]["components"][0],
        prefix="nat",
        entry=ComponentEntry("nat-gateway", "infra", "infra.components", "NAT"),
        lookup=Lookup(),
        prompt=choose,
        is_back=lambda _: False,
        emit=lambda _: None,
        sdk_factory=WizardSDK,
    )
    assert observed_network_choices == ["network-test", "__refresh__"]
    assert result["routing"]["targets"][0]["owner"] == "terraform"
    assert result["inputs"]["network_id"] == "network-test"


class QuotaSDK(WizardSDK):
    def preset_resources(self, platform, preset):
        return {"gpu_count": 0, "vcpu_count": int(preset)}

    def cpu_preset(self, platform, preset):
        return self.preset_resources(platform, preset)


def test_plan_quota_demand_noop_growth_and_replacement():
    before = {"resources": {"platform": "future-cpu", "preset": "4"}}
    after = {"resources": {"platform": "future-cpu", "preset": "8"}}

    def demand(actions):
        rows = plan_requirements(
            config(),
            {
                "resource_changes": [
                    {
                        "type": "nebius_compute_v1_instance",
                        "address": "module.nat.vm",
                        "change": {"actions": actions, "before": before, "after": after},
                    }
                ]
            },
            sdk_factory=QuotaSDK,
        )
        return {r.quota_name: r.required for r in rows}

    assert demand(["no-op"]) == {}
    assert demand(["update"]) == {"compute.instance.non-gpu.vcpu": 4}
    assert demand(["delete", "create"]) == {
        "compute.instance.count": 1,
        "compute.instance.non-gpu.vcpu": 8,
    }
    after["preemptible"] = {}
    assert demand(["delete", "create"]) == {
        "compute.instance.preemptible.count": 1,
        "compute.instance.non-gpu.vcpu": 8,
    }


def test_plan_counts_implicit_pools_and_new_network_routing():
    def change(kind, after):
        return {"type": kind, "address": kind, "change": {"actions": ["create"], "after": after}}

    resources = [
        change("nebius_vpc_v1_network", {}),
        change(
            "nebius_vpc_v1_subnet",
            {"ipv4_private_pools": {"pools": [{"cidrs": [{"cidr": "/28"}]}]}},
        ),
    ]
    rows = plan_requirements(config(), {"resource_changes": resources})
    assert {r.quota_name: r.required for r in rows} == {
        "vpc.network.count": 1,
        "vpc.route-table.count": 1,
        "vpc.route.count": 1,
        "vpc.subnet.count": 1,
        "vpc.pool.count": 1,
    }


def run_wizard(monkeypatch, payload, choose=prompt, lookup=None, emit=None):
    monkeypatch.setattr(wizard, "assess_live_quotas", lambda *a, **kw: None)
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: [])
    return wizard.configure_nat(
        payload,
        payload["infra"]["components"][0],
        prefix="nat",
        entry=ComponentEntry("nat-gateway", "infra", "infra.components", "NAT"),
        lookup=lookup or Lookup(),
        prompt=choose,
        is_back=lambda v: v == "BACK",
        emit=emit or (lambda _: None),
        sdk_factory=WizardSDK,
    )


@pytest.mark.parametrize("saved_routing", [None, True, False])
@pytest.mark.parametrize("tests_enabled", [True, False])
def test_wizard_enables_routes_without_activation_question(
    monkeypatch, saved_routing, tests_enabled
):
    payload = config(enabled=saved_routing)
    row = payload["infra"]["components"][0]
    if saved_routing is None:
        row.pop("routing")
    else:
        row["routing"]["verification"]["enabled"] = tests_enabled
    original = copy.deepcopy(payload)
    visits, emitted = [], []

    def choose(path, default, **kw):
        visits.append(path)
        assert not path.endswith("routing.review")
        if path.endswith("routing.targets"):
            assert any("Selecting external workload subnets delegates" in line for line in emitted)
        if path.endswith("routing.verification.enabled"):
            expected_default = tests_enabled if saved_routing is not None else True
            assert default == ("enabled" if expected_default else "disabled")
            return "enabled" if tests_enabled else "disabled", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, emit=emitted.append)
    assert result["routing"]["enabled"] is True
    assert result["routing"]["verification"]["enabled"] is tests_enabled
    assert result["routing"]["targets"][0]["delegated"] is True
    assert visits[-1] == "nat.routing.verification.enabled"
    assert any(
        "Routes activate during deploy after gateway readiness checks." in line for line in emitted
    )
    assert any("Other subnet fields remain with their existing owner." in line for line in emitted)
    assert payload == original


def test_final_interactive_step_can_backtrack_to_ssh_key(monkeypatch):
    payload = config(enabled=False)
    visits = []
    returned = False

    def choose(path, default, **kw):
        nonlocal returned
        visits.append(path)
        if path.endswith("routing.verification.enabled") and not returned:
            returned = True
            return "BACK", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose)
    assert visits[-3:] == [
        "nat.routing.verification.enabled",
        "nat.inputs.ssh_public_key",
        "nat.routing.verification.enabled",
    ]
    assert result["routing"]["enabled"] is True


def test_final_interactive_step_can_cancel_without_enabling_routes(monkeypatch):
    payload = config(enabled=False)
    original = copy.deepcopy(payload)
    emitted = []

    def choose(path, default, **kw):
        if path.endswith("routing.verification.enabled"):
            return None, True
        return prompt(path, default, **kw)

    with pytest.raises(wizard.WizardNavigation) as error:
        run_wizard(monkeypatch, payload, choose, emit=emitted.append)
    assert error.value.stopped and payload == original
    assert not any("Routes activate during deploy" in line for line in emitted)


@pytest.mark.parametrize("cancel", [False, True])
def test_shared_wizard_publishes_nat_only_on_completion(monkeypatch, cancel):
    from functools import partial

    import yaml

    from nebius_cxcli import cli

    payload = config(enabled=False)
    original = copy.deepcopy(payload["infra"]["components"][0])
    visits = []

    def choose(path, default, **kw):
        visits.append(path)
        if path.endswith("inputs.name"):
            return "edited-nat", False
        if cancel and path.endswith("routing.verification.enabled"):
            return None, True
        return prompt(path, default, **kw)

    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "_prompt_scalar_override", choose)
    monkeypatch.setattr(
        wizard, "configure_nat", partial(wizard.configure_nat, sdk_factory=WizardSDK)
    )
    monkeypatch.setattr(wizard, "assess_live_quotas", lambda *a, **kw: None)
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: [])
    updated_yaml, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"nat"},
        selected_apps=set(),
        infra_entries=(ComponentEntry("nat-gateway", "infra", "infra.components", "NAT"),),
        app_entries=(),
        provider_lookup=Lookup(),
    )
    updated = yaml.safe_load(updated_yaml)["infra"]["components"][0]
    assert visits[-1].endswith("routing.verification.enabled")
    assert not any(path.endswith("routing.review") for path in visits)
    assert completed is not cancel
    if cancel:
        assert updated == original
    else:
        assert updated["routing"]["enabled"] is True
        assert updated["routing"]["verification"]["enabled"] is False
        assert updated["inputs"]["name"] == "edited-nat"
    assert payload["infra"]["components"][0] == original


def test_new_default_is_40_and_back_preserves_answers(monkeypatch):
    payload = config()
    payload["infra"]["components"][0]["inputs"].pop("boot_disk_size_gib")
    visits = []
    returned = False

    def choose(path, default, **kw):
        nonlocal returned
        visits.append(path)
        if path.endswith("inputs.ssh_user_name") and not returned:
            returned = True
            return "BACK", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose)
    assert result["inputs"]["boot_disk_size_gib"] == 40
    assert visits.count("nat.inputs.boot_disk_size_gib") == 2
    assert visits.count("nat.inputs.network_id") == 1


def test_reuse_selector_and_per_subnet_tables_even_when_source_is_shared(monkeypatch):
    payload = config()

    class Inventory(Lookup):
        def resolve(self, *, provider, **kw):
            if provider == "project_subnets":
                return [OptionChoice(value, value) for value in ("gateway", "work-one", "work-two")]
            return super().resolve(provider=provider, **kw)

    def choose(path, default, **kw):
        assert not path.endswith("inputs.gateway_subnet_mode")
        if path.endswith(".gateway_subnet"):
            assert default == "__create_subnet__"
            return "gateway", False
        if path.endswith("routing.targets"):
            assert "gateway" not in {c.value for c in kw["choices"]}
            return ["work-one", "work-two"], False
        return prompt(path, default, **kw)

    from nebius_cxcli import nat_gateway_address_plan
    from nebius_cxcli.nat_gateway_address_space import AddressAssessment

    monkeypatch.setattr(
        nat_gateway_address_plan,
        "assess_config_address_space",
        lambda *a, **kw: AddressAssessment("feasible", "fixture"),
    )
    result = run_wizard(monkeypatch, payload, choose, Inventory())
    assert result["inputs"]["gateway_subnet_mode"] == "reuse"
    assert "gateway_subnet_cidr" not in result["inputs"]
    assert result["inputs"]["gateway_subnet_id"] == "gateway"
    assert len(result["routing"]["policies"]) == 2
    assert len({t["policy"] for t in result["routing"]["targets"]}) == 2


def test_refresh_and_quit_are_transactional(monkeypatch):
    payload = config()
    original = copy.deepcopy(payload)
    calls = []
    lookup = Lookup()
    lookup.refresh = lambda: calls.append("refresh")

    def choose(path, default, **kw):
        if path.endswith("inputs.network_id"):
            if not calls:
                return "__refresh__", False
            return None, True
        return prompt(path, default, **kw)

    with pytest.raises(wizard.WizardNavigation) as error:
        run_wizard(monkeypatch, payload, choose, lookup)
    assert error.value.stopped and calls == ["refresh"] and payload == original


def test_reediting_active_target_preserves_original_source_policy(monkeypatch):
    from nebius_cxcli.nat_gateway import policy_snapshot, target_policy_key

    payload = config()
    row = payload["infra"]["components"][0]
    original = copy.deepcopy(row["routing"]["policies"]["default"])
    target = row["routing"]["targets"][0]
    key = target_policy_key(target)
    target["policy"] = key
    row["routing"]["policies"] = {key: original}
    monkeypatch.setattr(
        WizardSDK,
        "route_policy",
        lambda *a: policy_snapshot(
            "table-installed-nat",
            [{"destination_cidr": "0.0.0.0/0", "allocation_id": "nat-allocation"}],
        ),
    )
    result = run_wizard(monkeypatch, payload)
    assert result["routing"]["policies"] == {key: original}


def test_catalog_does_not_materialize_create_only_cidr_into_reuse():
    from pathlib import Path

    import yaml

    catalog = yaml.safe_load((Path(__file__).parents[1] / "component_sources.yaml").read_text())
    assert (
        "inputs.gateway_subnet_cidr"
        not in catalog["components"]["infra"]["nat-gateway"]["defaults"]
    )


def test_inventory_refresh_invalidates_vpc_state_snapshot(monkeypatch):
    from nebius_cxcli.vpc_identity import VpcIdentityReader

    lookup = ProviderOptionLookup()
    reader = VpcIdentityReader(NS())
    lookup._vpc_identity_reader = reader
    evidence = [VpcIdentitySnapshot("unavailable"), VpcIdentitySnapshot("available")]
    monkeypatch.setattr(reader, "_read", lambda _: evidence.pop(0))
    payload = config()
    assert lookup.vpc_identity(payload).status == "unavailable"
    lookup.refresh()
    assert lookup.vpc_identity(payload).status == "available"


@pytest.mark.parametrize("tty", [False, True])
def test_new_target_prompt_does_not_default_to_refresh(monkeypatch, tty):
    from nebius_cxcli import cli

    payload = config()
    payload["infra"]["components"][0]["routing"].update(targets=[], policies={})
    monkeypatch.setattr(cli, "_is_tty_session", lambda: tty)
    if tty:
        import questionary

        def checkbox(*args, **kwargs):
            return NS(defaults=[c.value for c in kwargs["choices"] if c.checked])

        monkeypatch.setattr(questionary, "checkbox", checkbox)
        monkeypatch.setattr(cli, "_ask_questionary_with_wizard_navigation", lambda q: q.defaults)
    else:
        monkeypatch.setattr(cli.typer, "prompt", lambda *a, **kw: kw["default"])

    def choose(path, default, **kw):
        if path.endswith("routing.targets"):
            selected, stopped = cli._prompt_scalar_override(path, default, **kw)
            assert not stopped and selected == []
            return ["subnet-work"], False
        return prompt(path, default, **kw)

    run_wizard(monkeypatch, payload, choose)


def test_planned_private_only_subnet_is_not_gateway_eligible(monkeypatch):
    from nebius_cxcli.nat_gateway_choices import NatChoices

    payload = config(owner="terraform")
    vpc = payload["infra"]["components"][1]
    vpc["inputs"]["network"] = {"name": "new", "ipv4_private_cidrs": ["10.0.0.0/8"]}
    vpc["inputs"]["subnets"]["work"] = {
        "ipv4_private_cidrs": ["10.0.1.0/24"],
        "use_network_public_pools": False,
        "ipv4_public_cidrs": [],
    }
    inventory = NatChoices(
        payload, Lookup(), WizardSDK("project-test"), lambda _: None, "nat", NS()
    )
    monkeypatch.setattr(
        inventory,
        "provider",
        lambda *a, **kw: [
            OptionChoice(
                "planned:vpc@network.subnets.work.id", "Private", metadata={"planned": True}
            )
        ],
    )
    result = inventory.subnets(OptionChoice("planned:vpc@network.network_id", "VPC"))
    assert result[0].metadata["public_ip_capable"] is False
    assert "public" in result[0].metadata["reason"]


def test_backtracking_restores_original_policy_after_temporary_deselection(monkeypatch):
    from nebius_cxcli.nat_gateway import policy_snapshot, target_policy_key

    payload = config()
    row = payload["infra"]["components"][0]
    old_policy = copy.deepcopy(row["routing"]["policies"]["default"])
    targets = [
        {"owner": "external", "subnet_id": value, "delegated": True} for value in ("one", "two")
    ]
    for target in targets:
        target["policy"] = target_policy_key(target)
    row["routing"].update(
        targets=targets, policies={t["policy"]: copy.deepcopy(old_policy) for t in targets}
    )
    original = copy.deepcopy(row["routing"]["policies"])

    class Inventory(Lookup):
        def resolve(self, *, provider, **kw):
            return (
                [OptionChoice(value, value) for value in ("one", "two")]
                if provider == "project_subnets"
                else super().resolve(provider=provider, **kw)
            )

    monkeypatch.setattr(
        WizardSDK,
        "route_policy",
        lambda *a: policy_snapshot(
            "installed-nat", [{"destination_cidr": "0.0.0.0/0", "allocation_id": "nat"}]
        ),
    )
    visits = 0

    def choose(path, default, **kw):
        nonlocal visits
        if path.endswith("routing.targets"):
            visits += 1
            return (["one"] if visits == 1 else ["one", "two"]), False
        if path.endswith("inputs.platform") and visits == 1:
            return "BACK", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, Inventory())
    assert result["routing"]["policies"] == original
