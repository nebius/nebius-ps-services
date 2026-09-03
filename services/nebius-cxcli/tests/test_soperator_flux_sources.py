from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.paths import ProjectPaths

_DIGEST = "sha256:" + "1" * 64


def _paths(tmp_path: Path) -> ProjectPaths:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True)
    (flux_dir / "soperator-release-graph.yaml").write_text(
        yaml.safe_dump_all(
            [
                {
                    "apiVersion": "source.toolkit.fluxcd.io/v1",
                    "kind": "HelmRepository",
                    "metadata": {"name": "official", "namespace": "flux-system"},
                    "spec": {"url": "https://charts.example.invalid"},
                },
                {
                    "apiVersion": "source.toolkit.fluxcd.io/v1",
                    "kind": "HelmChart",
                    "metadata": {
                        "name": "locked-chart",
                        "namespace": "flux-system",
                        "annotations": {"soperator.nebius.ai/package-sha256": _DIGEST},
                    },
                    "spec": {
                        "chart": "example",
                        "version": "1.2.3",
                        "sourceRef": {"kind": "HelmRepository", "name": "official"},
                        "suspend": True,
                    },
                },
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=flux_dir,
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def _source_payload(*, token: str, suspended: bool, digest: str = _DIGEST) -> str:
    generation = 3 if suspended else 2
    return json.dumps(
        {
            "metadata": {
                "uid": "source-uid",
                "generation": generation,
                "resourceVersion": str(generation),
            },
            "spec": {"suspend": suspended},
            "status": {
                "observedGeneration": 2,
                "lastHandledReconcileAt": token,
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "True",
                        "lastTransitionTime": "2026-08-24T00:00:00Z",
                    },
                    {
                        "type": "ArtifactInStorage",
                        "status": "True",
                        "lastTransitionTime": "2026-08-24T00:00:01Z",
                    },
                ],
                "artifact": {"digest": digest},
            },
        }
    )


def _oci_paths(tmp_path: Path) -> ProjectPaths:
    paths = _paths(tmp_path)
    (paths.flux_dir / "soperator-release-graph.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "OCIRepository",
                "metadata": {"name": "locked-oci", "namespace": "flux-system"},
                "spec": {
                    "url": "oci://official.example.invalid/chart",
                    "ref": {"digest": _DIGEST},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return paths


def _suspended_helmrelease(
    *,
    observed_generation: int,
    include_reconciling: bool = False,
    reconciling_observed_generation: int | None = None,
) -> dict[str, object]:
    conditions: list[dict[str, object]] = [{"type": "Ready", "status": "True"}]
    if include_reconciling:
        reconciling: dict[str, object] = {"type": "Reconciling", "status": "True"}
        if reconciling_observed_generation is not None:
            reconciling["observedGeneration"] = reconciling_observed_generation
        conditions.append(reconciling)
    return {
        "metadata": {"generation": 3},
        "spec": {"suspend": True},
        "status": {
            "observedGeneration": observed_generation,
            "conditions": conditions,
        },
    }


def test_helmrelease_quiescence_requires_suspension_generation_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _suspended_helmrelease(observed_generation=2),
    )

    with pytest.raises(RuntimeError, match="did not quiesce"):
        flux_ops._wait_for_helmrelease_quiescence(
            {("flux-system", "release")},
            cache_dir=tmp_path / "cache",
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
        )


def test_helmrelease_quiescence_accepts_acknowledged_suspension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _suspended_helmrelease(observed_generation=3),
    )

    flux_ops._wait_for_helmrelease_quiescence(
        {("flux-system", "release")},
        cache_dir=tmp_path / "cache",
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0.01,
    )


def test_helmrelease_quiescence_ignores_stale_reconciling_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _suspended_helmrelease(
            observed_generation=3,
            include_reconciling=True,
            reconciling_observed_generation=2,
        ),
    )

    flux_ops._wait_for_helmrelease_quiescence(
        {("flux-system", "release")},
        cache_dir=tmp_path / "cache",
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0.01,
    )


@pytest.mark.parametrize("condition_generation", [None, 3, 4])
def test_helmrelease_quiescence_rejects_active_reconciling_condition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    condition_generation: int | None,
) -> None:
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _suspended_helmrelease(
            observed_generation=3,
            include_reconciling=True,
            reconciling_observed_generation=condition_generation,
        ),
    )

    with pytest.raises(RuntimeError, match="did not quiesce"):
        flux_ops._wait_for_helmrelease_quiescence(
            {("flux-system", "release")},
            cache_dir=tmp_path / "cache",
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
        )


def test_digest_pinned_oci_source_is_reread_as_oci_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = ""
    get_resources: list[str] = []

    def _fake_run(command: list[str], **kwargs):
        nonlocal token
        if "apply" in command:
            document = yaml.safe_load(str(kwargs.get("input") or ""))
            if isinstance(document, dict) and document.get("kind") == "OCIRepository":
                token = document["metadata"]["annotations"]["reconcile.fluxcd.io/requestedAt"]
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")
        if "get" in command:
            resource = command[command.index("get") + 1]
            get_resources.append(resource)
            return SimpleNamespace(
                returncode=0,
                stdout=_source_payload(token=token, suspended=False),
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    receipts = flux_ops.prepare_soperator_release_sources(
        _oci_paths(tmp_path),
        cache_dir=tmp_path / "cache",
        poll_interval_seconds=0.01,
    )

    assert receipts[0].kind == "OCIRepository"
    assert get_resources == ["ocirepository", "ocirepository"]


def test_mutable_source_requires_fresh_evidence_then_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = ""
    suspended = False
    patches = 0

    def _fake_run(command: list[str], **kwargs):
        nonlocal token, suspended, patches
        if "apply" in command:
            document = yaml.safe_load(str(kwargs.get("input") or ""))
            if isinstance(document, dict) and document.get("kind") == "HelmChart":
                token = document["metadata"]["annotations"]["reconcile.fluxcd.io/requestedAt"]
                suspended = False
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")
        if "patch" in command:
            patches += 1
            suspended = True
            return SimpleNamespace(returncode=0, stdout="patched", stderr="")
        if "get" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=_source_payload(token=token, suspended=suspended),
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    receipts = flux_ops.prepare_soperator_release_sources(
        _paths(tmp_path),
        cache_dir=tmp_path / "cache",
        poll_interval_seconds=0.01,
    )

    assert len(receipts) == 1
    assert receipts[0].request_token.startswith("cxcli-")
    assert receipts[0].expected_digest == _DIGEST
    assert receipts[0].observed_digest == _DIGEST
    assert receipts[0].suspended is True
    assert patches == 1


def test_digest_mismatch_fails_closed_and_still_resuspends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = ""
    suspended = False
    patches = 0

    def _fake_run(command: list[str], **kwargs):
        nonlocal token, suspended, patches
        if "apply" in command:
            document = yaml.safe_load(str(kwargs.get("input") or ""))
            if isinstance(document, dict) and document.get("kind") == "HelmChart":
                token = document["metadata"]["annotations"]["reconcile.fluxcd.io/requestedAt"]
                suspended = False
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")
        if "patch" in command:
            patches += 1
            suspended = True
            return SimpleNamespace(returncode=0, stdout="patched", stderr="")
        if "get" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=_source_payload(
                    token=token,
                    suspended=suspended,
                    digest="sha256:" + "2" * 64,
                ),
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="fresh artifact evidence"):
        flux_ops.prepare_soperator_release_sources(
            _paths(tmp_path),
            cache_dir=tmp_path / "cache",
            timeout_seconds=0,
            poll_interval_seconds=0.01,
        )

    assert patches == 1
    assert suspended is True


def _staged_contract() -> dict[str, object]:
    return {
        "releases": [
            {
                "releaseName": "cxcli-soperator-fluxcd-ns",
                "upstreamReleaseName": "soperator-fluxcd-ns",
                "namespace": "flux-system",
                "sourceKind": "OCIRepository",
                "sourceName": "source-crds",
                "stage": 0,
            },
            {
                "releaseName": "cxcli-soperator-fluxcd-product",
                "upstreamReleaseName": "soperator-fluxcd-product",
                "namespace": "flux-system",
                "sourceKind": "HelmChart",
                "sourceName": "source-product",
                "stage": 1,
                "isMain": True,
            },
        ]
    }


def _outer_bundle() -> str:
    patches = []
    for upstream_name, final_name in (
        ("soperator-fluxcd-ns", "cxcli-soperator-fluxcd-ns"),
        ("soperator-fluxcd-product", "cxcli-soperator-fluxcd-product"),
    ):
        patches.append(
            {
                "target": {
                    "group": "helm.toolkit.fluxcd.io",
                    "version": "v2",
                    "kind": "HelmRelease",
                    "name": upstream_name,
                },
                "patch": yaml.safe_dump(
                    [
                        {
                            "op": "replace",
                            "path": "/metadata/name",
                            "value": final_name,
                        }
                    ],
                    sort_keys=False,
                ),
            }
        )
    return yaml.safe_dump(
        {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {"name": "soperator-controller", "namespace": "soperator-system"},
            "spec": {
                "interval": "30m",
                "postRenderers": [{"kustomize": {"patches": patches}}],
            },
        },
        sort_keys=False,
    )


def test_staged_outer_identity_comes_from_rendered_graph_members() -> None:
    releases = [
        {"releaseName": "cxcli-soperator-child-a", "namespace": "flux-system"},
        {"releaseName": "cxcli-soperator-child-b", "namespace": "flux-system"},
    ]
    unrelated = {
        "kind": "HelmRelease",
        "metadata": {"name": "gpu-operator", "namespace": "nvidia-gpu-operator"},
        "spec": {},
    }
    outer = {
        "kind": "HelmRelease",
        "metadata": {"name": "soperator-controller", "namespace": "soperator-system"},
        "spec": {
            "postRenderers": [
                {
                    "kustomize": {
                        "patches": [
                            {"patch": "value: cxcli-soperator-child-a"},
                            {"patch": "value: cxcli-soperator-child-b"},
                        ]
                    }
                }
            ]
        },
    }

    resolved = flux_ops._staged_soperator_outer_release(
        [unrelated, outer],
        releases,
    )

    assert resolved is outer


def test_existing_raw_child_frontier_requires_exact_outer_ownership() -> None:
    outer = yaml.safe_load(_outer_bundle())
    releases = _staged_contract()["releases"]
    assert isinstance(releases, list)
    rows = flux_ops._normalize_soperator_outer_post_renderers(
        outer,
        releases,
        suspend_children=True,
    )
    raw = rows[0]
    payload = {
        "items": [
            {
                "metadata": {
                    "name": raw["rawName"],
                    "namespace": raw["rawNamespace"],
                    "labels": {
                        "helm.toolkit.fluxcd.io/name": "soperator-controller",
                        "helm.toolkit.fluxcd.io/namespace": "soperator-system",
                    },
                    "annotations": {
                        "meta.helm.sh/release-name": "soperator-controller",
                        "meta.helm.sh/release-namespace": "soperator-system",
                    },
                },
                "spec": {"suspend": True},
            }
        ]
    }

    active = flux_ops._existing_soperator_release_frontier(
        payload,
        outer=outer,
        rows=rows,
    )

    assert active == {(raw["rawNamespace"], raw["rawName"])}


def test_kruise_child_preserves_webhook_object_drift_correction() -> None:
    outer = yaml.safe_load(_outer_bundle())
    releases = _staged_contract()["releases"]
    assert isinstance(releases, list)
    releases.append(
        {
            "releaseName": "cxcli-soperator-fluxcd-kruise",
            "upstreamReleaseName": "soperator-fluxcd-kruise",
            "namespace": "flux-system",
            "sourceKind": "HelmChart",
            "sourceName": "source-kruise",
            "stage": 1,
        }
    )
    patches = outer["spec"]["postRenderers"][0]["kustomize"]["patches"]
    patches.append(
        {
            "target": {
                "group": "helm.toolkit.fluxcd.io",
                "version": "v2",
                "kind": "HelmRelease",
                "name": "soperator-fluxcd-kruise",
            },
            "patch": yaml.safe_dump(
                [
                    {
                        "op": "replace",
                        "path": "/metadata/name",
                        "value": "cxcli-soperator-fluxcd-kruise",
                    }
                ],
                sort_keys=False,
            ),
        }
    )

    flux_ops._normalize_soperator_outer_post_renderers(
        outer,
        releases,
        suspend_children=False,
    )

    kruise_patch = next(item for item in patches if item["target"]["name"].endswith("-kruise"))
    operations = yaml.safe_load(kruise_patch["patch"])
    assert [
        operation for operation in operations if operation["path"] == "/spec/driftDetection"
    ] == [
        {
            "op": "test",
            "path": "/spec/driftDetection",
            "value": {"mode": "warn"},
        },
        {
            "op": "replace",
            "path": "/spec/driftDetection",
            "value": {
                "mode": "enabled",
                "ignore": [
                    {
                        "paths": [
                            "/webhooks",
                            "/metadata/annotations/template",
                        ],
                        "target": {
                            "group": "admissionregistration.k8s.io",
                            "version": "v1",
                            "kind": "MutatingWebhookConfiguration",
                            "name": "kruise-mutating-webhook-configuration",
                        },
                    },
                    {
                        "paths": [
                            "/webhooks",
                            "/metadata/annotations/template",
                        ],
                        "target": {
                            "group": "admissionregistration.k8s.io",
                            "version": "v1",
                            "kind": "ValidatingWebhookConfiguration",
                            "name": "kruise-validating-webhook-configuration",
                        },
                    },
                ],
            },
        },
    ]
    ignore_rules = next(
        operation["value"]["ignore"]
        for operation in operations
        if operation["path"] == "/spec/driftDetection" and operation["op"] == "replace"
    )
    assert all("" not in rule["paths"] for rule in ignore_rules)
    assert all(
        rule["paths"] == ["/webhooks", "/metadata/annotations/template"] for rule in ignore_rules
    )


def test_existing_raw_child_frontier_rejects_foreign_or_extra_children() -> None:
    outer = yaml.safe_load(_outer_bundle())
    releases = _staged_contract()["releases"]
    assert isinstance(releases, list)
    rows = flux_ops._normalize_soperator_outer_post_renderers(
        outer,
        releases,
        suspend_children=True,
    )
    raw = rows[0]
    foreign = {
        "items": [
            {
                "metadata": {
                    "name": raw["rawName"],
                    "namespace": raw["rawNamespace"],
                    "labels": {},
                    "annotations": {},
                }
            }
        ]
    }
    with pytest.raises(flux_ops.SoperatorSafetyPauseError, match="not owned"):
        flux_ops._existing_soperator_release_frontier(foreign, outer=outer, rows=rows)

    extra = {
        "items": [
            {
                "metadata": {
                    "name": "soperator-controller-unexpected",
                    "namespace": "soperator-system",
                    "labels": {
                        "helm.toolkit.fluxcd.io/name": "soperator-controller",
                        "helm.toolkit.fluxcd.io/namespace": "soperator-system",
                    },
                    "annotations": {
                        "meta.helm.sh/release-name": "soperator-controller",
                        "meta.helm.sh/release-namespace": "soperator-system",
                    },
                }
            }
        ]
    }
    with pytest.raises(flux_ops.SoperatorSafetyPauseError, match="unexpected"):
        flux_ops._existing_soperator_release_frontier(extra, outer=outer, rows=rows)


def test_existing_raw_child_frontier_rejects_reconciled_or_terminating_children() -> None:
    outer = yaml.safe_load(_outer_bundle())
    releases = _staged_contract()["releases"]
    assert isinstance(releases, list)
    rows = flux_ops._normalize_soperator_outer_post_renderers(
        outer,
        releases,
        suspend_children=True,
    )
    raw = rows[0]
    metadata = {
        "name": raw["rawName"],
        "namespace": raw["rawNamespace"],
        "labels": {
            "helm.toolkit.fluxcd.io/name": "soperator-controller",
            "helm.toolkit.fluxcd.io/namespace": "soperator-system",
        },
        "annotations": {
            "meta.helm.sh/release-name": "soperator-controller",
            "meta.helm.sh/release-namespace": "soperator-system",
        },
    }
    reconciled = {
        "items": [
            {
                "metadata": metadata,
                "spec": {"suspend": False},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ]
    }
    with pytest.raises(flux_ops.SoperatorSafetyPauseError, match="may have reconciled"):
        flux_ops._existing_soperator_release_frontier(
            reconciled,
            outer=outer,
            rows=rows,
        )

    terminating = {
        "items": [
            {
                "metadata": {**metadata, "deletionTimestamp": "2026-01-01T00:00:00Z"},
                "spec": {"suspend": True},
            }
        ]
    }
    with pytest.raises(flux_ops.SoperatorSafetyPauseError, match="terminating"):
        flux_ops._existing_soperator_release_frontier(
            terminating,
            outer=outer,
            rows=rows,
        )


def test_remove_helmrelease_suspend_uses_json_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def _fake_run(command: list[str], **kwargs):
        del kwargs
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="patched", stderr="")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops._remove_helmrelease_suspend(
        name="soperator-main",
        namespace="flux-system",
        cache_dir=tmp_path / "cache",
        env={},
    )

    assert commands[0][commands[0].index("--type") + 1] == "json"
    patch = json.loads(commands[0][commands[0].index("-p") + 1])
    assert patch == [{"op": "remove", "path": "/spec/suspend"}]


def test_staged_release_rejects_missing_main_before_kubectl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _staged_contract()
    contract["releases"][1].pop("isMain")  # type: ignore[index,union-attr]
    monkeypatch.setattr(
        flux_ops,
        "_rendered_soperator_graph_contract",
        lambda _path: contract,
    )
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("kubectl must not run for an invalid graph")
        ),
    )

    with pytest.raises(ValueError, match="exactly one main workload"):
        flux_ops.apply_staged_soperator_release(
            _paths(tmp_path),
            cache_dir=tmp_path / "cache",
            freeze_main_workload_authority=lambda identity: identity,
        )


def test_staged_release_opens_exact_sources_and_releases_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patches: list[tuple[str, bool]] = []
    activations: list[str] = []
    source_stages: list[set[str]] = []
    waited_stages: list[int] = []
    dependency_stages: list[tuple[str, ...]] = []
    progress: list[tuple[int, int, tuple[str, ...]]] = []
    applied: list[str] = []
    main_stage_events: list[str] = []

    def _fake_run(command: list[str], **kwargs):
        if "kustomize" in command:
            return SimpleNamespace(returncode=0, stdout=_outer_bundle(), stderr="")
        if "get" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="NotFound")
        if "apply" in command:
            applied.append(str(kwargs.get("input") or ""))
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")
        raise AssertionError(command)

    def _patch(*, name: str, suspend: bool, **kwargs) -> None:
        del kwargs
        patches.append((name, suspend))

    def _sources(*args, source_names: set[str], **kwargs):
        del args, kwargs
        source_stages.append(source_names)
        return ()

    monkeypatch.setattr(
        flux_ops, "_rendered_soperator_graph_contract", lambda path: _staged_contract()
    )
    monkeypatch.setattr(
        flux_ops, "_run_kubectl_json_process", lambda *args, **kwargs: {"items": []}
    )
    monkeypatch.setattr(flux_ops, "_patch_helmrelease_suspend", _patch)
    monkeypatch.setattr(
        flux_ops,
        "_remove_helmrelease_suspend",
        lambda *, name, **kwargs: (
            activations.append(name),
            main_stage_events.append(f"activate:{name}"),
        ),
    )
    monkeypatch.setattr(flux_ops, "_wait_for_helmrelease_quiescence", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        flux_ops, "_wait_for_suspended_child_inventory", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(flux_ops, "prepare_soperator_release_sources", _sources)
    monkeypatch.setattr(
        flux_ops,
        "_wait_for_soperator_release_stage",
        lambda contract, stage, **kwargs: (
            waited_stages.append(stage),
            main_stage_events.append(f"wait:{stage}"),
        ),
    )
    monkeypatch.setattr(
        flux_ops,
        "_wait_for_soperator_dependency_health",
        lambda stage_items, **kwargs: dependency_stages.append(
            tuple(sorted(str(item["releaseName"]) for item in stage_items))
        ),
    )
    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops.apply_staged_soperator_release(
        _paths(tmp_path),
        cache_dir=tmp_path / "cache",
        poll_interval_seconds=0.01,
        on_stage_progress=lambda index, count, releases: progress.append((index, count, releases)),
        prepare_main_release_stage=lambda: (
            main_stage_events.append("prepare-main") or {"status": "prepared"}
        ),
        finish_main_release_stage=lambda _prepared, opened: main_stage_events.append(
            f"finish-main:{opened}"
        ),
    )

    assert source_stages == [{"source-crds"}, {"source-product"}]
    assert waited_stages == [0, 1]
    assert dependency_stages == [
        ("cxcli-soperator-fluxcd-ns",),
        ("cxcli-soperator-fluxcd-product",),
    ]
    assert progress == [
        (1, 2, ("cxcli-soperator-fluxcd-ns",)),
        (2, 2, ("cxcli-soperator-fluxcd-product",)),
    ]
    assert patches == [
        ("soperator-controller", True),
        ("cxcli-soperator-fluxcd-ns", True),
    ]
    assert activations == [
        "cxcli-soperator-fluxcd-ns",
        "cxcli-soperator-fluxcd-product",
        "soperator-controller",
    ]
    assert main_stage_events == [
        "activate:cxcli-soperator-fluxcd-ns",
        "wait:0",
        "prepare-main",
        "activate:cxcli-soperator-fluxcd-product",
        "wait:1",
        "finish-main:True",
        "activate:soperator-controller",
    ]
    assert len(applied) == 2
    staged_outer = yaml.safe_load(applied[0])
    staged_patches = staged_outer["spec"]["postRenderers"][0]["kustomize"]["patches"]
    assert {item["target"]["name"] for item in staged_patches} == {
        "soperator-controller-ns",
        "soperator-controller-product",
    }
    assert all("namespace" not in item["target"] for item in staged_patches)
    assert all(
        any(
            operation["path"] == "/spec/suspend" and operation["value"] is True
            for operation in yaml.safe_load(item["patch"])
        )
        for item in staged_patches
    )
    assert staged_outer["spec"]["suspend"] is False
    stable_outer = yaml.safe_load(applied[1])
    assert stable_outer["spec"]["suspend"] is True
    stable_patches = stable_outer["spec"]["postRenderers"][0]["kustomize"]["patches"]
    stable_suspend_by_target = {
        item["target"]["name"]: any(
            operation["path"] == "/spec/suspend" for operation in yaml.safe_load(item["patch"])
        )
        for item in stable_patches
    }
    assert stable_suspend_by_target == {
        "soperator-controller-ns": True,
        "soperator-controller-product": False,
    }


def test_staged_release_does_not_resume_stage_when_fresh_source_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patches: list[tuple[str, bool]] = []

    def _fake_run(command: list[str], **kwargs):
        del kwargs
        if "kustomize" in command:
            return SimpleNamespace(returncode=0, stdout=_outer_bundle(), stderr="")
        if "get" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="NotFound")
        if "apply" in command:
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(
        flux_ops, "_rendered_soperator_graph_contract", lambda path: _staged_contract()
    )
    monkeypatch.setattr(
        flux_ops, "_run_kubectl_json_process", lambda *args, **kwargs: {"items": []}
    )
    monkeypatch.setattr(
        flux_ops,
        "_patch_helmrelease_suspend",
        lambda *, name, suspend, **kwargs: patches.append((name, suspend)),
    )
    monkeypatch.setattr(
        flux_ops,
        "_remove_helmrelease_suspend",
        lambda *, name, **kwargs: patches.append((name, False)),
    )
    monkeypatch.setattr(flux_ops, "_wait_for_helmrelease_quiescence", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        flux_ops, "_wait_for_suspended_child_inventory", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        flux_ops,
        "prepare_soperator_release_sources",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upstream unavailable")),
    )
    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        flux_ops.apply_staged_soperator_release(
            _paths(tmp_path),
            cache_dir=tmp_path / "cache",
            poll_interval_seconds=0.01,
        )

    assert ("cxcli-soperator-fluxcd-ns", False) not in patches
    assert ("cxcli-soperator-fluxcd-product", False) not in patches


def _main_wait_target() -> flux_ops.FluxWaitTarget:
    return flux_ops.FluxWaitTarget(
        resource_type="helmrelease.helm.toolkit.fluxcd.io",
        name="soperator-main",
        namespace="flux-system",
        kind="HelmRelease",
        is_source=False,
        api_version="helm.toolkit.fluxcd.io/v2",
        is_soperator_graph_member=True,
        is_soperator_main=True,
        source_kind="OCIRepository",
        source_name="soperator-upstream",
        source_revision=_DIGEST,
    )


def _main_identity() -> flux_ops.SoperatorMainWorkloadIdentity:
    target = _main_wait_target()
    return flux_ops.SoperatorMainWorkloadIdentity(
        api_version=target.api_version,
        kind=target.kind,
        namespace=target.namespace,
        name=target.name,
        source_kind=target.source_kind,
        source_name=target.source_name,
        source_revision=target.source_revision,
        uid="main-uid",
        generation=3,
        observed_generation=3,
    )


def _main_stage_contract() -> dict[str, object]:
    return {
        "releases": [
            {
                "releaseName": "soperator-main",
                "namespace": "flux-system",
                "sourceKind": "OCIRepository",
                "sourceName": "soperator-upstream",
                "stage": 0,
                "isMain": True,
            }
        ]
    }


def _main_release_payload(*, ready: bool, stalled: bool) -> dict[str, object]:
    conditions = []
    if ready:
        conditions.append({"type": "Ready", "status": "True"})
    if stalled:
        conditions.append(
            {
                "type": "Stalled",
                "status": "True",
                "reason": "InstallFailed",
                "message": "main chart cannot be installed",
            }
        )
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "namespace": "flux-system",
            "name": "soperator-main",
            "uid": "main-uid",
            "generation": 3,
        },
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream",
                "namespace": "flux-system",
            }
        },
        "status": {"observedGeneration": 3, "conditions": conditions},
    }


def test_stage_freezes_main_before_accepting_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    events: list[str] = []
    identity = _main_identity()
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _main_release_payload(ready=True, stalled=False),
    )
    monkeypatch.setattr(
        flux_ops,
        "_observe_soperator_main_workload_identity",
        lambda *args, **kwargs: events.append("observe") or identity,
    )

    flux_ops._wait_for_soperator_release_stage(
        _main_stage_contract(),
        0,
        cache_dir=Path("cache"),
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0.01,
        main_target=_main_wait_target(),
        freeze_main_workload_authority=lambda observed: events.append("freeze") or observed,
    )

    assert events == ["observe", "freeze"]


def test_stage_freezes_current_main_while_it_is_still_progressing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen: list[flux_ops.SoperatorMainWorkloadIdentity] = []
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _main_release_payload(ready=False, stalled=False),
    )
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_json",
        lambda *args, **kwargs: {
            "kind": "OCIRepository",
            "metadata": {
                "namespace": "flux-system",
                "name": "soperator-upstream",
            },
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "artifact": {"digest": _DIGEST},
            },
        },
    )

    with pytest.raises(RuntimeError, match="did not become Ready"):
        flux_ops._wait_for_soperator_release_stage(
            _main_stage_contract(),
            0,
            cache_dir=Path("cache"),
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
            main_target=_main_wait_target(),
            freeze_main_workload_authority=lambda identity: frozen.append(identity) or identity,
        )

    assert frozen == [_main_identity()]


def test_stage_freezes_main_before_stalled_wins_over_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    identity = _main_identity()
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _main_release_payload(ready=True, stalled=True),
    )
    monkeypatch.setattr(
        flux_ops,
        "_observe_soperator_main_workload_identity",
        lambda *args, **kwargs: events.append("observe") or identity,
    )

    with pytest.raises(flux_ops.SoperatorMainWorkloadTerminalError) as raised:
        flux_ops._wait_for_soperator_release_stage(
            _main_stage_contract(),
            0,
            cache_dir=Path("cache"),
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
            main_target=_main_wait_target(),
            freeze_main_workload_authority=lambda observed: events.append("freeze") or observed,
        )

    assert events == ["observe", "freeze"]
    assert raised.value.identity == identity
    assert raised.value.reason == "InstallFailed"
    assert "main chart cannot be installed" not in str(raised.value)


def test_stage_without_freeze_callback_never_classifies_main_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _main_release_payload(ready=True, stalled=True),
    )

    with pytest.raises(RuntimeError, match="did not become Ready"):
        flux_ops._wait_for_soperator_release_stage(
            _main_stage_contract(),
            0,
            cache_dir=Path("cache"),
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
        )


def test_non_main_stalled_release_retries_without_waiting_for_stage_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = {
        "releases": [
            {
                "releaseName": "dependency",
                "namespace": "flux-system",
                "sourceKind": "OCIRepository",
                "sourceName": "dependency-source",
                "stage": 0,
            }
        ]
    }
    payload = _main_release_payload(ready=True, stalled=True)
    payload["metadata"]["name"] = "dependency"  # type: ignore[index]
    payload["spec"]["chartRef"]["name"] = "dependency-source"  # type: ignore[index]
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: payload,
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "staged Soperator HelmRelease flux-system/dependency stalled "
            r"\(InstallFailed\); retrying the exact declared release stage"
        ),
    ):
        flux_ops._wait_for_soperator_release_stage(
            contract,
            0,
            cache_dir=Path("cache"),
            env={},
            timeout_seconds=30,
            poll_interval_seconds=0.01,
        )


def test_kruise_dependency_health_requires_crds_workloads_and_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def _payload(command: list[str], **kwargs):
        del kwargs
        commands.append(tuple(command))
        name = command[-3]
        if "customresourcedefinition" in command:
            return {"status": {"conditions": [{"type": "Established", "status": "True"}]}}
        if "deployment" in command:
            return {
                "metadata": {"generation": 2},
                "spec": {"replicas": 1},
                "status": {
                    "observedGeneration": 2,
                    "readyReplicas": 1,
                    "availableReplicas": 1,
                },
            }
        if "daemonset" in command:
            return {
                "metadata": {"generation": 3},
                "status": {
                    "observedGeneration": 3,
                    "desiredNumberScheduled": 2,
                    "numberReady": 2,
                    "numberAvailable": 2,
                    "numberUnavailable": 0,
                },
            }
        if "endpoints" in command and name == "kruise-webhook-service":
            return {"subsets": [{"addresses": [{"ip": "192.0.2.10"}]}]}
        raise AssertionError(command)

    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", _payload)

    flux_ops._wait_for_soperator_dependency_health(
        [{"upstreamReleaseName": "soperator-fluxcd-kruise"}],
        cache_dir=Path("cache"),
        env={},
        timeout_seconds=0,
        poll_interval_seconds=0.01,
    )

    assert any("resourcedistributions.apps.kruise.io" in command for command in commands)
    assert any("statefulsets.apps.kruise.io" in command for command in commands)
    assert any("kruise-controller-manager" in command for command in commands)
    assert any("kruise-daemon" in command for command in commands)
    assert any("kruise-webhook-service" in command for command in commands)


def test_kruise_default_recovery_repairs_only_missing_soperator_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventories = iter(
        [
            {
                "items": [
                    {
                        "metadata": {
                            "name": "controller",
                            "namespace": "soperator",
                            "resourceVersion": "17",
                            "labels": {"app.kubernetes.io/managed-by": "slurm-operator"},
                            "ownerReferences": [
                                {
                                    "kind": "SlurmCluster",
                                    "name": "soperator",
                                    "controller": True,
                                }
                            ],
                        },
                        "spec": {
                            "updateStrategy": {
                                "type": "RollingUpdate",
                                "rollingUpdate": {"podUpdatePolicy": "InPlaceIfPossible"},
                            }
                        },
                    },
                    {
                        "metadata": {
                            "name": "login",
                            "namespace": "soperator",
                            "resourceVersion": "18",
                        },
                        "spec": {
                            "updateStrategy": {
                                "type": "RollingUpdate",
                                "rollingUpdate": {"partition": 0},
                            }
                        },
                    },
                ]
            },
            {
                "items": [
                    {
                        "metadata": {
                            "name": "controller",
                            "namespace": "soperator",
                            "resourceVersion": "19",
                        },
                        "spec": {
                            "updateStrategy": {
                                "type": "RollingUpdate",
                                "rollingUpdate": {"partition": 0},
                            }
                        },
                    }
                ]
            },
        ]
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: next(inventories),
    )

    def _fake_run(command: list[str], **kwargs):
        del kwargs
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="patched", stderr="")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops._restore_soperator_kruise_statefulset_defaults(
        cache_dir=tmp_path / "cache",
        env={},
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )

    assert len(commands) == 1
    command = commands[0]
    assert "statefulsets.apps.kruise.io" in command
    assert command[command.index("-n") + 1] == "soperator"
    patch = json.loads(command[command.index("-p") + 1])
    assert patch == {
        "metadata": {"resourceVersion": "17"},
        "spec": {"updateStrategy": {"rollingUpdate": {"partition": 0}}},
    }


def test_kruise_default_recovery_retries_unavailable_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = {
        "items": [
            {
                "metadata": {
                    "name": "controller",
                    "namespace": "soperator",
                    "resourceVersion": "17",
                    "labels": {"app.kubernetes.io/managed-by": "slurm-operator"},
                    "ownerReferences": [{"kind": "SlurmCluster", "controller": True}],
                },
                "spec": {
                    "updateStrategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {},
                    }
                },
            }
        ]
    }
    inventories = iter([missing, missing, {"items": []}])
    outcomes = iter(
        [
            SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="failed calling webhook: no endpoints available for service",
            ),
            SimpleNamespace(returncode=0, stdout="patched", stderr=""),
        ]
    )
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: next(inventories),
    )
    monkeypatch.setattr(flux_ops.subprocess, "run", lambda *args, **kwargs: next(outcomes))

    flux_ops._restore_soperator_kruise_statefulset_defaults(
        cache_dir=tmp_path / "cache",
        env={},
        timeout_seconds=1,
        poll_interval_seconds=0.01,
    )


def test_kruise_default_recovery_rejects_non_soperator_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: {
            "items": [
                {
                    "metadata": {
                        "name": "foreign",
                        "namespace": "other",
                        "resourceVersion": "7",
                        "labels": {"app.kubernetes.io/managed-by": "slurm-operator"},
                        "ownerReferences": [{"kind": "Deployment", "controller": True}],
                    },
                    "spec": {
                        "updateStrategy": {
                            "type": "RollingUpdate",
                            "rollingUpdate": {},
                        }
                    },
                }
            ]
        },
    )
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("foreign StatefulSet must not be patched")
        ),
    )

    with pytest.raises(
        flux_ops.SoperatorSafetyPauseError,
        match="not owned by Soperator",
    ):
        flux_ops._restore_soperator_kruise_statefulset_defaults(
            cache_dir=tmp_path / "cache",
            env={},
            timeout_seconds=1,
            poll_interval_seconds=0.01,
        )


def test_soperator_controller_dependency_health_blocks_unready_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _payload(command: list[str], **kwargs):
        del kwargs
        if "deployment" in command:
            return {
                "metadata": {"generation": 4},
                "spec": {"replicas": 1},
                "status": {
                    "observedGeneration": 4,
                    "readyReplicas": 1,
                    "availableReplicas": 1,
                },
            }
        if "endpoints" in command:
            return {"subsets": []}
        raise AssertionError(command)

    monkeypatch.setattr(flux_ops, "_run_kubectl_json_process", _payload)

    with pytest.raises(RuntimeError, match="Soperator controller APIs and webhook"):
        flux_ops._wait_for_soperator_dependency_health(
            [{"upstreamReleaseName": "soperator-fluxcd-soperator"}],
            cache_dir=Path("cache"),
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
        )
