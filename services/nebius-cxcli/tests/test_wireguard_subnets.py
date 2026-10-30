"""VPN route choices exercise real SDK shapes and the actual field wizard."""

from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
import yaml
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.vpc.v1 import (
    GetNetworkRequest,
    IPv4PrivateSubnetPools,
    ListSubnetsResponse,
    Network,
    Subnet,
    SubnetSpec,
    SubnetStatus,
    SubnetStatusPool,
)

from nebius_cxcli import cli
from nebius_cxcli import wireguard_subnets as routes
from nebius_cxcli.provider_options import OptionChoice
from nebius_cxcli.wireguard_secret_api import SecretStashAPI


def subnet(name="workers", *, network="network", cidrs=("10.10.0.0/24",), shared=False):
    return Subnet(
        metadata=ResourceMetadata(id="subnet-" + name, name=name, parent_id="project"),
        spec=SubnetSpec(
            network_id=network,
            ipv4_private_pools=IPv4PrivateSubnetPools(use_network_pools=shared),
        ),
        status=SubnetStatus(ipv4_private_pools=[SubnetStatusPool(cidrs=list(cidrs))]),
    )


def sdk_listing(monkeypatch, pages, *, network_parent="project"):
    api = SecretStashAPI(object())
    requests = []

    def call(method, request):
        requests.append(request)
        if isinstance(request, GetNetworkRequest):
            return Network(metadata=ResourceMetadata(id="network", parent_id=network_parent))
        assert request.parent_id == "project"
        assert request.page_size == 100
        result = pages[request.page_token]
        if isinstance(result, Exception):
            raise result
        return result

    api.call = call
    monkeypatch.setattr(routes, "wireguard_api", lambda: nullcontext(api))
    return requests


def test_live_routes_page_filter_and_deduplicate_shared_ranges(monkeypatch):
    requests = sdk_listing(
        monkeypatch,
        {
            "": ListSubnetsResponse(
                items=[subnet(), subnet("other-vpc", network="other")], next_page_token="next"
            ),
            "next": ListSubnetsResponse(
                items=[subnet("shared", cidrs=("10.10.0.0/24", "10.20.0.0/24"), shared=True)]
            ),
        },
    )
    choices = routes.live_subnet_choices("project", "network")
    assert [c.value for c in choices] == ["10.10.0.0/24", "10.20.0.0/24"]
    assert "subnet-workers" in choices[0].label
    assert "subnet-shared" in choices[0].label
    assert "shared network pool" in choices[0].label
    assert all("other-vpc" not in c.label and not c.recommended for c in choices)
    assert [r.page_token for r in requests[1:]] == ["", "next"]


@pytest.mark.parametrize("cidr", ["/24", "10.0.0.1", "not-cidr", "2001:db8::/64"])
def test_invalid_sdk_ranges_never_become_route_choices(monkeypatch, cidr):
    sdk_listing(monkeypatch, {"": ListSubnetsResponse(items=[subnet(cidrs=(cidr,))])})
    with pytest.raises(ValueError):
        routes.live_subnet_choices("project", "network")


def test_omitted_pool_spec_is_labelled_shared(monkeypatch):
    item = Subnet(
        metadata=ResourceMetadata(id="subnet", parent_id="project"),
        spec=SubnetSpec(network_id="network"),
        status=SubnetStatus(ipv4_private_pools=[SubnetStatusPool(cidrs=["10.0.0.0/24"])]),
    )
    sdk_listing(monkeypatch, {"": ListSubnetsResponse(items=[item])})
    assert "shared network pool" in routes.live_subnet_choices("project", "network")[0].label


@pytest.mark.parametrize("failure", ["denied", "loop", "foreign-subnet", "foreign-network"])
def test_incomplete_or_foreign_listing_is_rejected(monkeypatch, failure):
    item = subnet()
    if failure == "foreign-subnet":
        item.metadata.parent_id = "foreign"
    pages = {"": ListSubnetsResponse(items=[item], next_page_token="next")}
    pages["next"] = (
        RuntimeError("provider-private-detail")
        if failure == "denied"
        else ListSubnetsResponse(next_page_token="next")
    )
    sdk_listing(
        monkeypatch, pages, network_parent="foreign" if failure == "foreign-network" else "project"
    )
    with pytest.raises((ValueError, RuntimeError)):
        routes.live_subnet_choices("project", "network")


def payload():
    return {"client_info": {"nebius": {"project_id": "project"}}}


@pytest.mark.parametrize("binding", [False, True])
@pytest.mark.parametrize("state", ["fresh", "available", "stale", "foreign", "missing"])
def test_managed_network_requires_usable_scoped_identity(binding, state):
    row = {"inputs": {"network_id": "planned:vpc@managed.network_id"}}
    if binding:
        row = {
            "bindings": {
                "inputs.network_id": {
                    "source_component": "vpc",
                    "source_instance": "managed",
                    "source_output": "network_id",
                }
            }
        }
    identity = NS(id="network", parent_id="foreign" if state == "foreign" else "project")
    snapshot = NS(
        status=state if state in {"fresh", "available", "stale"} else "fresh",
        networks={} if state == "missing" else {"managed": identity},
    )
    lookup = NS(vpc_identity=lambda _: snapshot)
    assert routes.selected_network_id(row, payload(), lookup) == (
        "network" if state in {"fresh", "available"} else ""
    )


def prompt(monkeypatch, answers, *, current=None, listing=None, row=None):
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    seen = []
    values = iter(answers)

    def ask(label, **kwargs):
        seen.append((label, kwargs.get("default")))
        value = next(values)
        return kwargs.get("default", "") if value is None else value

    monkeypatch.setattr(cli.typer, "prompt", ask)
    if isinstance(listing, Exception):

        def discover(*_):
            raise listing
    else:

        def discover(*_):
            return (
                listing
                if listing is not None
                else [OptionChoice("10.10.0.0/24", "Workers — subnet-workers")]
            )

    monkeypatch.setattr(routes, "live_subnet_choices", discover)
    result = routes.prompt_local_subnets(
        current=current or [],
        row=row or {"inputs": {"network_id": "network"}},
        payload=payload(),
        lookup=None,
    )
    return result, seen


def test_fresh_route_prompt_does_not_select_all(monkeypatch):
    result, seen = prompt(monkeypatch, [None])
    assert result == ([], False)
    assert seen[0][1] == ""


def test_select_live_routes_saves_cidrs(monkeypatch):
    assert prompt(monkeypatch, ["1"])[0] == (["10.10.0.0/24"], False)


def test_saved_manual_route_is_visible_and_preserved(monkeypatch, capsys):
    result, _ = prompt(monkeypatch, [None], current=["10.30.0.0/24"])
    assert result == (["10.30.0.0/24"], False)
    assert "saved route" in capsys.readouterr().out


def test_manual_routes_validate_normalize_and_deduplicate(monkeypatch):
    result, _ = prompt(monkeypatch, ["1,2", "2001:db8::/64", "10.30.0.5/24,10.10.0.0/24"])
    assert result == (["10.10.0.0/24", "10.30.0.0/24"], False)


@pytest.mark.parametrize("answers", [["q"], ["2", "q"], ["qq"], ["2", "qq"]])
def test_route_navigation_preserved(monkeypatch, answers):
    result, _ = prompt(monkeypatch, answers)
    if answers[-1] == "qq":
        assert result[1]
    else:
        assert cli._wizard_backtrack_requested(result[0])


@pytest.mark.parametrize("listing", [[], RuntimeError("provider-private-detail")])
def test_empty_and_failed_discovery_are_distinct(monkeypatch, capsys, listing):
    result, _ = prompt(monkeypatch, ["1", "10.30.0.0/24"], listing=listing)
    assert result == (["10.30.0.0/24"], False)
    output = capsys.readouterr().out
    assert ("discovery failed" in output) is isinstance(listing, Exception)
    assert ("No resolved private" in output) is isinstance(listing, list)
    assert "provider-private-detail" not in output


def test_actual_wizard_persists_selected_cidr_list(monkeypatch):
    from pathlib import Path

    from nebius_cxcli.components import ComponentEntry

    source = str(Path(__file__).resolve().parents[3] / "platform-infra/modules/wireguard-gw")
    specs = tuple(spec for spec in cli.module_variables(source) if spec.name == "local_subnets")
    entry = ComponentEntry(
        id="wireguard-gw",
        scope="infra",
        config_path="infra.wireguard-gw",
        description="VPN",
        source=source,
    )
    monkeypatch.setattr(cli, "module_variables", lambda _: specs)
    monkeypatch.setattr(cli, "module_required_variables", lambda _: ("local_subnets",))
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda _: {"local_subnets"})
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: "2")
    monkeypatch.setattr(
        routes,
        "live_subnet_choices",
        lambda *a: [
            OptionChoice("10.10.0.0/24", "Workers"),
            OptionChoice("10.20.0.0/24", "Services"),
        ],
    )
    config = {
        **payload(),
        "version": "v2",
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "vpn",
                    "enabled": True,
                    "inputs": {"name": "vpn", "network_id": "network", "local_subnets": []},
                }
            ]
        },
        "apps": {"charts": []},
    }
    updated, complete = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(config),
        selected_infra={"vpn"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
    )
    assert complete
    assert yaml.safe_load(updated)["infra"]["components"][0]["inputs"]["local_subnets"] == [
        "10.20.0.0/24"
    ]


def test_text_prompt_can_explicitly_clear_saved_routes(monkeypatch):
    result, _ = prompt(monkeypatch, ["3"], current=["10.10.0.0/24"])
    assert result == ([], False)


def test_tunnel_only_rejects_conflicting_destinations(monkeypatch, capsys):
    result, _ = prompt(monkeypatch, ["1,3", "3"])
    assert result == ([], False)
    assert "by itself" in capsys.readouterr().out


def test_tty_defaults_and_clear_are_explicit(monkeypatch):
    import questionary

    monkeypatch.setattr(cli, "_is_tty_session", lambda: True)
    monkeypatch.setattr(
        routes, "live_subnet_choices", lambda *a: [OptionChoice("10.10.0.0/24", "Workers")]
    )
    checked = []

    def checkbox(label, **kwargs):
        checked.extend(choice.value for choice in kwargs["choices"] if choice.checked)
        return object()

    monkeypatch.setattr(questionary, "checkbox", checkbox)
    monkeypatch.setattr(cli, "_ask_questionary_with_wizard_navigation", lambda _: [])
    assert routes.prompt_local_subnets(
        current=["10.10.0.0/24"],
        row={"inputs": {"network_id": "network"}},
        payload=payload(),
        lookup=None,
    ) == ([], False)
    assert checked == ["10.10.0.0/24"]
