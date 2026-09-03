from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import pytest

from nebius_cxcli.operation_config_authority import (
    ConfigGenerationTransition,
    apply_project_generation_transition,
    assert_config_authority_current,
    config_transition_from_payload,
    file_sha256,
    upsert_config_transition,
    validate_config_transition,
    validate_config_transition_chain,
)
from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction
from nebius_cxcli.render import ProjectGenerationPlan, project_generation_snapshot_sha256
from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@dataclass
class _Store:
    transitions: dict[str, ConfigGenerationTransition] = field(default_factory=dict)
    fail_first_applied_record: bool = False

    def get(self, stage: str) -> ConfigGenerationTransition | None:
        return self.transitions.get(stage)

    def record(self, transition: ConfigGenerationTransition) -> None:
        if transition.status == "applied" and self.fail_first_applied_record:
            self.fail_first_applied_record = False
            raise RuntimeError("simulated receipt write interruption")
        self.transitions[transition.stage] = transition


def _plan(project: Path, *, new: bytes) -> ProjectGenerationPlan:
    config = (project / "config.yaml").resolve()
    manifest = (project / "generated" / "manifest.json").resolve()
    transaction = ProjectBundleTransaction(project)
    expected = {
        path: preimage.sha256
        for path, preimage in transaction.snapshot_preimages((config, manifest)).items()
    }
    return ProjectGenerationPlan(
        writes={config: new, manifest: b'{"generation":"new"}\n'},
        removals=(),
        sha256="sha256:" + "b" * 64,
        expected_preimages=expected,
        preimage_sha256="sha256:" + "c" * 64,
    )


def _paths(project: Path) -> ProjectPaths:
    generated = project / "generated"
    return ProjectPaths(
        config_path=project / "config.yaml",
        repo_root=project,
        deployments_dir=project.parent,
        project_dir=project,
        generated_dir=generated,
        infra_dir=generated / "infra",
        flux_dir=generated / "flux",
        reports_dir=generated / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def test_applied_config_generation_recovers_after_receipt_write_interruption(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    config = project / "config.yaml"
    manifest = project / "generated" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    config.write_bytes(b"old\n")
    manifest.write_bytes(b'{"generation":"old"}\n')
    plan = _plan(project, new=b"new\n")
    paths = _paths(project)
    store = _Store(fail_first_applied_record=True)

    with pytest.raises(RuntimeError, match="receipt write interruption"):
        apply_project_generation_transition(
            project_dir=project,
            config_path=config,
            owner="test-operation",
            stage="stage-a",
            store=store,
            build_plan=lambda: plan,
            assert_authority=lambda: None,
            current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
        )

    assert config.read_bytes() == b"new\n"
    assert store.transitions["stage-a"].status == "planned"
    recovered = apply_project_generation_transition(
        project_dir=project,
        config_path=config,
        owner="test-operation",
        stage="stage-a",
        store=store,
        build_plan=lambda: pytest.fail("postimage recovery must not replan"),
        assert_authority=lambda: None,
        current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
    )

    assert recovered.status == "applied"
    assert store.transitions["stage-a"].status == "applied"


def test_config_authority_rejects_a_foreign_postimage(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = project / "config.yaml"
    manifest = project / "generated" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    config.write_bytes(b"old\n")
    manifest.write_bytes(b'{"generation":"old"}\n')
    initial = _digest(b"old\n")
    paths = _paths(project)
    initial_snapshot = project_generation_snapshot_sha256(paths)
    store = _Store()
    apply_project_generation_transition(
        project_dir=project,
        config_path=config,
        owner="test-operation",
        stage="stage-a",
        store=store,
        build_plan=lambda: _plan(project, new=b"new\n"),
        assert_authority=lambda: None,
        current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
    )
    config.write_bytes(b"foreign-edit\n")

    with pytest.raises(SoperatorSafetyPauseError, match="last durable operation generation"):
        assert_config_authority_current(
            tuple(store.transitions.values()),
            initial_config_sha256=initial,
            initial_project_snapshot_sha256=initial_snapshot,
            current_config_sha256=file_sha256(config),
            current_project_snapshot_sha256=project_generation_snapshot_sha256(paths),
            current_project_generation_sha256=ProjectBundleTransaction(
                project
            ).current_generation_sha256(),
        )


def test_config_authority_rejects_foreign_generated_project_drift(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = project / "config.yaml"
    manifest = project / "generated" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    config.write_bytes(b"old\n")
    manifest.write_bytes(b'{"generation":"old"}\n')
    paths = _paths(project)
    initial_snapshot = project_generation_snapshot_sha256(paths)
    store = _Store()
    apply_project_generation_transition(
        project_dir=project,
        config_path=config,
        owner="test-operation",
        stage="stage-a",
        store=store,
        build_plan=lambda: _plan(project, new=b"new\n"),
        assert_authority=lambda: None,
        current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
    )
    manifest.write_bytes(b'{"generation":"foreign"}\n')

    with pytest.raises(SoperatorSafetyPauseError, match="generated project state"):
        assert_config_authority_current(
            tuple(store.transitions.values()),
            initial_config_sha256=_digest(b"old\n"),
            initial_project_snapshot_sha256=initial_snapshot,
            current_config_sha256=file_sha256(config),
            current_project_snapshot_sha256=project_generation_snapshot_sha256(paths),
            current_project_generation_sha256=ProjectBundleTransaction(
                project
            ).current_generation_sha256(),
        )


def _valid_transition(*, stage: str = "stage-a") -> ConfigGenerationTransition:
    def digest(value: str) -> str:
        return "sha256:" + value * 64

    return ConfigGenerationTransition(
        schema="nebius-cxcli.config-generation-transition.v2",
        transition_id=digest("1"),
        owner="test",
        stage=stage,
        status="planned",
        from_config_sha256=digest("a"),
        to_config_sha256=digest("b"),
        project_preimage_sha256=digest("c"),
        project_generation_sha256=digest("d"),
        project_postimage_sha256=digest("e"),
        planned_at="2026-08-30T00:00:00Z",
    )


def test_config_transition_payload_and_evidence_round_trip() -> None:
    transition = _valid_transition()

    restored = config_transition_from_payload(asdict(transition))

    assert restored == transition
    assert restored.evidence_sha256.startswith("sha256:")


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"schema": "wrong"}, "unsupported schema"),
        ({"status": "wrong"}, "invalid status"),
        ({"owner": ""}, "requires owner"),
        ({"transition_id": "sha256:not-hex"}, "invalid digest"),
        ({"status": "applied"}, "requires a timestamp"),
        ({"applied_at": "2026-08-30T00:00:01Z"}, "cannot be marked applied"),
    ),
)
def test_config_transition_validation_rejects_invalid_receipts(
    changes: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        validate_config_transition(replace(_valid_transition(), **changes))


def test_config_transition_chain_rejects_duplicate_broken_and_non_tail_plans() -> None:
    first = replace(_valid_transition(), status="applied", applied_at="now")
    second = replace(
        _valid_transition(stage="stage-b"),
        transition_id="sha256:" + "2" * 64,
        from_config_sha256=first.to_config_sha256,
        to_config_sha256="sha256:" + "f" * 64,
    )
    validate_config_transition_chain(
        (first, second),
        initial_config_sha256=first.from_config_sha256,
    )

    with pytest.raises(RuntimeError, match="repeats"):
        validate_config_transition_chain(
            (first, replace(first, transition_id="sha256:" + "3" * 64)),
            initial_config_sha256=first.from_config_sha256,
        )
    with pytest.raises(RuntimeError, match="broken"):
        validate_config_transition_chain(
            (first, replace(second, from_config_sha256="sha256:" + "9" * 64)),
            initial_config_sha256=first.from_config_sha256,
        )
    with pytest.raises(RuntimeError, match="planned tail"):
        validate_config_transition_chain(
            (replace(first, status="planned", applied_at=""), second),
            initial_config_sha256=first.from_config_sha256,
        )


def test_config_transition_upsert_is_immutable_and_advances_only_planned_tail() -> None:
    planned = _valid_transition()
    initial = planned.from_config_sha256
    assert upsert_config_transition((), planned, initial_config_sha256=initial) == (planned,)
    assert upsert_config_transition((planned,), planned, initial_config_sha256=initial) == (
        planned,
    )
    with pytest.raises(RuntimeError, match="changed during recovery"):
        upsert_config_transition(
            (planned,),
            replace(planned, owner="foreign"),
            initial_config_sha256=initial,
        )
    applied = replace(planned, status="applied", applied_at="now")
    assert upsert_config_transition((planned,), applied, initial_config_sha256=initial) == (
        applied,
    )
    with pytest.raises(RuntimeError, match="changed after"):
        upsert_config_transition(
            (applied,),
            replace(applied, applied_at="later"),
            initial_config_sha256=initial,
        )
    next_planned = replace(
        _valid_transition(stage="stage-b"),
        transition_id="sha256:" + "2" * 64,
        from_config_sha256=planned.to_config_sha256,
    )
    with pytest.raises(RuntimeError, match="already has a planned"):
        upsert_config_transition(
            (planned,),
            next_planned,
            initial_config_sha256=initial,
        )


def test_config_authority_accepts_exact_initial_snapshot() -> None:
    digest = "sha256:" + "a" * 64
    snapshot = "sha256:" + "b" * 64

    assert_config_authority_current(
        (),
        initial_config_sha256=digest,
        initial_project_snapshot_sha256=snapshot,
        current_config_sha256=digest,
        current_project_snapshot_sha256=snapshot,
        current_project_generation_sha256=None,
    )


def test_applied_transition_rejects_generated_mismatch_and_config_rollback(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    config = project / "config.yaml"
    manifest = project / "generated" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    config.write_bytes(b"old\n")
    manifest.write_bytes(b"old\n")
    paths = _paths(project)
    store = _Store()
    apply_project_generation_transition(
        project_dir=project,
        config_path=config,
        owner="test",
        stage="stage-a",
        store=store,
        build_plan=lambda: _plan(project, new=b"new\n"),
        assert_authority=lambda: None,
        current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
    )
    manifest.write_bytes(b"foreign\n")
    with pytest.raises(SoperatorSafetyPauseError, match="generated state does not"):
        apply_project_generation_transition(
            project_dir=project,
            config_path=config,
            owner="test",
            stage="stage-a",
            store=store,
            build_plan=lambda: pytest.fail("must not replan"),
            assert_authority=lambda: None,
            current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
        )

    config.write_bytes(b"old\n")
    with pytest.raises(SoperatorSafetyPauseError, match="rolled back"):
        apply_project_generation_transition(
            project_dir=project,
            config_path=config,
            owner="test",
            stage="stage-a",
            store=store,
            build_plan=lambda: pytest.fail("must not replan"),
            assert_authority=lambda: None,
            current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
        )


def test_planned_transition_rejects_replanning_and_missing_config_ownership(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    config = project / "config.yaml"
    manifest = project / "generated" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    config.write_bytes(b"old\n")
    manifest.write_bytes(b"old\n")
    paths = _paths(project)
    store = _Store()

    with pytest.raises(RuntimeError, match="pause before commit"):
        apply_project_generation_transition(
            project_dir=project,
            config_path=config,
            owner="test",
            stage="stage-a",
            store=store,
            build_plan=lambda: _plan(project, new=b"new\n"),
            assert_authority=lambda: (_ for _ in ()).throw(RuntimeError("pause before commit")),
            current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
        )
    with pytest.raises(SoperatorSafetyPauseError, match="differs from its planned"):
        apply_project_generation_transition(
            project_dir=project,
            config_path=config,
            owner="test",
            stage="stage-a",
            store=store,
            build_plan=lambda: _plan(project, new=b"different\n"),
            assert_authority=lambda: None,
            current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
        )

    empty_store = _Store()
    bad_plan = ProjectGenerationPlan(
        writes={manifest.resolve(): b"new\n"},
        removals=(),
        sha256="sha256:" + "b" * 64,
        expected_preimages={manifest.resolve(): _digest(b"old\n")},
        preimage_sha256="sha256:" + "c" * 64,
    )
    with pytest.raises(SoperatorSafetyPauseError, match="does not own config.yaml"):
        apply_project_generation_transition(
            project_dir=project,
            config_path=config,
            owner="test",
            stage="stage-b",
            store=empty_store,
            build_plan=lambda: bad_plan,
            assert_authority=lambda: None,
            current_project_snapshot_sha256=lambda: project_generation_snapshot_sha256(paths),
        )
