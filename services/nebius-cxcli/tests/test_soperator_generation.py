import base64
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli import soperator_release_artifacts
from nebius_cxcli import soperator_release_resolver as resolver
from nebius_cxcli.soperator_generation import use_generation_soperator_releases
from soperator_fixtures import sample_snapshot


@pytest.mark.parametrize("authority", ["release", "campaign"])
@pytest.mark.parametrize("complete", [False, True])
def test_old_snapshot_cutover_preserves_unfinished_local_evidence(
    tmp_path, monkeypatch, authority, complete
):
    from nebius_cxcli import soperator_full_stack_upgrade, soperator_operation
    from nebius_cxcli.soperator_generation import require_current_active_soperator_snapshots
    from nebius_cxcli.soperator_release import soperator_release_snapshot_path

    paths = SimpleNamespace(project_dir=tmp_path, reports_dir=tmp_path / "reports")
    paths.reports_dir.mkdir()
    snapshot_path = soperator_release_snapshot_path(paths.reports_dir, "cluster0")
    snapshot_path.write_text(json.dumps({"schema": "nebius-cxcli.soperator-release-snapshot.v2"}))
    snapshot_path.chmod(0o600)
    before = snapshot_path.read_bytes()
    receipt = SimpleNamespace(status="complete" if complete else "active")
    if authority == "release":
        soperator_operation._release_intent_path(paths, "cluster0").touch()
        monkeypatch.setattr(soperator_operation, "_load_release_intent", lambda _: receipt)
    else:
        monkeypatch.setattr(
            soperator_full_stack_upgrade, "load_campaign_receipt", lambda _: receipt
        )
    config = {
        "infra": {"components": []},
        "apps": {
            "charts": [
                {"id": "soperator", "instance_id": "cluster0", "enabled": True},
            ]
        },
    }
    if complete:
        require_current_active_soperator_snapshots(config, paths)
    else:
        with pytest.raises(ValueError, match="previous cxcli binary"):
            require_current_active_soperator_snapshots(config, paths)
    assert snapshot_path.read_bytes() == before


def generation(*snapshots):
    rows, files = [], {}
    for snapshot in snapshots:
        target = snapshot.target_ref
        rows.append(
            {
                "id": "soperator",
                "instance_id": target,
                "enabled": True,
                "version": snapshot.release,
                "repo": snapshot.chart_oci_url("umbrella"),
            }
        )
        files[f"reports/soperator-release-snapshot-{target}.json"] = base64.b64encode(
            json.dumps(snapshot.canonical_payload()).encode()
        ).decode()
    return SimpleNamespace(
        manifest={"runtime_config": {"infra": {"components": []}, "apps": {"charts": rows}}},
        files=files,
    )


@pytest.fixture
def verified(monkeypatch):
    observed = []

    def hydrate(snapshot):
        observed.append(snapshot)
        return SimpleNamespace(snapshot=snapshot, source=object())

    monkeypatch.setattr(resolver, "frozen_soperator_release_from_snapshot", hydrate)
    monkeypatch.setattr(
        resolver,
        "freeze_soperator_release",
        lambda *a, **k: pytest.fail("must not resolve chart tags"),
    )
    monkeypatch.setattr(
        soperator_release_artifacts, "verify_soperator_release_artifacts", lambda *a, **k: None
    )
    return observed


def test_generation_binds_all_releases_and_restores_context_after_cancellation(verified):
    first, second, outer = (
        sample_snapshot(release=version, target_ref=f"cluster{index}")
        for index, version in enumerate(("4.1.7", "4.1.8", "4.1.6"))
    )
    outer_frozen = SimpleNamespace(snapshot=outer)
    with resolver.use_frozen_soperator_release(outer_frozen):
        with (
            pytest.raises(KeyboardInterrupt),
            use_generation_soperator_releases(generation(first, second), emit=lambda _: None),
        ):
            assert (
                resolver.current_frozen_soperator_release(
                    first.release, target_ref=first.target_ref
                ).snapshot
                == first
            )
            assert (
                resolver.current_frozen_soperator_release(
                    second.release, target_ref=second.target_ref
                ).snapshot
                == second
            )
            assert (
                resolver.current_frozen_soperator_release(
                    outer.release, target_ref=outer.target_ref
                )
                is outer_frozen
            )
            raise KeyboardInterrupt
        assert (
            resolver.current_frozen_soperator_release(first.release, target_ref=first.target_ref)
            is None
        )
        assert (
            resolver.current_frozen_soperator_release(second.release, target_ref=second.target_ref)
            is None
        )
        assert (
            resolver.current_frozen_soperator_release(outer.release, target_ref=outer.target_ref)
            is outer_frozen
        )
    assert (
        resolver.current_frozen_soperator_release(outer.release, target_ref=outer.target_ref)
        is None
    )
    assert verified == [first, second]


def test_same_release_for_two_targets_has_separate_snapshots(verified):
    first = sample_snapshot(target_ref="cluster0")
    second = sample_snapshot(target_ref="cluster1")
    with use_generation_soperator_releases(
        generation(first, second), emit=lambda _: None
    ) as snapshots:
        assert snapshots == {"cluster0": first, "cluster1": second}
        assert (
            resolver.current_frozen_soperator_release(first.release, target_ref="cluster0").snapshot
            == first
        )
        assert (
            resolver.current_frozen_soperator_release(
                second.release, target_ref="cluster1"
            ).snapshot
            == second
        )
    assert verified == [first, second]


@pytest.mark.parametrize(
    "corruption", ["missing", "base64", "digest", "unsealed", "release", "repo", "target"]
)
def test_invalid_accepted_snapshot_never_falls_back_to_discovery(verified, corruption):
    snapshot = sample_snapshot(target_ref="cluster0")
    data = generation(snapshot)
    key = "reports/soperator-release-snapshot-cluster0.json"
    if corruption == "missing":
        data.files.clear()
    elif corruption == "base64":
        data.files[key] = "not base64!"
    elif corruption in {"digest", "unsealed"}:
        payload = snapshot.canonical_payload()
        payload["snapshot_sha256"] = "" if corruption == "unsealed" else "sha256:" + "f" * 64
        data.files[key] = base64.b64encode(json.dumps(payload).encode()).decode()
    elif corruption in {"release", "repo"}:
        data.manifest["runtime_config"]["apps"]["charts"][0][
            "version" if corruption == "release" else "repo"
        ] = "different"
    else:
        data.manifest["runtime_config"]["apps"]["charts"][0]["instance_id"] = "different-target"
        data.files["reports/soperator-release-snapshot-different-target.json"] = data.files[key]
    with pytest.raises(ValueError), use_generation_soperator_releases(data, emit=lambda _: None):
        pytest.fail("invalid evidence must not admit reconfiguration")
    assert not verified


def test_verification_failure_restores_prior_release_context(verified, monkeypatch):
    first, second = (
        sample_snapshot(release=v, target_ref=f"cluster{index}")
        for index, v in enumerate(("4.1.7", "4.1.8"))
    )

    def verify(snapshot, _source):
        if snapshot.release == second.release:
            raise ValueError("official OCI chart differs from release source")

    monkeypatch.setattr(soperator_release_artifacts, "verify_soperator_release_artifacts", verify)
    with (
        pytest.raises(ValueError),
        use_generation_soperator_releases(generation(first, second), emit=lambda _: None),
    ):
        pytest.fail("must fail before save")
    assert (
        resolver.current_frozen_soperator_release(first.release, target_ref=first.target_ref)
        is None
    )
    assert (
        resolver.current_frozen_soperator_release(second.release, target_ref=second.target_ref)
        is None
    )
