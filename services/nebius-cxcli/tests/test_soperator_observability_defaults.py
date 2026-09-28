from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import observability_routing as routing
from nebius_cxcli import soperator_release_resolver as resolver
from nebius_cxcli.soperator_release_identity import SoperatorReleaseIdentityLedger


@pytest.fixture
def verified_source(tmp_path, monkeypatch):
    metadata = resolver.SoperatorReleaseMetadata(
        selector="4.1.8",
        release="4.1.8",
        repository="nebius/soperator",
        tag="v4.1.8",
        commit="a" * 40,
        tree="b" * 40,
        archive_url="https://example.invalid/source.tar.gz",
        archive_root="source",
        published_at="",
        tree_entries=(),
    )
    source = resolver.SoperatorSourceReceipt(
        schema="fixture",
        release=metadata.release,
        commit=metadata.commit,
        tree=metadata.tree,
        archive_sha256="sha256:" + "c" * 64,
        manifest_sha256="sha256:" + "d" * 64,
        source_dir=str(tmp_path / "source"),
    )
    values = tmp_path / "source/helm/soperator-fluxcd/values.yaml"
    values.parent.mkdir(parents=True)
    values.write_text(yaml.safe_dump({"observability": {"enabled": True}}))
    ledger = SoperatorReleaseIdentityLedger(tmp_path / "identities")
    monkeypatch.setattr(resolver, "SoperatorReleaseIdentityLedger", lambda *_: ledger)
    monkeypatch.setattr(resolver, "resolve_soperator_release", lambda _: metadata)
    monkeypatch.setattr(resolver, "acquire_soperator_release_source", lambda _: source)
    monkeypatch.setattr(
        resolver, "classify_soperator_release_capabilities", lambda _: ("upstream-flux-v1", "")
    )
    monkeypatch.setattr(
        resolver, "_run", lambda *a, **k: pytest.fail("Source-only defaults must not run Helm")
    )
    return SimpleNamespace(metadata=metadata, source=source, values=values, ledger=ledger)


def test_source_defaults_preserve_identity_and_report_progress(verified_source, monkeypatch):
    events = []
    resolutions = []

    def resolve(release):
        assert events, "Progress must precede network resolution"
        resolutions.append(release)
        return verified_source.metadata

    monkeypatch.setattr(resolver, "resolve_soperator_release", resolve)
    result = resolver.resolve_soperator_observability_defaults("4.1.8", emit=events.append)
    assert resolutions == ["4.1.8", "4.1.8"]
    assert result.metadata == verified_source.metadata
    assert result.source == verified_source.source
    assert result.values == {"enabled": True}
    assert events[-1] == "Verified Soperator datasource defaults are ready."
    assert len(list(verified_source.ledger.root.glob("*.json"))) == 1


@pytest.mark.parametrize(
    "failure,match",
    [
        ("receipt", "receipt does not match"),
        ("layout", "upstream Flux contract"),
        ("defaults", "defaults are incomplete"),
        ("tree", "source tree mismatch"),
        ("moved_tag", "tag moved"),
    ],
)
def test_source_defaults_fail_closed(verified_source, monkeypatch, failure, match):
    if failure == "receipt":
        monkeypatch.setattr(
            resolver,
            "acquire_soperator_release_source",
            lambda _: replace(verified_source.source, commit="e" * 40),
        )
    elif failure == "layout":
        monkeypatch.setattr(
            resolver, "classify_soperator_release_capabilities", lambda _: ("unknown", "")
        )
    elif failure == "defaults":
        verified_source.values.write_text("observability: {}\n")
    elif failure == "tree":

        def reject(_):
            raise ValueError("source tree mismatch")

        monkeypatch.setattr(resolver, "acquire_soperator_release_source", reject)
    else:
        identities = iter(
            [verified_source.metadata, replace(verified_source.metadata, commit="e" * 40)]
        )
        monkeypatch.setattr(resolver, "resolve_soperator_release", lambda _: next(identities))
    with pytest.raises((ValueError, RuntimeError), match=match):
        resolver.resolve_soperator_observability_defaults("4.1.8")
    assert not list(verified_source.ledger.root.glob("*.json"))


def test_source_defaults_reject_previously_moved_tag(verified_source, monkeypatch):
    with verified_source.ledger.locked(verified_source.metadata) as identity:
        verified_source.ledger.record(identity)
    monkeypatch.setattr(
        resolver,
        "resolve_soperator_release",
        lambda _: replace(verified_source.metadata, commit="e" * 40),
    )
    monkeypatch.setattr(
        resolver,
        "acquire_soperator_release_source",
        lambda _: pytest.fail("Moved identity must fail before source acquisition"),
    )
    with pytest.raises(RuntimeError, match="refusing the moved tag"):
        resolver.resolve_soperator_observability_defaults("4.1.8")


def test_cached_preview_does_not_admit_a_moved_release_tag(verified_source, monkeypatch):
    from nebius_cxcli.soperator_release import SoperatorArtifactRequest

    with routing.native_defaults_scope():
        scope = routing._NATIVE_DEFAULTS.get()
        assert scope is not None
        scope.releases["4.1.8"] = resolver.resolve_soperator_observability_defaults("4.1.8")
        monkeypatch.setattr(
            resolver,
            "resolve_soperator_release",
            lambda *a, **k: replace(verified_source.metadata, commit="e" * 40),
        )
        monkeypatch.setattr(
            resolver,
            "acquire_soperator_release_source",
            lambda *a, **k: pytest.fail("Moved identity must fail before source acquisition"),
        )
        with pytest.raises(RuntimeError, match="refusing the moved tag"):
            resolver.freeze_soperator_release(
                "4.1.8", request=SoperatorArtifactRequest.deployment("cluster", {})
            )


@pytest.mark.parametrize("abort", [False, True])
def test_defaults_scope_isolated_by_operation_release_and_target(monkeypatch, abort):
    from test_observability_routing import payload

    source = payload()
    source["apps"]["charts"].append(
        {"id": "soperator", "instance_id": "cluster", "enabled": True, "version": "4.1.8"}
    )
    row = source["apps"]["charts"][-1]
    defaults = {
        "enabled": True,
        "vmStack": {
            "enabled": True,
            "releaseName": "vm-stack",
            "namespace": "monitoring",
            "values": {
                "vmagent": {
                    "spec": {
                        "remoteWrite": [
                            {
                                "url": "http://vmsingle-vm-stack-victoria-metrics-k8s-stack.monitoring.svc:8429/api/v1/write"
                            }
                        ]
                    }
                }
            },
        },
        "vmLogs": {"enabled": True, "releaseName": "vm-logs", "namespace": "monitoring"},
        "opentelemetry": {"namespace": "monitoring"},
    }
    original = copy.deepcopy(defaults)
    calls = []

    def resolve(release, **_kwargs):
        calls.append(release)
        return SimpleNamespace(values=defaults)

    monkeypatch.setattr(resolver, "current_frozen_soperator_release", lambda *a, **k: None)
    monkeypatch.setattr(resolver, "resolve_soperator_observability_defaults", resolve)
    try:
        with routing.native_defaults_scope():
            first = {}
            routing.bind_native_backends(source, "cluster", first)
            row["values"] = {"observability": {"vmLogs": {"namespace": "custom-logs"}}}
            second = {}
            with routing.native_defaults_scope():
                routing.bind_native_backends(source, "cluster", second)
            assert first["native_backends"]["vmLogs"]["namespace"] == "monitoring"
            assert second["native_backends"]["vmLogs"]["namespace"] == "custom-logs"
            assert calls == ["4.1.8"]
            row["version"] = "4.1.9"
            routing.bind_native_backends(source, "cluster", {})
            assert calls == ["4.1.8", "4.1.9"]
            if abort:
                raise KeyboardInterrupt
    except KeyboardInterrupt:
        assert abort
    assert defaults == original
    with routing.native_defaults_scope():
        routing.bind_native_backends(source, "cluster", {})
    assert calls == ["4.1.8", "4.1.9", "4.1.9"]


def test_frozen_generation_overrides_cached_preview_defaults(tmp_path, monkeypatch):
    from nebius_cxcli.soperator_values import read_observability_defaults
    from test_observability_routing import payload
    from test_soperator_configuration_render import frozen_charts_for

    snapshot, receipt, root = frozen_charts_for(tmp_path, "4.1.8")
    defaults = read_observability_defaults(root / "helm/soperator-fluxcd/values.yaml")
    preview = copy.deepcopy(defaults)
    preview["vmLogs"]["namespace"] = "preview-logs"
    frozen = SimpleNamespace(
        snapshot=snapshot,
        source=receipt,
        source_context=SimpleNamespace(
            source=receipt, umbrella=snapshot.umbrella, charts=snapshot.charts
        ),
    )
    source = payload()
    source["apps"]["charts"].append(
        {"id": "soperator", "instance_id": "cluster", "enabled": True, "version": "4.1.8"}
    )
    monkeypatch.setattr(resolver, "current_frozen_soperator_release", lambda *a, **k: None)
    monkeypatch.setattr(
        resolver,
        "resolve_soperator_observability_defaults",
        lambda *a, **k: SimpleNamespace(values=preview),
    )
    with routing.native_defaults_scope():
        settings = {}
        routing.bind_native_backends(source, "cluster", settings)
        assert settings["native_backends"]["vmLogs"]["namespace"] == "preview-logs"
        monkeypatch.setattr(resolver, "current_frozen_soperator_release", lambda *a, **k: frozen)
        routing.bind_native_backends(source, "cluster", settings)
        assert settings["native_backends"]["vmLogs"]["namespace"] == defaults["vmLogs"]["namespace"]
