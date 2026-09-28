"""Behavioral cost and invalidation guarantees for invocation-local preparation."""

from types import SimpleNamespace

import pytest

from nebius_cxcli import terraform_ops
from nebius_cxcli.deployment_preparation import prepared_deployment
from nebius_cxcli.deployment_retry import TransientReadError, retry_read


def test_input_fingerprint_separates_file_names_and_contents(tmp_path):
    from nebius_cxcli.deployment_preparation import terraform_inputs

    original, changed = tmp_path / "original", tmp_path / "changed"
    original.mkdir()
    changed.mkdir()
    # Both trees contain valid Terraform comments, but used to encode as the
    # same concatenation of relative names and file contents.
    (original / "a.tf").write_text("# ")
    (original / "b.tf").write_text("# b.tf")
    (changed / "a.tf").write_text("# b.tf# ")
    (changed / "b.tf").write_text("")
    assert terraform_inputs(original) != terraform_inputs(changed)


def test_initialization_and_validation_reuse_requires_live_unchanged_root(tmp_path, monkeypatch):
    calls = []
    root = tmp_path / "infra"
    root.mkdir()
    (root / "main.tf").write_text('module "x" { source = "./a" }')
    monkeypatch.setattr(terraform_ops, "_require_terraform", lambda: "terraform")

    def run(command, *, cwd, **kwargs):
        calls.append((str(cwd), command[1]))
        (cwd / ".terraform").mkdir(exist_ok=True)
        (cwd / ".terraform.lock.hcl").write_text("locked")

    monkeypatch.setattr(terraform_ops, "_run", run)
    with prepared_deployment():
        terraform_ops.terraform_validate(root)
        terraform_ops.terraform_validate(root)
        assert [item[1] for item in calls] == ["init", "validate"]
        # Changing values needs validation, not provider/module installation.
        (root / "terraform.tfvars").write_text("nodes = 2")
        terraform_ops.terraform_validate(root)
        assert [item[1] for item in calls] == ["init", "validate", "validate"]
        (root / "main.tf").write_text('module "x" { source = "./b" }')
        terraform_ops.terraform_validate(root)
        assert [item[1] for item in calls][-2:] == ["init", "validate"]
        (root / ".terraform").rmdir()
        terraform_ops.terraform_validate(root)
        assert [item[1] for item in calls][-2:] == ["init", "validate"]
        other = tmp_path / "other"
        other.mkdir()
        (other / "main.tf").write_bytes((root / "main.tf").read_bytes())
        terraform_ops.terraform_validate(other)
        assert calls[-2][0] == str(other)
    terraform_ops.terraform_validate(root)
    assert [item[1] for item in calls][-2:] == ["init", "validate"]


def test_failed_preparation_is_never_cached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(terraform_ops, "_require_terraform", lambda: "terraform")

    def fail(*args, **kwargs):
        calls.append(1)
        (tmp_path / ".terraform").mkdir(exist_ok=True)
        raise RuntimeError("provider registry unavailable")

    monkeypatch.setattr(terraform_ops, "_run", fail)
    with prepared_deployment():
        for _ in range(2):
            with pytest.raises(RuntimeError):
                terraform_ops.terraform_init(tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize("failed_reinitialization", [False, True])
def test_reinitialization_invalidates_only_its_root_even_if_identity_is_recycled(
    tmp_path, monkeypatch, failed_reinitialization
):
    from nebius_cxcli import deployment_preparation

    roots = [tmp_path / name for name in ("first", "second")]
    for root in roots:
        root.mkdir()
        (root / "main.tf").write_text("# fixture")
    calls = []
    fail_next_init = False
    monkeypatch.setattr(terraform_ops, "_require_terraform", lambda: "terraform")
    # Linux can recycle the removed directory's inode. Model equal pre/post
    # fingerprints without depending on the test host's allocation behavior.
    monkeypatch.setattr(
        deployment_preparation,
        "initialized_identity",
        lambda root: "recycled-identity" if (root / ".terraform").is_dir() else None,
    )

    def run(command, *, cwd, **kwargs):
        nonlocal fail_next_init
        calls.append((cwd.name, command[1]))
        (cwd / ".terraform").mkdir(exist_ok=True)
        if command[1] == "init" and fail_next_init:
            fail_next_init = False
            raise RuntimeError("initialization interrupted")

    monkeypatch.setattr(terraform_ops, "_run", run)
    with prepared_deployment():
        for root in roots:
            terraform_ops.terraform_validate(root)
        (roots[0] / ".terraform").rmdir()
        if failed_reinitialization:
            fail_next_init = True
            with pytest.raises(RuntimeError, match="initialization interrupted"):
                terraform_ops.terraform_validate(roots[0], extra_env={"TF_WORKSPACE": "alternate"})
        terraform_ops.terraform_validate(roots[0])
        terraform_ops.terraform_validate(roots[1])
        terraform_ops.terraform_validate(roots[0])
    first = [command for root, command in calls if root == "first"]
    expected = ["init", "validate", "init"]
    if failed_reinitialization:
        expected.append("init")
    assert first == [*expected, "validate"]
    assert [command for root, command in calls if root == "second"] == ["init", "validate"]


@pytest.mark.parametrize("changed", ["backend", "tool", "environment", "provider-lock", "module"])
def test_preparation_repeats_after_execution_dependency_changes(tmp_path, monkeypatch, changed):
    import json

    root = tmp_path / "infra"
    root.mkdir()
    tool = tmp_path / "terraform"
    tool.write_text("original tool")
    module = tmp_path / "module"
    module.mkdir()
    (module / "main.tf").write_text("original module")
    calls = []
    monkeypatch.setattr(terraform_ops, "_require_terraform", lambda: str(tool))

    def run(command, *, cwd, **kwargs):
        calls.append(command)
        modules = cwd / ".terraform" / "modules"
        modules.mkdir(parents=True, exist_ok=True)
        (modules / "modules.json").write_text(
            json.dumps({"Modules": [{"Source": "../module", "Dir": str(module)}]})
        )

    monkeypatch.setattr(terraform_ops, "_run", run)
    with prepared_deployment():
        terraform_ops.terraform_init(root, backend=False)
        terraform_ops.terraform_init(root, backend=False)
        assert len(calls) == 1
        if changed == "tool":
            tool.write_text("replacement tool")
        elif changed == "environment":
            monkeypatch.setenv("TF_CLI_ARGS_init", "-reconfigure")
        elif changed == "provider-lock":
            (root / ".terraform.lock.hcl").write_text("different provider lock")
        elif changed == "module":
            (module / "main.tf").write_text("changed module")
        terraform_ops.terraform_init(root, backend=changed == "backend")
        assert len(calls) == 2


def test_transient_reads_have_one_three_attempt_budget_even_when_nested():
    calls, sleeps = [], []

    def fail():
        calls.append(1)
        raise TransientReadError("transport")

    def nested():
        return retry_read(fail, sleep=lambda _: pytest.fail("nested backoff"))

    with pytest.raises(TransientReadError):
        retry_read(nested, sleep=sleeps.append, jitter=lambda: 0.1)
    assert len(calls) == 3
    assert sleeps == [1.1, 2.1]
    # A new invocation has a new allowance, irrespective of stored history.
    with pytest.raises(TransientReadError):
        retry_read(fail, sleep=lambda _: None)
    assert len(calls) == 6


def test_unknown_failure_is_not_retried():
    with pytest.raises(ValueError, match="identity"):
        retry_read(
            lambda: (_ for _ in ()).throw(ValueError("identity")),
            sleep=lambda _: pytest.fail("retry"),
        )


def test_transient_read_can_recover_without_repeating_its_caller():
    calls, sleeps = [], []

    def read():
        calls.append(1)
        if len(calls) < 3:
            raise TransientReadError("transport")
        return "ready"

    assert retry_read(read, sleep=sleeps.append, jitter=lambda: 1) == "ready"
    assert sleeps == [1.25, 2.25]


def test_observation_admission_reuses_raw_plan_but_invalidates_changed_inputs(tmp_path):
    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
    from nebius_cxcli.deployment_preparation import TerraformObservation, terraform_inputs

    root = tmp_path / "infra"
    root.mkdir()
    (root / "main.tf").write_text("original")
    executor = object.__new__(_CliDeploymentExecutor)
    executor.paths = SimpleNamespace(infra_dir=root)
    raw = {
        "resource_changes": [
            {"address": "example.one", "change": {"actions": ["no-op"], "before": {}, "after": {}}}
        ]
    }
    executor._observation = TerraformObservation(root.resolve(), terraform_inputs(root), raw)
    plans = []
    executor._terraform_plan = lambda **kwargs: (plans.append(kwargs), {"resource_changes": []})
    assert executor._observed_terraform_admission()["resource_changes"] == []
    assert not plans
    (root / "main.tf").write_text("changed")
    executor._observed_terraform_admission()
    assert len(plans) == 1


def test_provider_cache_symlink_is_part_of_initialization_identity(tmp_path):
    from nebius_cxcli.deployment_preparation import initialized_identity

    linked = tmp_path / ".terraform" / "providers"
    linked.mkdir(parents=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    binary = cache / "terraform-provider-example"
    binary.write_text("binary")
    (linked / "example").symlink_to(cache, target_is_directory=True)
    before = initialized_identity(tmp_path)
    binary.unlink()
    assert initialized_identity(tmp_path) != before
    binary.write_text("replacement-binary")
    assert initialized_identity(tmp_path) != before
    binary.unlink()
    cache.rmdir()
    assert initialized_identity(tmp_path) is None


def test_frozen_snapshot_conflict_stops_before_release_preparation(monkeypatch):
    import json

    from nebius_cxcli import cli
    from nebius_cxcli.soperator_operation import SOPERATOR_OPERATION_ANCHOR_SCHEMA
    from nebius_cxcli.soperator_release_preparation import assert_scheduling_inputs

    operation = "sha256:" + "a" * 64
    monkeypatch.setattr(
        cli,
        "_read_soperator_slurm_cluster_journal",
        lambda **kw: (
            {
                "schema": cli.SOPERATOR_SLURM_RECOVERY_SCHEMA,
                "status": "safety-paused",
                "targetRef": "target",
                "operationSpecSha256": operation,
                "policy": {"jobPolicy": "fail"},
            },
            "1",
        ),
    )
    anchor = {
        "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
        "clusterId": "cluster",
        "kubernetesUid": "uid",
        "targetRef": "target",
        "operationSpecSha256": operation,
        "operationId": operation,
        "releaseSnapshotSha256": "sha256:" + "b" * 64,
    }
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl_cluster",
        lambda *a, **kw: SimpleNamespace(stdout=json.dumps({"data": anchor})),
    )
    with pytest.raises(cli.SoperatorSafetyPauseError, match="different frozen release snapshot"):
        assert_scheduling_inputs(
            cli,
            target_ref="target",
            cluster_id="cluster",
            kubernetes_uid="uid",
            kube_context="context",
            extra_env={},
            snapshot_sha256="sha256:" + "c" * 64,
            policy={"jobPolicy": "fail"},
        )


def test_infrastructure_drift_blocks_release_before_any_application_mutation(tmp_path):
    from io import StringIO

    from rich.console import Console

    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
    from nebius_cxcli.deployment_plan import DeploymentAction, DeploymentStageKind

    executor = object.__new__(_CliDeploymentExecutor)
    executor.cli = SimpleNamespace(progress_console=Console(file=StringIO()))
    executor.plan = SimpleNamespace(target_ref="target", action=DeploymentAction.RECONCILE)
    executor.campaign_intent = None
    executor._terraform_plan = lambda **kw: (
        None,
        {"resource_changes": [{"address": "extra", "change": {"actions": ["delete"]}}]},
    )
    executor._release = lambda **kw: pytest.fail("release mutated before scope check")
    admission = SimpleNamespace(
        stage=SimpleNamespace(name=DeploymentStageKind.RECONCILE),
        terraform={"resource_changes": []},
    )
    with pytest.raises((ValueError, RuntimeError)):
        executor.execute(admission, recovering=False)
