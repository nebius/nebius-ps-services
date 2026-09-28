"""Passive worker evidence stays outside desired configuration and survives recovery."""

from types import SimpleNamespace

import pytest

from nebius_cxcli.deployment_recovery import capture_execution_cache, restore_execution_cache
from nebius_cxcli.operation_config_authority import assert_config_authority_current
from nebius_cxcli.paths import resolve_project_paths
from nebius_cxcli.render import (
    build_project_generation_plan,
    project_generation_snapshot_sha256,
    promote_staged_generated_paths,
    staged_generated_paths,
)
from nebius_cxcli.soperator_passive_checks import PassiveDiagnostics


@pytest.fixture(
    params=[
        "soperator-checks-0123456789abcdef",
        "soperator-campaign-checks-0123456789abcdef-target",
    ]
)
def worker_receipts(tmp_path, request):
    root = tmp_path / "execution"
    config = root / "tenant/project/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("cluster: lab\n")
    paths = resolve_project_paths(config)
    snapshot = project_generation_snapshot_sha256(paths)

    def authority():
        assert_config_authority_current(
            [],
            initial_config_sha256="sha256:" + "a" * 64,
            initial_project_snapshot_sha256=snapshot,
            current_config_sha256="sha256:" + "a" * 64,
            current_project_snapshot_sha256=project_generation_snapshot_sha256(paths),
            current_project_generation_sha256=None,
        )

    passive = PassiveDiagnostics(
        SimpleNamespace(
            path=paths.reports_dir / (request.param + ".json"),
            state={"operation": "operation-a"},
            authority=authority,
        )
    )
    return root, paths, passive, snapshot


def test_worker_receipts_preserve_authority_and_render_promotion(worker_receipts):
    _root, paths, passive, snapshot = worker_receipts
    for worker in ("worker-0", "worker-1"):
        passive._worker_receipt(worker, "policy-a", {"identity": worker, "boot": "boot-a"})
        passive.checks.authority()
        assert project_generation_snapshot_sha256(paths) == snapshot
        assert passive._worker_path(worker).stat().st_mode & 0o777 == 0o600

    staged = staged_generated_paths(paths)
    staged.infra_dir.mkdir(parents=True)
    (staged.infra_dir / "main.tf").write_text("terraform {}\n")
    plan = build_project_generation_plan(
        final_paths=paths,
        staged_paths=staged,
        config_path=paths.config_path,
        config_content=paths.config_path.read_text(),
    )
    for worker in ("worker-0", "worker-1"):
        assert passive._worker_path(worker) not in plan.removals
        assert passive._worker_path(worker) not in plan.writes
    promote_staged_generated_paths(staged, paths)
    for worker in ("worker-0", "worker-1"):
        assert passive._worker_receipt(worker, "policy-a") == {"identity": worker, "boot": "boot-a"}

    rendered = project_generation_snapshot_sha256(paths)
    (paths.infra_dir / "main.tf").write_text("changed desired configuration\n")
    assert project_generation_snapshot_sha256(paths) != rendered


def test_worker_receipts_survive_authenticated_recovery(worker_receipts, tmp_path):
    root, _paths, passive, _snapshot = worker_receipts
    evidence = {"identity": "worker-0", "boot": "boot-a"}
    passive._worker_receipt("worker-0", "policy-a", evidence)
    relative = passive._worker_path("worker-0").relative_to(root)
    cache = capture_execution_cache(root)
    assert relative.as_posix() in cache
    restored = tmp_path / "restored"
    restore_execution_cache(restored, cache)
    passive.checks.path = restored / passive.checks.path.relative_to(root)
    assert passive._worker_receipt("worker-0", "policy-a") == evidence
    assert passive._worker_path("worker-0").stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="different execution"):
        passive._worker_receipt("worker-0", "different-policy")
    passive.checks.state["operation"] = "different-operation"
    with pytest.raises(RuntimeError, match="different execution"):
        passive._worker_receipt("worker-0", "policy-a")
