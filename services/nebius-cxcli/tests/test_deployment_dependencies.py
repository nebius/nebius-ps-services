from __future__ import annotations

import copy

import pytest

from nebius_cxcli.deployment_dependencies import (
    admit_recreations,
    prior_deletions,
    recreation_candidates,
)
from nebius_cxcli.deployment_plan import assert_stage_plan, terraform_admission


def row(address, actions, *, resource_type="nebius_iam_v1_service_account", identity="old"):
    return {
        "address": address,
        "type": resource_type,
        "mode": "managed",
        "change": {
            "actions": actions,
            "before": {"metadata": {"id": identity}},
            "after": {"metadata": {"id": identity, "name": address}},
            "after_unknown": {},
        },
    }


def test_recreation_admits_noop_dependency_and_propagated_unknowns():
    original = {"resource_changes": [row("account", ["delete"]), row("group", ["delete"])]}
    inventory = terraform_admission(original, include_noop=True)
    deleted = prior_deletions("retire", inventory)
    baseline = {"resource_changes": [row("account", ["no-op"]), row("group", ["update"])]}
    required = recreation_candidates(terraform_admission(baseline, include_noop=True), deleted)
    assert {item.address for item in required} == {"account", "group"}
    diagnostic = copy.deepcopy(baseline)
    for item in diagnostic["resource_changes"]:
        item["change"].update(
            actions=["delete", "create"], after_unknown={"metadata": {"id": True}}
        )
        item["change"]["after"]["metadata"]["id"] = None
    admitted = admit_recreations(
        terraform_admission(diagnostic), terraform_admission(baseline), required
    )
    actual = copy.deepcopy(admitted)
    for item in actual["resource_changes"]:
        assert item["change"]["actions"] == ["create"]
        item["change"]["after"]["metadata"]["id"] = "new"
        item["change"]["after_unknown"] = {}
    assert_stage_plan(admitted, actual)


def test_diagnostic_cannot_authorize_survivor_destruction():
    deleted = prior_deletions(
        "retire",
        terraform_admission({"resource_changes": [row("account", ["delete"])]}, include_noop=True),
    )
    baseline = {"resource_changes": [row("account", ["no-op"]), row("survivor", ["no-op"])]}
    diagnostic = {
        "resource_changes": [
            row("account", ["delete", "create"]),
            row("survivor", ["delete", "create"]),
        ]
    }
    with pytest.raises(ValueError, match="survivor"):
        admit_recreations(terraform_admission(diagnostic), terraform_admission(baseline), deleted)


def test_missing_original_identity_blocks_retirement_admission():
    original = {"resource_changes": [row("account", ["delete"], identity="")]}
    with pytest.raises(ValueError, match="identity"):
        prior_deletions("retire", terraform_admission(original, include_noop=True))


def test_incarnation_proof_allows_partial_growth_but_rejects_old_or_changed_id(tmp_path):
    from nebius_cxcli.deployment_dependencies import DependencyJournal, resource_identity

    references = prior_deletions(
        "retire",
        terraform_admission({"resource_changes": [row("account", ["delete"])]}, include_noop=True),
    )
    path = tmp_path / "dependencies.json"
    journal = DependencyJournal(path, "sha256:" + "1" * 64)
    with pytest.raises(RuntimeError, match="Missing predecessor"):
        journal.require(references, {})
    journal.deleted(references, {})
    with pytest.raises(RuntimeError, match="before admitted creation"):
        journal.require(references, {"account": resource_identity({"id": "new"})})
    journal.require(references, {})
    journal.begin_creation(references)
    journal = DependencyJournal(path, "sha256:" + "1" * 64)
    journal.require(references, {"account": resource_identity({"id": "new"})})
    journal.require(references, {"account": resource_identity({"id": "new"})})
    with pytest.raises(RuntimeError, match="Original retired"):
        journal.require(references, {"account": resource_identity({"id": "old"})})
    with pytest.raises(RuntimeError, match="identity changed"):
        journal.require(references, {"account": resource_identity({"id": "foreign"})})


@pytest.mark.integration
def test_native_terraform_recreation_preserves_dependency_unknowns(tmp_path):
    """Local built-in-provider fixture: no cloud provider, backend, or provisioner."""
    import json
    import shutil
    import subprocess

    terraform = shutil.which("terraform")
    if not terraform:
        pytest.skip("Terraform executable is unavailable")

    def command(*args):
        result = subprocess.run(
            [terraform, *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )
        return result.stdout

    def write(enabled, version):
        (tmp_path / "main.tf").write_text(
            """terraform { required_version = ">= 1.4" }
resource "terraform_data" "account" {
  for_each = ENABLED ? toset(["worker"]) : toset([])
  input = "worker-account"
}
resource "terraform_data" "group" {
  for_each = ENABLED ? toset(["worker"]) : toset([])
  triggers_replace = [terraform_data.account[each.key].id, VERSION]
}
""".replace("ENABLED", str(enabled).lower()).replace("VERSION", str(version))
        )

    def plan(name, *options):
        command("plan", "-input=false", "-no-color", "-out=" + name, *options)
        return json.loads(command("show", "-json", name))

    write(True, 1)
    command("init", "-backend=false", "-input=false", "-no-color")
    command("apply", "-auto-approve", "-input=false", "-no-color")
    write(False, 1)
    retirement = plan("retire.tfplan")
    references = prior_deletions("retire", terraform_admission(retirement, include_noop=True))
    write(True, 2)
    baseline = plan("grow-original.tfplan")
    assert any(
        row["address"].startswith("terraform_data.account")
        and row["change"]["actions"] == ["no-op"]
        for row in baseline["resource_changes"]
    )
    required = recreation_candidates(terraform_admission(baseline, include_noop=True), references)
    diagnostic = plan("diagnostic.tfplan", *("-replace=" + item.address for item in required))
    admission = admit_recreations(
        terraform_admission(diagnostic), terraform_admission(baseline), required
    )
    (tmp_path / "diagnostic.tfplan").unlink()
    # Apply only the frozen retirement and then a normal fresh growth plan.
    write(False, 1)
    command("apply", "-input=false", "retire.tfplan")
    write(True, 2)
    actual = plan("grow.tfplan")
    assert_stage_plan(admission, terraform_admission(actual))
    command("apply", "-input=false", "grow.tfplan")
    assert not [
        row
        for row in plan("final.tfplan")["resource_changes"]
        if row["change"]["actions"] != ["no-op"]
    ]
