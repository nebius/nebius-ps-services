from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from io import StringIO
from pathlib import Path
from types import ModuleType

import yaml


def _load_generator() -> ModuleType:
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "generate_soperator_migration_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("generate_soperator_migration_profiles", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _committed_profile_payload() -> dict[str, object]:
    profile_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "nebius_cxcli"
        / "soperator_migration_profiles.yaml"
    )
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


class _JsonResponse(StringIO):
    def __enter__(self) -> _JsonResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_generator_fetches_all_github_release_pages(monkeypatch) -> None:
    generator = _load_generator()
    monkeypatch.setattr(generator, "RELEASES_PER_PAGE", 2)
    calls: list[str] = []
    payloads = {
        1: [{"tag_name": "4.0.1"}, {"tag_name": "3.0.5"}],
        2: [{"tag_name": "2.0.0"}],
    }

    def _urlopen(url: str, *, timeout: int) -> _JsonResponse:
        assert timeout == 30
        calls.append(url)
        page = int(url.rsplit("page=", maxsplit=1)[1])
        return _JsonResponse(json.dumps(payloads.get(page, [])))

    monkeypatch.setattr(generator.urllib.request, "urlopen", _urlopen)

    releases = generator._fetch_releases()

    assert [release["tag_name"] for release in releases] == ["4.0.1", "3.0.5", "2.0.0"]
    assert "per_page=2&page=1" in calls[0]
    assert "per_page=2&page=2" in calls[1]
    assert len(calls) == 2


def _tarball_bytes(files: list[tuple[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files:
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _chart_tarball(files: list[tuple[str, str]] | None = None) -> bytes:
    chart_files = [
        (
            "nebius-soperator-test/helm/soperator/Chart.yaml",
            "apiVersion: v2\n"
            "name: helm-soperator\n"
            "version: 3.0.5\n"
            "appVersion: \"3.0.5\"\n",
        ),
        (
            "nebius-soperator-test/helm/soperator/values.yaml",
            "worker:\n"
            "  image:\n"
            "    repository: cr.eu-north1.nebius.cloud/soperator/worker_slurmd\n"
            "    tag: 3.0.5\n",
        ),
        (
            "nebius-soperator-test/helm/soperator/crds/slurmcluster.yaml",
            "kind: CustomResourceDefinition\n"
            "metadata:\n"
            "  name: slurmclusters.slurm.nebius.ai\n",
        ),
        (
            "nebius-soperator-test/helm/soperator/templates/slurm-cluster.yaml",
            "kind: SlurmCluster\n"
            "spec:\n"
            "  image: cr.eu-north1.nebius.cloud/soperator/controller:3.0.5\n",
        ),
        (
            "nebius-soperator-test/helm/soperator-activechecks/Chart.yaml",
            "apiVersion: v2\n"
            "name: soperator-activechecks\n"
            "version: 3.0.5\n"
            "appVersion: \"3.0.5\"\n",
        ),
        (
            "nebius-soperator-test/helm/soperator-activechecks/templates/check.yaml",
            "kind: ActiveCheck\n",
        ),
    ]
    if files is not None:
        chart_files = files
    return _tarball_bytes(chart_files)


def test_generator_extracts_chart_contract_fingerprints_from_tarball() -> None:
    generator = _load_generator()

    contract = generator._release_contract_from_tarball(_chart_tarball())

    assert contract["source_tarball_sha256"]
    assert contract["contract_fingerprint"]
    assert contract["main_chart"]["chart_path"] == "helm/soperator"
    assert contract["main_chart"]["chart_name"] == "helm-soperator"
    assert contract["main_chart"]["chart_version"] == "3.0.5"
    assert contract["main_chart"]["app_version"] == "3.0.5"
    components = {component["chart_path"]: component for component in contract["component_contracts"]}
    soperator = components["helm/soperator"]
    assert soperator["id"] == "soperator"
    assert soperator["crds"]["file_count"] == 1
    assert soperator["templates"]["file_count"] == 1
    assert soperator["slurm_contract"]["file_count"] == 2
    assert (
        "cr.eu-north1.nebius.cloud/soperator/worker_slurmd:3.0.5"
        in soperator["images"]["values"]
    )
    assert (
        "cr.eu-north1.nebius.cloud/soperator/controller:3.0.5"
        in soperator["images"]["values"]
    )
    assert components["helm/soperator-activechecks"]["slurm_contract"]["file_count"] == 2


def test_generator_contract_fingerprint_is_deterministic_across_tar_order() -> None:
    generator = _load_generator()
    files = [
        (
            "nebius-soperator-test/helm/soperator/Chart.yaml",
            "apiVersion: v2\nname: helm-soperator\nversion: 4.0.1\nappVersion: \"4.0.1\"\n",
        ),
        (
            "nebius-soperator-test/helm/soperator/templates/slurm.yaml",
            "kind: SlurmCluster\n",
        ),
        (
            "nebius-soperator-test/helm/soperator/crds/slurm.yaml",
            "metadata:\n  name: slurmclusters.slurm.nebius.ai\n",
        ),
    ]

    first = generator._release_contract_from_tarball(_tarball_bytes(files))
    second = generator._release_contract_from_tarball(_tarball_bytes(list(reversed(files))))

    assert first["contract_fingerprint"] == second["contract_fingerprint"]
    assert (
        first["component_contracts"][0]["contract_fingerprint"]
        == second["component_contracts"][0]["contract_fingerprint"]
    )


def test_generator_profile_payload_records_scope_contracts_and_compatibility_axes() -> None:
    generator = _load_generator()
    contract = generator._release_contract_from_tarball(_chart_tarball())

    payload = generator._profile_payload(
        [
            {"tag_name": "4.0.1", "published_at": "2026-02-01T00:00:00Z"},
            {"tag_name": "3.0.5", "published_at": "2025-12-01T00:00:00Z"},
            {"tag_name": "v1.14.1", "published_at": "2024-09-23T00:00:00Z"},
        ],
        release_contracts={"3.0.5": contract},
    )

    assert (
        payload["generator_scope"]
        == "chart-tarball-crd-template-image-and-slurm-contract-fingerprints"
    )
    support_rules = {rule["id"]: rule for rule in payload["support_rules"]}
    assert payload["support_rules"][0]["id"] == "k8s-1-33-requires-soperator-1-23"
    assert support_rules["legacy-before-1-22-not-validated"]["status"] == "not_validated"
    assert (
        support_rules["k8s-before-1-33-soperator-1-22-plus-supported"]["target_k8s_max"]
        == "1.33"
    )
    assert (
        support_rules["k8s-before-1-33-soperator-1-22-plus-supported"][
            "target_version_range"
        ]
        == "=4.0.2"
    )
    assert (
        support_rules["k8s-before-1-33-soperator-1-22-plus-supported"][
            "target_chart_version_policy"
        ]
        == "cxcli_pin"
    )
    assert support_rules["k8s-before-1-33-soperator-1-22-plus-supported"][
        "recommended_order"
    ] == {"soperator_after_k8s_min": "1.32"}
    assert (
        support_rules["k8s-1-33-requires-soperator-1-23"]["target_version_range"]
        == "<1.23.0"
    )
    assert (
        support_rules["soperator-target-same-app-non-cxcli-pin-not-validated"][
            "status"
        ]
        == "not_validated"
    )
    assert (
        support_rules["soperator-target-same-app-non-cxcli-pin-not-validated"][
            "target_version_range"
        ]
        == "=4.0.2"
    )
    assert (
        support_rules["soperator-target-same-app-non-cxcli-pin-not-validated"][
            "target_chart_version_policy"
        ]
        == "not_cxcli_pin"
    )
    assert (
        support_rules["soperator-target-before-cxcli-pin-not-validated"]["status"]
        == "not_validated"
    )
    assert (
        support_rules["soperator-target-before-cxcli-pin-not-validated"][
            "target_version_range"
        ]
        == ">=1.23.0,<4.0.2"
    )
    assert (
        support_rules["soperator-target-newer-than-cxcli-pin-not-validated"][
            "target_version_range"
        ]
        == ">4.0.2,<5.0.0"
    )
    assert support_rules["k8s-1-33-soperator-4-supported"]["status"] == "supported"
    assert (
        support_rules["k8s-1-33-soperator-4-supported"]["target_version_range"]
        == "=4.0.2"
    )
    assert (
        support_rules["k8s-1-33-soperator-4-supported"][
            "target_chart_version_policy"
        ]
        == "cxcli_pin"
    )
    assert support_rules["k8s-1-33-soperator-4-supported"]["recommended_order"] == {
        "soperator_after_k8s_min": "1.32"
    }
    assert [release["version"] for release in payload["releases"]] == [
        "1.14.1",
        "3.0.5",
        "4.0.1",
    ]
    assert payload["releases"][0]["profile_id"] == "legacy-v1-to-target"
    assert payload["releases"][1]["profile_id"] == "v3-to-target"
    assert payload["profile_groups"]["v3-to-target"]["requires_aligned_sfs"] is True
    assert (
        payload["profile_groups"]["v3-to-target"]["compatibility_axes"]["compute_layout"]
        == "replace-and-roll"
    )
    assert (
        payload["profile_groups"]["v3-to-target"]["compatibility_axes"]["node_label_layout"][
            "source_role_label_keys"
        ]
        == ["slurm.nebius.ai/nodeset", "slurm.nebius.ai/nodeset-name"]
    )
    assert (
        payload["profile_groups"]["v3-to-target"]["compatibility_axes"]["node_label_layout"][
            "target_role_label_key"
        ]
        == "slurm.nebius.ai/nodeset-name"
    )
    assert (
        payload["profile_groups"]["v4-to-target"]["compatibility_axes"]["storage_layout"]
        == "adopt-existing-or-create-if-missing"
    )
    assert (
        payload["profile_groups"]["v4-to-target"]["compatibility_axes"]["node_label_layout"][
            "source_role_label_keys"
        ]
        == ["slurm.nebius.ai/nodeset-name"]
    )
    v1_quiesce = payload["profile_groups"]["legacy-v1-to-target"]["execution_contract"][
        "source_controller_quiesce"
    ]
    assert v1_quiesce["required_before_target_compute_reconcile"] is True
    assert {
        "kind": "MutatingWebhookConfiguration",
        "name": "soperator-controller-mutating-webhook-configuration",
    } in v1_quiesce["admission_webhooks"]
    assert {
        "kind": "ValidatingWebhookConfiguration",
        "name": "soperator-controller-validating-webhook-configuration",
    } in v1_quiesce["admission_webhooks"]
    assert {
        "kind": "MutatingWebhookConfiguration",
        "name": "slurm-operator-mutating-webhook-configuration",
    } in v1_quiesce["admission_webhooks"]
    assert {
        "kind": "ValidatingWebhookConfiguration",
        "name": "slurm-operator-validating-webhook-configuration",
    } in v1_quiesce["admission_webhooks"]
    assert {
        "namespace": "soperator-system",
        "name": "soperator-controller-manager",
        "release_name": "soperator-controller",
        "chart_prefix": "helm-soperator",
    } in v1_quiesce["deployments"]
    assert {
        "namespace": "soperator",
        "release_name": "slurm-operator",
        "chart_prefix": "slurm-operator",
    } in v1_quiesce["deployments"]
    v2_quiesce = payload["profile_groups"]["v2-to-target"]["execution_contract"][
        "source_controller_quiesce"
    ]
    assert v2_quiesce["required_before_target_compute_reconcile"] is True
    assert {
        "kind": "MutatingWebhookConfiguration",
        "name": "soperator-controller-mutating-webhook-configuration",
    } in v2_quiesce["admission_webhooks"]
    assert {
        "kind": "ValidatingWebhookConfiguration",
        "name": "soperator-controller-validating-webhook-configuration",
    } in v2_quiesce["admission_webhooks"]
    assert all(
        webhook["name"] != "slurm-operator-mutating-webhook-configuration"
        for webhook in v2_quiesce["admission_webhooks"]
    )
    assert all(
        deployment.get("release_name") != "slurm-operator"
        for deployment in v2_quiesce["deployments"]
    )
    v3_quiesce = payload["profile_groups"]["v3-to-target"]["execution_contract"][
        "source_controller_quiesce"
    ]
    assert v3_quiesce == v2_quiesce
    release = payload["releases"][1]
    assert release["chart_path"] == "helm/soperator"
    assert release["chart_name"] == "helm-soperator"
    assert release["chart_version"] == "3.0.5"
    assert release["app_version"] == "3.0.5"
    assert release["contract_fingerprint"] == contract["contract_fingerprint"]
    assert release["component_contracts"][0]["crds"]["file_count"] == 1


def test_committed_support_rules_match_generator_policy() -> None:
    generator = _load_generator()
    payload = _committed_profile_payload()

    assert payload["support_rules"] == generator._support_rules()
