from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_address_plan
from nebius_cxcli import nat_gateway_wizard as wizard
from nebius_cxcli.nat_gateway_address_space import AddressAssessment
from nebius_cxcli.provider_options import OptionChoice
from nebius_cxcli.vpc_identity import VpcResourceIdentity
from test_nat_gateway import config
from test_nat_gateway_wizard_quota import Lookup, WizardSDK, prompt, run_wizard


class GatewayLookup(Lookup):
    subnet_ids = ("gateway", "subnet-work")

    def resolve(self, *, provider, **kwargs):
        if provider == "project_subnets":
            assert kwargs["args"]["network_id"] == "network-test"
            return [
                OptionChoice(
                    value,
                    value,
                    recommended=True,
                    metadata={
                        "name": value,
                        "network_id": "network-test",
                        "parent_id": "project-test",
                    },
                )
                for value in self.subnet_ids
            ]
        return super().resolve(provider=provider, **kwargs)


@pytest.fixture(autouse=True)
def address_assessment(monkeypatch):
    monkeypatch.setattr(
        nat_gateway_address_plan,
        "assess_config_address_space",
        lambda *a, **kw: AddressAssessment("feasible", "fixture"),
    )


@pytest.mark.parametrize("planned", [False, True])
@pytest.mark.parametrize("saved", [False, True])
def test_direct_managed_gateway_choice_preserves_binding_and_identity(monkeypatch, planned, saved):
    payload = config(owner="terraform")
    vpc = payload["infra"]["components"][1]
    vpc["inputs"]["subnets"]["gateway"] = {"name": "gateway", "ipv4_private_cidrs": ["/28"]}
    other = copy.deepcopy(vpc)
    other["instance_id"] = "other"
    other["inputs"]["network"]["existing_id"] = "network-other"
    payload["infra"]["components"].append(other)
    token = "planned:vpc@network.subnets.gateway.id"
    work = "planned:vpc@network.subnets.work.id"
    if saved:
        from nebius_cxcli.nat_gateway_choices import select_binding

        row = payload["infra"]["components"][0]
        row["inputs"]["gateway_subnet_mode"] = "reuse"
        row["inputs"].pop("gateway_subnet_cidr")
        select_binding(row, "gateway_subnet_id", token)

    class Inventory(GatewayLookup):
        subnet_ids = ("subnet-work",) if planned else ("gateway", "subnet-work")

        def vpc_identity(self, payload):
            snapshot = super().vpc_identity(payload)
            if not planned:
                snapshot.subnets[("network", "gateway")] = VpcResourceIdentity(
                    "gateway", "project-test", "network-test"
                )
            return snapshot

    visited = []

    def choose(path, default, **kw):
        visited.append(path)
        if path.endswith(".gateway_subnet"):
            choices = kw["choices"]
            assert default == (token if saved else "__create_subnet__")
            values = [c.value for c in choices]
            assert set(values) == {token, work, "__create_subnet__", "__refresh__"}
            assert values[-2:] == ["__create_subnet__", "__refresh__"]
            assert "gateway" not in values  # Managed identity is shown once as a binding.
            choice = next(c for c in choices if c.value == token)
            assert choice.metadata["planned"] is planned
            assert choice.label.startswith("planned:" if planned else "existing:")
            assert "public IPs allowed" in choice.label and "direct Internet egress" in choice.label
            assert "existing; managed by" not in choice.label
            if not planned:
                assert choice.metadata["binding_ref"] == token
                assert choice.metadata["live_id"] == "gateway"
            if planned:
                assert values.index(work) < values.index(token)
            return token, False
        if path.endswith("routing.targets"):
            assert token not in {c.value for c in kw["choices"]}
            assert all("existing; managed by" not in c.label for c in kw["choices"])
            return [work], False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, Inventory())
    assert result["inputs"]["gateway_subnet_mode"] == "reuse"
    assert "gateway_subnet_cidr" not in result["inputs"]
    assert "gateway_subnet_id" not in result["inputs"]
    assert result["bindings"]["inputs.gateway_subnet_id"] == {
        "source_component": "vpc",
        "source_instance": "network",
        "source_output": "subnets",
        "key": "gateway",
        "attribute": "id",
    }
    assert visited.count("nat.gateway_subnet") == 1
    assert "nat.inputs.admin_cidrs" not in visited
    assert "nat.inputs.gateway_subnet_mode" not in visited
    assert "nat.inputs.gateway_subnet_cidr" not in visited


def test_creation_clears_saved_gateway_binding(monkeypatch):
    from nebius_cxcli.nat_gateway_choices import select_binding

    payload = config(owner="terraform")
    row = payload["infra"]["components"][0]
    row["inputs"]["gateway_subnet_mode"] = "reuse"
    row["inputs"].pop("gateway_subnet_cidr")
    token = "planned:vpc@network.subnets.work.id"
    select_binding(row, "gateway_subnet_id", token)

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            assert default == token
            return "__create_subnet__", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose)
    assert result["inputs"]["gateway_subnet_mode"] == "create"
    assert result["inputs"]["gateway_subnet_cidr"] == "/28"
    assert "gateway_subnet_id" not in result["inputs"]
    assert "inputs.gateway_subnet_id" not in result["bindings"]


def test_saved_literal_for_managed_subnet_selects_its_canonical_binding(monkeypatch):
    payload = config(owner="terraform")
    row = payload["infra"]["components"][0]
    row["inputs"].update(gateway_subnet_mode="reuse", gateway_subnet_id="subnet-work")
    row["inputs"].pop("gateway_subnet_cidr")
    token = "planned:vpc@network.subnets.work.id"

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            assert default == token
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, GatewayLookup())
    assert result["inputs"]["gateway_subnet_mode"] == "reuse"
    assert "gateway_subnet_id" not in result["inputs"]
    assert result["bindings"]["inputs.gateway_subnet_id"]["key"] == "work"
    assert result["routing"]["targets"][0]["subnet_id"] == "gateway"


def test_planned_private_only_subnet_is_excluded_from_picker(monkeypatch):
    payload = config(owner="terraform")
    payload["infra"]["components"][1]["inputs"]["subnets"]["private"] = {
        "name": "private",
        "ipv4_private_cidrs": ["/28"],
        "use_network_public_pools": False,
    }
    messages = []

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            assert "planned:vpc@network.subnets.private.id" not in {c.value for c in kw["choices"]}
        return prompt(path, default, **kw)

    run_wizard(monkeypatch, payload, choose, emit=messages.append)
    assert any("Unavailable for gateway: planned: private" in m for m in messages)


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("reuse", [False, True])
def test_picker_prompt_adapters_keep_create_default_and_allow_direct_reuse(monkeypatch, tty, reuse):
    from nebius_cxcli import cli

    monkeypatch.setattr(cli, "_is_tty_session", lambda: tty)
    calls = []
    if tty:
        import questionary

        def select(*args, **kwargs):
            calls.append(kwargs["default"])
            return NS(answer="gateway" if reuse else kwargs["default"])

        monkeypatch.setattr(questionary, "select", select)
        monkeypatch.setattr(cli, "_ask_questionary_with_wizard_navigation", lambda q: q.answer)
    else:

        def text_prompt(*args, **kwargs):
            calls.append(kwargs["default"])
            return "1" if reuse else ""

        monkeypatch.setattr(cli.typer, "prompt", text_prompt)

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            return cli._prompt_scalar_override(path, default, **kw)
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, config(), choose, GatewayLookup())
    assert calls == ["__create_subnet__"]
    assert result["inputs"]["gateway_subnet_mode"] == ("reuse" if reuse else "create")
    assert "gateway_subnet" not in result
    if reuse:
        assert result["inputs"]["gateway_subnet_id"] == "gateway"
    else:
        assert result["inputs"]["gateway_subnet_cidr"] == "/28"


@pytest.mark.parametrize("saved", ["gateway", "missing"])
def test_saved_gateway_selection_is_preserved_or_explained(monkeypatch, saved):
    payload = config()
    inputs = payload["infra"]["components"][0]["inputs"]
    inputs.update(gateway_subnet_mode="reuse", gateway_subnet_id=saved)
    inputs.pop("gateway_subnet_cidr")
    messages = []

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            assert default == (saved if saved == "gateway" else "__create_subnet__")
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, GatewayLookup(), messages.append)
    if saved == "gateway":
        assert result["inputs"]["gateway_subnet_id"] == saved
    else:
        assert result["inputs"]["gateway_subnet_mode"] == "create"
        assert "gateway_subnet_id" not in result["inputs"]
        assert any("Saved gateway subnet 'missing' is unavailable" in m for m in messages)


@pytest.mark.parametrize(
    "reason",
    [
        "public IP allocation is unavailable",
        "no direct default Internet route",
        "subnet is not ready",
    ],
)
def test_unsuitable_gateway_is_explained_and_cannot_be_selected(monkeypatch, reason):
    original_info = WizardSDK.subnet_info

    def subnet_info(self, subnet_id, network):
        info = original_info(self, subnet_id, network)
        if subnet_id == "gateway":
            info["reason"] = reason
        return info

    monkeypatch.setattr(WizardSDK, "subnet_info", subnet_info)
    messages, visits = [], []

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            assert "gateway" not in {c.value for c in kw["choices"]}
            visits.append(path)
            if len(visits) == 1:
                return "gateway", False  # Controller also rejects stale/unlisted answers.
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, config(), choose, GatewayLookup(), messages.append)
    assert result["inputs"]["gateway_subnet_mode"] == "create"
    assert len(visits) == 2
    assert any("Unavailable for gateway" in m and reason in m for m in messages)
    assert any("Select gateway_subnet from the displayed choices" in m for m in messages)


def test_empty_inventory_offers_creation_and_refresh_refetches(monkeypatch):
    payload = config()
    original = copy.deepcopy(payload)

    class Inventory(GatewayLookup):
        subnet_ids = ()

        def refresh(self):
            self.subnet_ids = ("gateway", "subnet-work")

    observed = []

    def choose(path, default, **kw):
        if path.endswith(".gateway_subnet"):
            observed.append([c.value for c in kw["choices"]])
            if len(observed) == 1:
                assert observed[0] == ["__create_subnet__", "__refresh__"]
                return "__refresh__", False
            return None, True
        return prompt(path, default, **kw)

    with pytest.raises(wizard.WizardNavigation) as error:
        run_wizard(monkeypatch, payload, choose, Inventory())
    assert error.value.stopped and payload == original
    assert observed[1] == ["gateway", "subnet-work", "__create_subnet__", "__refresh__"]


def test_incomplete_gateway_inventory_retries_without_showing_empty_picker(monkeypatch):
    class Inventory(GatewayLookup):
        failed = False
        refreshed = False

        def resolve(self, *, provider, **kwargs):
            if provider == "project_subnets" and not self.refreshed:
                self.failed = True
                return []
            return super().resolve(provider=provider, **kwargs)

        def last_error(self):
            return "discovery incomplete" if self.failed else None

        def refresh(self):
            self.failed, self.refreshed = False, True

    visited, messages = [], []

    def choose(path, default, **kw):
        visited.append(path)
        return prompt(path, default, **kw)

    run_wizard(monkeypatch, config(), choose, Inventory(), messages.append)
    assert visited.index("nat.retry") < visited.index("nat.gateway_subnet")
    assert visited.count("nat.gateway_subnet") == 1
    assert any("discovery incomplete" in m for m in messages)


@pytest.mark.parametrize("reuse_first", [False, True])
def test_backtracking_visits_visible_steps_and_switches_mode(monkeypatch, reuse_first):
    payload = config()
    original = copy.deepcopy(payload)
    visits, picks = [], []
    targets = 0

    def choose(path, default, **kw):
        nonlocal targets
        visits.append(path)
        assert len(visits) < 40, "Wizard did not advance through the scripted navigation"
        if path.endswith(".gateway_subnet"):
            reuse = reuse_first if not picks else not reuse_first
            choice = "gateway" if reuse else "__create_subnet__"
            picks.append(choice)
            return choice, False
        if path.endswith("routing.targets"):
            targets += 1
            if targets == 1:
                return "BACK", False
        if path.endswith("inputs.gateway_subnet_cidr") and targets == 1 and len(picks) == 1:
            return "BACK", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, GatewayLookup())
    first_targets = visits.index("nat.routing.targets")
    assert visits[first_targets + 1] == (
        "nat.gateway_subnet" if reuse_first else "nat.inputs.gateway_subnet_cidr"
    )
    assert len(picks) == 2 and payload == original
    if reuse_first:
        assert result["inputs"]["gateway_subnet_mode"] == "create"
        assert "gateway_subnet_id" not in result["inputs"]
        assert result["inputs"]["gateway_subnet_cidr"] == "/28"
    else:
        assert result["inputs"]["gateway_subnet_mode"] == "reuse"
        assert "gateway_subnet_cidr" not in result["inputs"]


def test_changing_vpc_clears_reused_gateway_and_workload_defaults(monkeypatch):
    class Inventory(Lookup):
        def resolve(self, *, provider, args, **kwargs):
            if provider == "project_networks":
                return [OptionChoice(n, n) for n in ("network-test", "network-other")]
            if provider == "project_subnets":
                network = args["network_id"]
                return [OptionChoice(f"{network}-{n}", n) for n in ("gateway", "work")]
            return super().resolve(provider=provider, args=args, **kwargs)

    networks = 0
    returned = False

    def choose(path, default, **kw):
        nonlocal networks, returned
        if path.endswith("inputs.network_id"):
            networks += 1
            return ("network-test" if networks == 1 else "network-other"), False
        if path.endswith(".gateway_subnet"):
            if networks == 1 and returned:
                return "BACK", False
            assert default == "__create_subnet__"
            network = "network-test" if networks == 1 else "network-other"
            return f"{network}-gateway", False
        if path.endswith("routing.targets"):
            if networks == 1:
                returned = True
                return "BACK", False
            assert default == []
            assert {c.value for c in kw["choices"]} == {"network-other-work", "__refresh__"}
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, config(), choose, Inventory())
    assert result["inputs"]["network_id"] == "network-other"
    assert result["inputs"]["gateway_subnet_id"] == "network-other-gateway"
    assert result["routing"]["targets"][0]["subnet_id"] == "network-other-work"


def test_managed_network_alias_preserves_gateway_targets_and_route_preimage(monkeypatch):
    from nebius_cxcli.nat_gateway import policy_snapshot

    payload = config(owner="terraform")
    row, vpc = payload["infra"]["components"]
    vpc["inputs"]["network"] = {"name": "network", "ipv4_private_cidrs": ["10.0.0.0/8"]}
    row["inputs"].update(gateway_subnet_mode="reuse", gateway_subnet_id="gateway")
    row["inputs"].pop("gateway_subnet_cidr")
    original_policy = copy.deepcopy(row["routing"]["policies"]["default"])
    original_info = WizardSDK.subnet_info

    def subnet_info(self, subnet_id, network):
        info = original_info(self, subnet_id, network)
        if subnet_id == "subnet-work":
            info["policy"] = policy_snapshot(
                "installed-nat", [{"destination_cidr": "0.0.0.0/0", "allocation_id": "nat"}]
            )
        return info

    monkeypatch.setattr(WizardSDK, "subnet_info", subnet_info)
    work = "planned:vpc@network.subnets.work.id"

    def choose(path, default, **kw):
        if path.endswith("inputs.network_id"):
            assert default == "planned:vpc@network.network_id"
        if path.endswith(".gateway_subnet"):
            assert default == "gateway"
        if path.endswith("routing.targets"):
            assert default == [work]
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, GatewayLookup())
    assert result["inputs"]["gateway_subnet_id"] == "gateway"
    assert result["bindings"]["inputs.network_id"]["source_instance"] == "network"
    assert list(result["routing"]["policies"].values()) == [original_policy]


@pytest.mark.parametrize("provider", ["project_networks", "project_subnets"])
def test_incomplete_inventory_without_error_requires_retry(monkeypatch, provider):
    class Inventory(GatewayLookup):
        current = ""
        refreshed = False

        def resolve(self, *, provider, **kwargs):
            self.current = provider
            return super().resolve(provider=provider, **kwargs)

        def vpc_discovery_complete(self):
            return self.refreshed or self.current != provider

        def refresh(self):
            self.refreshed = True

    visited, messages = [], []

    def choose(path, default, **kw):
        visited.append(path)
        return prompt(path, default, **kw)

    run_wizard(monkeypatch, config(), choose, Inventory(), messages.append)
    picker = "nat.inputs.network_id" if provider == "project_networks" else "nat.gateway_subnet"
    assert "nat.retry" in visited
    assert visited.index("nat.retry") < visited.index(picker)
    assert any("incomplete" in message for message in messages)


@pytest.mark.parametrize("managed_first", [False, True])
def test_refresh_never_exposes_gateway_as_workload_when_identity_representation_changes(
    monkeypatch, managed_first
):
    payload = config(owner="terraform")
    payload["infra"]["components"][1]["inputs"]["subnets"]["gateway"] = {"name": "gateway"}
    gateway_binding = "planned:vpc@network.subnets.gateway.id"

    class Inventory(GatewayLookup):
        managed = managed_first

        def vpc_identity(self, payload):
            snapshot = super().vpc_identity(payload)
            if self.managed:
                snapshot.subnets[("network", "gateway")] = VpcResourceIdentity(
                    "gateway", "project-test", "network-test"
                )
            return snapshot

        def refresh(self):
            self.managed = not managed_first

    targets = 0

    def choose(path, default, **kw):
        nonlocal targets
        if path.endswith(".gateway_subnet"):
            return (gateway_binding if managed_first else "gateway"), False
        if path.endswith("routing.targets"):
            targets += 1
            assert targets <= 2
            assert not {"gateway", gateway_binding} & {c.value for c in kw["choices"]}
            if targets == 1:
                return "__refresh__", False
        return prompt(path, default, **kw)

    result = run_wizard(monkeypatch, payload, choose, Inventory())
    assert targets == 2
    assert result["routing"]["targets"][0]["subnet_key"] == "work"
