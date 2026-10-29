from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import nebius_cxcli.cli as cli
import nebius_cxcli.vpc_identity as identity_module
from nebius_cxcli.components import component_entries
from nebius_cxcli.generated_manifest import GENERATED_MANIFEST_SCHEMA
from nebius_cxcli.object_storage_errors import ObjectStorageError
from nebius_cxcli.paths import resolve_project_paths
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
from nebius_cxcli.terraform_backend import backend_settings_from_config
from nebius_cxcli.vpc_choices import planned_subnet_choices_for_vpc, reconcile_vpc_choices
from nebius_cxcli.vpc_identity import (
    VpcIdentityReader,
    VpcIdentitySnapshot,
    VpcResourceIdentity,
    parse_vpc_state,
)

NETWORK = "vpcnetwork-test"
SUBNET = "vpcsubnet-test"
TOKEN = "planned:vpc@vpc.subnets.private.id"


def payload():
    return {
        "client_info": {
            "client_name": "example",
            "nebius": {
                "tenant_id": "tenant-test",
                "project_id": "project-test",
                "region_id": "eu-north1",
            },
        },
        "infra": {
            "components": [
                {
                    "id": "vpc",
                    "instance_id": "vpc",
                    "enabled": True,
                    "inputs": {
                        "network": {"existing_id": NETWORK},
                        "subnets": {
                            "private": {"name": "private", "ipv4_private_cidrs": ["10.0.0.0/16"]}
                        },
                    },
                },
                {
                    "id": "mk8s",
                    "instance_id": "cluster",
                    "enabled": True,
                    "inputs": {"cluster": {"network_id": NETWORK}},
                },
            ]
        },
    }


def live(subnet_id=SUBNET, **metadata):
    return OptionChoice(
        value=subnet_id,
        label=f"{subnet_id} (private)",
        metadata={
            "name": "private",
            "parent_id": "project-test",
            "network_id": NETWORK,
            "private_cidrs": ("10.0.0.0/16",),
            **metadata,
        },
    )


def configured():
    return planned_subnet_choices_for_vpc(
        instance_id="vpc", inputs=payload()["infra"]["components"][0]["inputs"]
    )


def snapshot():
    return VpcIdentitySnapshot(
        "available",
        {"vpc": VpcResourceIdentity(NETWORK, "project-test", NETWORK)},
        {("vpc", "private"): VpcResourceIdentity(SUBNET, "project-test", NETWORK)},
    )


def reconcile(*, observed=None, identity=None, complete=True):
    return reconcile_vpc_choices(
        live=[live()] if observed is None else observed,
        configured=configured(),
        snapshot=identity or snapshot(),
        project_id="project-test",
        network_id=NETWORK,
        discovery_complete=complete,
    )


def test_existing_managed_subnet_appears_once_with_live_metadata_and_binding():
    result = reconcile()
    assert not result.diagnostics
    assert len(result.choices) == 1
    choice = result.choices[0]
    assert choice.value == TOKEN
    assert "existing; managed" in choice.label
    assert "planned:" not in choice.label
    assert choice.metadata["live_id"] == SUBNET
    assert choice.metadata["planned"] is False
    assert choice.metadata["private_cidrs"] == ("10.0.0.0/16",)
    data = payload()
    data["infra"]["components"][1]["inputs"]["cluster"]["subnet_id"] = choice.value
    cli._materialize_planned_vpc_binding_tokens(data)
    row = data["infra"]["components"][1]
    assert "subnet_id" not in row["inputs"]["cluster"]
    assert row["bindings"]["inputs.cluster.subnet_id"] == {
        "source_component": "vpc",
        "source_instance": "vpc",
        "source_output": "subnets",
        "key": "private",
        "attribute": "id",
    }
    assert cli._choice_by_value(result.choices, SUBNET) == choice


@pytest.mark.parametrize("state", [VpcIdentitySnapshot(), VpcIdentitySnapshot("available")])
def test_new_subnet_remains_planned_when_inventory_has_no_collision(state):
    result = reconcile(observed=[], identity=state)
    assert not result.diagnostics
    assert result.choices[0].metadata["planned"] is True


@pytest.mark.parametrize(
    "observed,state,complete",
    [
        ([live()], VpcIdentitySnapshot(), True),
        ([], snapshot(), True),
        ([live(parent_id="other-project")], snapshot(), True),
        ([live(network_id="other-network")], snapshot(), True),
        ([live()], VpcIdentitySnapshot("unavailable", diagnostic="State access denied"), True),
        ([live()], snapshot(), False),
    ],
)
def test_unresolved_candidates_are_not_selectable_or_adopted(observed, state, complete):
    result = reconcile(observed=observed, identity=state, complete=complete)
    assert result.diagnostics
    assert all(choice.value != TOKEN for choice in result.choices)
    assert list(result.choices) == observed


def test_new_subnet_in_an_existing_vpc_remains_planned():
    result = reconcile_vpc_choices(
        live=[live()],
        snapshot=snapshot(),
        project_id="project-test",
        network_id=NETWORK,
        configured=planned_subnet_choices_for_vpc(
            instance_id="vpc",
            inputs={"subnets": {"new": {"name": "new", "ipv4_private_cidrs": ["10.4.0.0/16"]}}},
        ),
    )
    assert not result.diagnostics
    assert len(result.choices) == 2


def state_and_manifest(data=None):
    data = data or payload()
    settings = backend_settings_from_config(data)
    manifest = {
        "schema": GENERATED_MANIFEST_SCHEMA,
        "execution": {
            "backend": asdict(settings),
            "targets": [],
            "required_component_outputs": [],
            "status_watchers": [],
            "validations": [],
        },
        "project": {"tenant_id": "tenant-test", "project_id": "project-test"},
        "render": {
            "module_sources": [
                {"component_id": "vpc", "instance_id": "vpc", "module_name": "vpc_renamed_2"}
            ]
        },
        "runtime_config": {"version": "v2"},
    }
    attrs = {"id": SUBNET, "parent_id": "project-test", "network_id": NETWORK}
    state = {
        "version": 4,
        "serial": 5,
        "lineage": "example",
        "resources": [
            {
                "module": "module.vpc_renamed_2",
                "mode": "managed",
                "type": "nebius_vpc_v1_subnet",
                "name": "this",
                "instances": [{"index_key": "private", "attributes": attrs}],
            },
            {
                "module": "module.vpc_renamed_2",
                "mode": "managed",
                "type": "nebius_vpc_v1_network",
                "name": "this",
                "instances": [{"attributes": {"id": NETWORK, "parent_id": "project-test"}}],
            },
        ],
        "outputs": {
            "vpc_subnets": {"value": {"private": dict(attrs)}},
            "vpc_subnet_ids": {"value": {"private": SUBNET}},
            "vpc_network_id": {"value": NETWORK},
            "vpc_network": {"value": {"id": NETWORK, "parent_id": "project-test"}},
        },
    }
    return state, manifest


def test_raw_state_uses_recorded_module_name_and_agreeing_outputs():
    state, manifest = state_and_manifest()
    assert parse_vpc_state(state, manifest=manifest, project_id="project-test") == snapshot()
    state["outputs"] = {}
    result = parse_vpc_state(state, manifest=manifest, project_id="project-test")
    assert result.subnets == snapshot().subnets


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(version=3),
        lambda s: s.update(resources=[]),
        lambda s: s["outputs"]["vpc_subnet_ids"]["value"].update(private="other"),
        lambda s: s["outputs"]["vpc_subnets"]["value"]["private"].update(parent_id="foreign"),
        lambda s: s["resources"][0]["instances"].append(
            copy.deepcopy(s["resources"][0]["instances"][0])
        ),
        lambda s: s["resources"][0]["instances"][0].update(deposed="old"),
        lambda s: s["resources"][0]["instances"][0]["attributes"].update(network_id="other"),
    ],
)
def test_state_conflicts_fail_before_choices(mutation):
    state, manifest = state_and_manifest()
    mutation(state)
    with pytest.raises(ValueError):
        parse_vpc_state(state, manifest=manifest, project_id="project-test")


def test_adopted_network_output_does_not_prove_managed_network_ownership():
    state, manifest = state_and_manifest()
    state["resources"] = state["resources"][:1]
    result = parse_vpc_state(state, manifest=manifest, project_id="project-test")
    assert not result.networks
    assert result.subnets == snapshot().subnets


@pytest.mark.parametrize("field", ["project", "render", "execution"])
def test_malformed_manifest_is_unavailable(tmp_path, monkeypatch, field):
    paths = fixture_paths(tmp_path)
    _state, manifest = state_and_manifest()
    manifest[field] = None
    (paths.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        identity_module, "object_storage_transport", lambda *_: pytest.fail("must not connect")
    )
    assert VpcIdentityReader(paths).read(payload()).status == "unavailable"


@pytest.mark.parametrize("client", [None, [], {"nebius": None}])
def test_malformed_project_scope_is_unavailable(tmp_path, client):
    assert (
        VpcIdentityReader(fixture_paths(tmp_path)).read({"client_info": client}).status
        == "unavailable"
    )


def test_plugin_choices_do_not_prove_complete_live_inventory(monkeypatch):
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_resolve_project_subnets", lambda **_: [])
    monkeypatch.setattr(
        "nebius_cxcli.provider_options._load_option_plugins", lambda _: [lambda **_: [live()]]
    )
    assert lookup.resolve(
        provider="project_subnets", args={}, payload=payload(), field_path="subnet_id"
    )
    assert not lookup.vpc_discovery_complete()


@pytest.mark.parametrize("provider", ["project_networks", "project_subnets"])
def test_filtered_inventory_cannot_prove_configured_resource_is_new(monkeypatch, provider):
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, f"_resolve_{provider}", lambda **_: (live(),))
    monkeypatch.setattr("nebius_cxcli.provider_options._load_option_plugins", lambda _: [])
    observed = lookup.resolve(
        provider=provider, args={"_filter": "^different-id$"}, payload=payload(), field_path="id"
    )
    assert not observed
    result = reconcile(
        observed=observed, identity=VpcIdentitySnapshot(), complete=lookup.vpc_discovery_complete()
    )
    assert result.diagnostics
    assert not result.choices


@pytest.mark.parametrize("row", [None, {}, {"component_id": None}, {"component_id": "vm"}])
def test_malformed_module_inventory_does_not_imply_resource_absence(row):
    state, manifest = state_and_manifest()
    manifest["render"]["module_sources"] = [row]
    with pytest.raises(ValueError, match="module inventory"):
        parse_vpc_state(state, manifest=manifest, project_id="project-test")


def fixture_paths(tmp_path):
    paths = resolve_project_paths(tmp_path / "tenant" / "project" / "config.yaml")
    paths.generated_dir.mkdir(parents=True)
    return paths


def test_state_reader_uses_one_exact_bounded_get_without_recovery(tmp_path, monkeypatch):
    paths = fixture_paths(tmp_path)
    state, manifest = state_and_manifest()
    (paths.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    calls = []

    @contextmanager
    def transport(settings):
        def request(operation, key, **kwargs):
            calls.append((settings, operation, key, kwargs))
            return SimpleNamespace(body=json.dumps(state).encode())

        yield SimpleNamespace(request=request)

    monkeypatch.setattr(identity_module, "object_storage_transport", transport)
    monkeypatch.setattr(
        "nebius_cxcli.generated_manifest.recover_project_bundle",
        lambda *_: pytest.fail("identity inspection must not recover"),
    )
    before = sorted(p.relative_to(paths.project_dir) for p in paths.project_dir.rglob("*"))
    reader = VpcIdentityReader(paths)
    assert reader.read(payload()) == snapshot()
    assert reader.read(payload()) == snapshot()
    assert len(calls) == 1
    assert calls[0][1:3] == ("get_object", manifest["execution"]["backend"]["key"])
    assert "deadline" in calls[0][3]
    assert sorted(p.relative_to(paths.project_dir) for p in paths.project_dir.rglob("*")) == before


@pytest.mark.parametrize(
    "code,deployed,expected",
    [
        ("NoSuchKey", False, "fresh"),
        ("NoSuchKey", True, "unavailable"),
        ("NoSuchBucket", False, "unavailable"),
        ("AccessDenied", False, "unavailable"),
    ],
)
def test_state_absence_is_distinct_from_failed_reads(
    tmp_path, monkeypatch, code, deployed, expected
):
    paths = fixture_paths(tmp_path)
    _state, manifest = state_and_manifest()
    (paths.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    if deployed:
        paths.reports_dir.mkdir()
        (paths.reports_dir / "deploy-report.md").write_text("deployment evidence")

    @contextmanager
    def transport(_settings):
        raise ObjectStorageError(code)
        yield

    monkeypatch.setattr(identity_module, "object_storage_transport", transport)
    assert VpcIdentityReader(paths).read(payload()).status == expected


def test_missing_s3_credentials_is_unavailable_without_auth_setup(tmp_path, monkeypatch):
    paths = fixture_paths(tmp_path)
    _state, manifest = state_and_manifest()
    (paths.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "NEBIUS_S3_ACCESS_KEY_ID",
        "NEBIUS_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    result = VpcIdentityReader(paths).read(payload())
    assert result.status == "unavailable"
    assert "invalid credentials" in result.diagnostic


def test_backend_scope_mismatch_never_sends_credentials(tmp_path, monkeypatch):
    paths = fixture_paths(tmp_path)
    _state, manifest = state_and_manifest()
    manifest["execution"]["backend"]["endpoint"] = "https://untrusted.example"
    (paths.generated_dir / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        identity_module, "object_storage_transport", lambda *_: pytest.fail("must not connect")
    )
    assert VpcIdentityReader(paths).read(payload()).status == "unavailable"


class ScopedLookup(ProviderOptionLookup):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.seen_networks = []

    def vpc_identity(self, payload):
        return self.state

    def resolve(self, *, provider, args, payload, field_path):
        self._last_vpc_complete = True
        if provider == "project_networks":
            return [
                OptionChoice(value=NETWORK, label="network", metadata={"parent_id": "project-test"})
            ]
        self.seen_networks.append(args.get("network_id"))
        return [live()]


def test_deployed_network_binding_is_resolved_before_subnet_discovery():
    data = payload()
    data["infra"]["components"][0]["inputs"]["network"] = {"name": "network"}
    cluster = data["infra"]["components"][1]["inputs"]["cluster"]
    cluster["network_id"] = "planned:vpc@vpc.network_id"
    entry = next(e for e in component_entries("infra") if e.id == "mk8s")
    lookup = ScopedLookup(snapshot())
    choices = cli._resolve_dynamic_field_choices(
        payload=data,
        entry=entry,
        full_path_label="infra.components[1].inputs.cluster.subnet_id",
        provider_lookup=lookup,
    )
    assert lookup.seen_networks == [NETWORK]
    assert [c.value for c in choices] == [TOKEN]


def test_unresolved_candidate_prevents_singleton_materialization():
    data = payload()
    lookup = ScopedLookup(VpcIdentitySnapshot("unavailable", diagnostic="State access denied"))
    entries = component_entries("infra")
    cli._materialize_singleton_provider_defaults(
        payload=data, selected_infra={"cluster"}, infra_entries=entries, provider_lookup=lookup
    )
    assert "subnet_id" not in data["infra"]["components"][1]["inputs"]["cluster"]
    with pytest.raises(RuntimeError, match="Cannot automatically select VPC networking"):
        cli._require_vpc_networking_for_noninteractive(
            payload=data, selected_infra={"cluster"}, infra_entries=entries, provider_lookup=lookup
        )
