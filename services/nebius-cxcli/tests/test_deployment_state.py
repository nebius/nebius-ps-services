from __future__ import annotations

import copy

import pytest

from nebius_cxcli.deployment_state import (
    DeploymentGeneration,
    DeploymentState,
    ObjectVersion,
)
from nebius_cxcli.terraform_backend import TerraformBackendSettings


def settings():
    return TerraformBackendSettings(
        "project", "client", "region", "bucket", "state", "https://storage.example.invalid"
    )


class Store:
    def __init__(self):
        self.objects = {}
        self.next_etag = 0

    def read(self, key):
        return copy.deepcopy(self.objects.get(key))

    def attempt_keys(self, prefix):
        return tuple(sorted(key for key in self.objects if key.startswith(prefix + "/attempts/")))

    def write(self, key, value, *, etag):
        old = self.objects.get(key)
        if (old.etag if old else None) != etag:
            raise RuntimeError("CAS conflict")
        self.next_etag += 1
        version = str(self.next_etag)
        self.objects[key] = ObjectVersion(copy.deepcopy(value), version)
        return version


def test_matching_generation_resumes_across_runners_and_different_generation_is_blocked():
    store = Store()
    first = DeploymentState(store, settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {"desired": 1}}, {})
    started = first.begin(generation, plan={"stages": ["infrastructure", "release"]})
    second = DeploymentState(store, settings(), assert_held=lambda: None)
    resumed = second.begin(generation, plan={"must": "not replace frozen plan"})
    assert resumed == started
    assert second.generation(generation.identity) == generation
    with pytest.raises(RuntimeError, match="different deployment"):
        second.begin(DeploymentGeneration({"runtime_config": {"desired": 2}}, {}), plan={})
    checkpoint = first.checkpoint(started, stage="infrastructure", evidence={"verified": True})
    with pytest.raises(RuntimeError, match="CAS conflict"):
        second.checkpoint(resumed, stage="infrastructure", evidence={"stale": True})
    accepted = first.accept(checkpoint, evidence={"cluster_id": "cluster", "ready": True})
    assert accepted.value["active"] is None
    assert accepted.value["accepted"]["generation"] == generation.identity
    second.assert_publishable("another-generation")


def test_lost_fence_cannot_write_any_authority():
    def lost():
        raise RuntimeError("lost fence")

    store = Store()
    state = DeploymentState(store, settings(), assert_held=lost)
    with pytest.raises(RuntimeError, match="lost fence"):
        state.begin(DeploymentGeneration({}, {}), plan={})
    assert not store.objects


def test_corrupt_remote_generation_is_not_a_local_cache_fallback():
    store = Store()
    state = DeploymentState(store, settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    state.begin(generation, plan={})
    key = next(key for key in store.objects if "/generations/" in key)
    store.objects[key].value["manifest"]["runtime_config"]["changed"] = True
    with pytest.raises(RuntimeError, match="identity check"):
        state.generation(generation.identity)


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"schema": "nebius-cxcli.deployment.v1"},
        {"schema": "nebius-cxcli.deployment.v1", "active": {}, "accepted": None},
        {"schema": "nebius-cxcli.deployment.v1", "active": None, "accepted": {"generation": "bad"}},
    ],
)
def test_malformed_authority_is_never_absence(invalid):
    store = Store()
    state = DeploymentState(store, settings(), assert_held=lambda: None)
    store.objects[state.prefix + "/state.json"] = ObjectVersion(invalid, "etag")
    with pytest.raises(RuntimeError):
        state.assert_publishable("new")


def test_onboard_acceptance_is_atomic_and_destroy_clears_only_matching_owner():
    store = Store()
    state = DeploymentState(store, settings(), assert_held=lambda: None)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    evidence = {"identities": {"cluster": {"cluster_id": "id", "kubernetes_uid": "uid"}}}
    state.register(generation, evidence=evidence)
    assert state.read().value["active"] is None
    assert state.read().value["accepted"]["evidence"]["identities"] == evidence["identities"]
    with pytest.raises(RuntimeError, match="ownership"):
        state.register(generation, evidence={"identities": {"foreign": {}}})
    completed = state.read()
    state.clear_accepted(target_ref="foreign")
    assert state.read() == completed
    state.clear_accepted(target_ref="cluster")
    assert state.read().value["accepted"] is None
    state.clear_accepted(target_ref="cluster")


def test_private_generation_roundtrip_uses_portable_internal_paths(tmp_path):
    from pathlib import Path

    from nebius_cxcli.paths import ProjectPaths, private_project_root, resolve_project_paths

    def paths(root):
        project = root / "tenant" / "project"
        project.mkdir(parents=True)
        return ProjectPaths(
            project / "config.yaml",
            root,
            root,
            project,
            project / "generated",
            project / "generated/infra",
            project / "generated/flux",
            project / "generated/reports",
            "tenant",
            "project",
        )

    first = paths(tmp_path / "first")
    first.infra_dir.mkdir(parents=True)
    first.flux_dir.mkdir(parents=True)
    (first.infra_dir / "main.tf").write_text("terraform {}")
    (first.flux_dir / "main.yaml").write_text("kind: ConfigMap\n")
    manifest = {
        "runtime_config": {},
        "paths": {"generated_dir": str(first.generated_dir)},
        "deploy": {"targets": [{"flux_dir": str(first.flux_dir)}]},
    }
    generation = DeploymentGeneration.capture(first, manifest)
    second = paths(tmp_path / "second")
    materialized = generation.materialize(second)
    assert DeploymentGeneration.capture(second, materialized) == generation
    with private_project_root(second.repo_root):
        assert resolve_project_paths(second.config_path) == second
    assert not Path(materialized["paths"]["generated_dir"]).is_absolute()


def test_scoped_acceptance_and_destroy_preserve_other_target_generation():
    state = DeploymentState(Store(), settings(), assert_held=lambda: None)
    first = DeploymentGeneration({"runtime_config": {"revision": 1}}, {})
    second = DeploymentGeneration({"runtime_config": {"revision": 2}}, {})
    identities = {
        ref: {"cluster_id": ref, "kubernetes_uid": ref + "-uid"} for ref in ("one", "two")
    }
    record = state.begin(first, plan={})
    state.accept(
        record,
        evidence={
            "identities": identities,
            "targets": {
                ref: {
                    "generation": first.identity,
                    "identity": identity,
                    "desiredBundle": ref + "-old",
                }
                for ref, identity in identities.items()
            },
        },
    )
    record = state.begin(second, plan={"semanticPlan": {"selectedTargets": ["one"]}})
    with pytest.raises(RuntimeError, match="selected target"):
        state.accept(record, evidence={"targets": {}})
    state.accept(
        record,
        evidence={
            "selectedTargets": ["one"],
            "identities": {"one": identities["one"]},
            "targets": {
                "one": {
                    "generation": second.identity,
                    "identity": identities["one"],
                    "desiredBundle": "one-new",
                },
            },
        },
    )
    evidence = state.read().value["accepted"]["evidence"]
    assert evidence["targets"]["two"]["generation"] == first.identity
    assert evidence["targets"]["one"]["generation"] == second.identity
    state.clear_accepted(target_ref="one")
    evidence = state.read().value["accepted"]["evidence"]
    assert set(evidence["targets"]) == set(evidence["identities"]) == {"two"}
