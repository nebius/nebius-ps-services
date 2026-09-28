from __future__ import annotations

import copy
from types import SimpleNamespace

import click
import pytest
import yaml

from nebius_cxcli import grafana_install, observability_installation, soperator_release_resolver
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_model import to_dynamic_payload
from nebius_cxcli.observability_routing import (
    app_row,
    resolve_settings,
    save_settings,
    target_settings,
)
from test_observability_routing import payload
from test_render import _mk8s_inputs, _starter_payload


@pytest.fixture
def mixed_config(tmp_path):
    data = _starter_payload(selected_infra={"mk8s"}, selected_apps={"soperator"})
    native = next(row for row in data["infra"]["components"] if row["id"] == "mk8s")
    native["inputs"] = _mk8s_inputs()
    native["inputs"]["node_groups"] = {
        role: {"node_count": 1, "gpu": False, "platform": "cpu-d3", "preset": "4vcpu-16gb"}
        for role in ("accounting", "controller", "login", "system", "worker-cpu")
    }
    plain = copy.deepcopy(native)
    plain.update(instance_id="plain", inputs=_mk8s_inputs(cluster_name="plain"))
    data["infra"]["components"].append(plain)
    targets = data.setdefault("deploy", {}).setdefault("targets", [])
    target = next((row for row in targets if row["instance_id"] == "mk8s"), None)
    if target is None:
        target = {"instance_id": "mk8s"}
        targets.append(target)
    target.setdefault("observability", {})["routing"] = {"metrics": {"storage": "local"}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(to_dynamic_payload(data)))
    return path


@pytest.fixture
def forbid_native_resolution(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Plain target setup must not resolve another target's Soperator release")

    monkeypatch.setattr(soperator_release_resolver, "current_frozen_soperator_release", forbidden)
    monkeypatch.setattr(
        soperator_release_resolver, "resolve_soperator_observability_defaults", forbidden
    )


@pytest.mark.parametrize("accept", [False, True])
def test_mixed_project_install_uses_real_scoped_loader(
    mixed_config, monkeypatch, forbid_native_resolution, capsys, accept
):
    before = mixed_config.read_bytes()
    calls = []
    confirmations = []

    def confirm(message, **_kwargs):
        confirmations.append(message)
        return SimpleNamespace(
            ask=lambda: accept if message.startswith("Save these settings") else False
        )

    monkeypatch.setattr(grafana_install.questionary, "confirm", confirm)
    monkeypatch.setattr(
        grafana_install.questionary,
        "select",
        lambda _message, **kwargs: SimpleNamespace(ask=lambda: kwargs["default"]),
    )

    def install(path, target, candidate, **kwargs):
        from nebius_cxcli.observability_routing import _NATIVE_DEFAULTS

        assert _NATIVE_DEFAULTS.get() is not None
        calls.append((path, target, candidate, kwargs["expected_bytes"]))

    monkeypatch.setattr(observability_installation, "install_observability", install)

    def run():
        grafana_install.install(
            mixed_config, "plain", interactive=True, overrides={}, datasources=(), emit=click.echo
        )

    if accept:
        run()
        assert len(calls) == 1
        path, target, candidate, expected = calls[0]
        assert (path, target, expected) == (mixed_config, "plain", before)
        assert target_settings(candidate, "mk8s") == {"metrics": {"storage": "local"}}
        assert app_row(candidate, "plain", "grafana")
        assert app_row(candidate, "mk8s", "grafana") is None
    else:
        with pytest.raises(click.Abort):
            run()
        assert not calls
    assert mixed_config.read_bytes() == before
    assert confirmations[-1].startswith("Save these settings")
    output = capsys.readouterr().out
    assert "Target plain: managed MK8s" in output
    assert "Soperator" not in output
    assert "replaces generated artifacts" in output
    assert "deploys all pending project changes" in output


@pytest.mark.parametrize("scope", [frozenset({"plain"}), frozenset()])
def test_scoped_load_preserves_unrelated_routing(mixed_config, forbid_native_resolution, scope):
    loaded = to_dynamic_payload(load_config(mixed_config, observability_target_refs=scope))
    assert target_settings(loaded, "mk8s") == {"metrics": {"storage": "local"}}
    assert app_row(loaded, "mk8s", "grafana") is None


@pytest.mark.parametrize(
    "routing",
    [
        {"metrics": {"storag": "local"}},
        {"metrics": "local"},
        {"metrics": {"remote": {"url": 1}}},
        {"datasources": "invalid"},
        {"signals": ["invalid"]},
    ],
)
def test_scoped_load_validates_unrelated_routing(mixed_config, forbid_native_resolution, routing):
    data = yaml.safe_load(mixed_config.read_text())
    next(row for row in data["deploy"]["targets"] if row["instance_id"] == "mk8s")["observability"][
        "routing"
    ] = routing
    mixed_config.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        load_config(mixed_config, observability_target_refs=frozenset({"plain"}))


def test_invalid_setup_target_fails_before_source_lookup(mixed_config, forbid_native_resolution):
    with pytest.raises(ValueError, match="missing.*not an enabled managed MK8s target"):
        load_config(mixed_config, observability_target_refs=frozenset({"missing"}))


def test_unscoped_loader_still_resolves_project_native_sources(mixed_config, monkeypatch):
    def stop(_release, **kwargs):
        raise RuntimeError("project-wide native resolution")

    monkeypatch.setattr(soperator_release_resolver, "current_frozen_soperator_release", stop)
    with pytest.raises(RuntimeError, match="project-wide native resolution"):
        load_config(mixed_config)


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("fail_render", [False, True])
@pytest.mark.parametrize("available_lookups", [2, 4])
@pytest.mark.parametrize("entrypoint", ["public", "unchanged", "owner"])
def test_install_reuses_native_source_through_real_config_loading(
    mixed_config, tmp_path, monkeypatch, interactive, fail_render, available_lookups, entrypoint
):
    from contextlib import nullcontext

    from nebius_cxcli import cli, deployment_cli
    from nebius_cxcli.config_loader import normalize_runtime_config_payload
    from nebius_cxcli.observability import materialize_observability_app_values
    from nebius_cxcli.observability_routing import _NATIVE_DEFAULTS, native_defaults_scope
    from nebius_cxcli.soperator_release_identity import SoperatorReleaseIdentityLedger
    from test_soperator_configuration_render import frozen_charts_for

    resolver = soperator_release_resolver
    snapshot, receipt, _ = frozen_charts_for(tmp_path / "source", "4.1.8")
    data = yaml.safe_load(mixed_config.read_text())
    next(row for row in data["apps"]["charts"] if row["id"] == "soperator")["version"] = "4.1.8"
    mixed_config.write_text(yaml.safe_dump(data))
    metadata = resolver.SoperatorReleaseMetadata(
        selector=snapshot.release,
        release=snapshot.release,
        repository=snapshot.repository,
        tag=snapshot.tag,
        commit=snapshot.commit,
        tree=snapshot.tree,
        archive_url=snapshot.archive_url,
        archive_root=snapshot.archive_root,
        published_at="",
        tree_entries=(),
    )
    lookups = []

    def resolve(release):
        lookups.append(release)
        if len(lookups) > available_lookups:
            raise RuntimeError("could not resolve Soperator release tree: timed out")
        return metadata

    monkeypatch.setattr(resolver, "resolve_soperator_release", resolve)
    monkeypatch.setattr(resolver, "acquire_soperator_release_source", lambda _: receipt)
    monkeypatch.setattr(
        resolver, "classify_soperator_release_capabilities", lambda _: ("upstream-flux-v1", "")
    )
    monkeypatch.setattr(
        resolver,
        "SoperatorReleaseIdentityLedger",
        lambda: SoperatorReleaseIdentityLedger(tmp_path / "identities"),
    )
    monkeypatch.setattr(deployment_cli, "deployment_execution", lambda **_: nullcontext())
    monkeypatch.setattr(
        grafana_install.questionary,
        "confirm",
        lambda prompt, **_: SimpleNamespace(ask=lambda: prompt.startswith("Save these settings")),
    )
    overrides = {
        "metrics_storage": "local",
        "logs_storage": "local",
        "traces_storage": "local",
        "pushgateway": True,
    }
    if entrypoint != "public":
        with native_defaults_scope():
            prepared = grafana_install.configure(
                to_dynamic_payload(load_config(mixed_config)), "mk8s", overrides=overrides
            )
            normalize_runtime_config_payload(prepared, base_dir=mixed_config.parent)
        if entrypoint == "unchanged":
            mixed_config.write_text(yaml.safe_dump(prepared))
        lookups.clear()
    before = mixed_config.read_bytes()
    stages = []

    def render(*, config_path, force):
        assert force
        # Exercise the failing pre-render config boundary and subsequent
        # materialization; package admission and external effects are separate.
        loaded = load_config(config_path)
        materialize_observability_app_values(loaded)
        stages.append("render")
        if fail_render:
            raise RuntimeError("required artifact rejected")

    monkeypatch.setattr(cli, "render_command", render)
    monkeypatch.setattr(cli, "deploy_command", lambda **_: stages.append("deploy"))

    def install():
        if entrypoint == "owner":
            observability_installation.install_observability(
                mixed_config,
                "mk8s",
                prepared,
                expected_bytes=mixed_config.read_bytes(),
                emit=lambda _: None,
            )
            return
        grafana_install.install(
            mixed_config,
            "mk8s",
            interactive=interactive,
            overrides=overrides,
            datasources=(),
            emit=lambda _: None,
        )

    if fail_render:
        with pytest.raises(RuntimeError, match="required artifact rejected"):
            install()
    else:
        install()
    assert lookups == ["4.1.8", "4.1.8"]
    assert stages == (["render"] if fail_render else ["render", "deploy"])
    assert _NATIVE_DEFAULTS.get() is None
    saved = yaml.safe_load(mixed_config.read_text())
    assert target_settings(saved, "mk8s")["pushgateway"] is True
    if entrypoint == "unchanged":
        assert mixed_config.read_bytes() == before
    # Another command must reverify; an earlier success is no offline authority.
    available_lookups = len(lookups)
    with pytest.raises(RuntimeError, match="release tree: timed out"):
        install()
    assert _NATIVE_DEFAULTS.get() is None


@pytest.mark.parametrize("disabled_native", [False, True])
def test_shared_configure_does_not_initialize_other_targets(
    forbid_native_resolution, disabled_native
):
    source = payload()
    source["infra"]["components"].append(
        {"id": "mk8s", "instance_id": "peer", "enabled": True, "inputs": {}}
    )
    source["apps"]["charts"].append(
        {
            "id": "victoria-logs-single",
            "instance_id": "peer",
            "target_ref": "peer",
            "enabled": True,
            "namespace": "peer-logs",
            "release-name": "peer-logs",
            "values": {},
        }
    )
    if disabled_native:
        source["apps"]["charts"].append(
            {"id": "soperator", "instance_id": "cluster", "enabled": False, "version": "4.1.8"}
        )
    before = copy.deepcopy(source)
    result = grafana_install.configure(source, "cluster")
    assert source == before
    assert target_settings(result, "peer") == {}
    assert app_row(result, "peer", "victoria-logs-single") == before["apps"]["charts"][0]
    assert app_row(result, "peer", "grafana") is None
    assert app_row(result, "cluster", "grafana")


def test_selected_target_keeps_peer_grafana_and_database_values(forbid_native_resolution):
    source = payload()
    source["infra"]["components"].append(
        {"id": "mk8s", "instance_id": "peer", "enabled": True, "inputs": {}}
    )
    save_settings(source, "peer", resolve_settings(source, "peer"))
    source["apps"]["charts"] = [
        {
            "id": component,
            "instance_id": "peer",
            "target_ref": "peer",
            "enabled": True,
            "namespace": "peer-observability",
            "release-name": "peer-" + component,
            "values": {"authored": "retain"},
        }
        for component in ("grafana", "postgresql")
    ]
    before = copy.deepcopy(source)
    candidate = grafana_install.configure(source, "cluster")
    assert target_settings(candidate, "peer") == target_settings(before, "peer")
    assert [
        row for row in candidate["apps"]["charts"] if row.get("target_ref") == "peer"
    ] == before["apps"]["charts"]
    assert app_row(candidate, "cluster", "grafana")
    assert app_row(candidate, "cluster", "postgresql")


@pytest.mark.parametrize("interactive", [False, True])
def test_native_setup_resolves_only_selected_release(tmp_path, monkeypatch, capsys, interactive):
    from test_soperator_configuration_render import frozen_charts_for

    snapshot, receipt, _ = frozen_charts_for(tmp_path, "4.1.8")
    frozen = SimpleNamespace(
        snapshot=snapshot,
        source=receipt,
        source_context=SimpleNamespace(
            source=receipt, umbrella=snapshot.umbrella, charts=snapshot.charts
        ),
    )
    releases = []

    def lookup(release, **kwargs):
        assert release == "4.1.8", "Selected setup must not inspect the peer release"
        releases.append(release)
        return frozen

    monkeypatch.setattr(soperator_release_resolver, "current_frozen_soperator_release", lookup)
    source = payload()
    source["infra"]["components"].append(
        {"id": "mk8s", "instance_id": "peer", "enabled": True, "inputs": {}}
    )
    source["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": target,
            "target_ref": target,
            "enabled": True,
            "version": release,
            "namespace": "flux-system",
            "release-name": target + "-soperator",
            "values": {},
        }
        for target, release in (("cluster", "4.1.8"), ("peer", "4.1.9"))
    ]
    save_settings(source, "peer", {"metrics": {"storage": "local"}})
    monkeypatch.setattr(
        grafana_install.questionary, "confirm", lambda *a, **k: SimpleNamespace(ask=lambda: False)
    )
    candidate = grafana_install.configure(
        source,
        "cluster",
        interactive=interactive,
        overrides={
            "metrics_storage": "local",
            "logs_storage": "local",
            "traces_storage": "local",
            "pushgateway": False,
        },
    )
    assert releases and set(releases) == {"4.1.8"}
    assert target_settings(candidate, "peer") == {"metrics": {"storage": "local"}}
    output = capsys.readouterr().out
    assert output.count("Target cluster is configured with Soperator 4.1.8") == int(interactive)
    assert "4.1.9" not in output
