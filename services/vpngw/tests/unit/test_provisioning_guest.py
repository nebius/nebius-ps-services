from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.compute import v1 as compute
from test_ordinary_apply import empty_routes, planning_result
from test_ordinary_apply import release_wheel as release_wheel
from test_provisioning_execute import bootstrap, cloud_fixture
from test_provisioning_journal import plan as resource_plan

from nebius_vpngw import apply_approval
from nebius_vpngw.deploy import ordinary_apply
from nebius_vpngw.deploy import provisioning_guest as guest
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.provisioning_execute import execute
from nebius_vpngw.deploy.provisioning_journal import ProvisioningJournal


@pytest.fixture
def scope(tmp_path, monkeypatch, release_wheel):
    instance = NS(hostname="gateway-0", config_yaml="gateway: {}\nconnections: []\n")
    manifest = ordinary_apply.artifact(release_wheel, instance.config_yaml)
    desired = resource_plan()
    desired["artifact_sha256"] = manifest["wheel_sha256"]
    descriptor = guest.descriptor(desired, instance)
    envelope = CommandPlan(
        "project-a",
        "gateway",
        "a" * 64,
        manifest["wheel_sha256"],
        (
            Effect(
                "provisioning",
                "ordinary-provisioning",
                "create",
                ("gateway-0",),
                apply_approval.digest(desired),
                "c" * 64,
                True,
            ),
            Effect(
                "guest:gateway-0",
                "ordinary-provisioned-runtime",
                "apply",
                ("gateway-0",),
                apply_approval.digest(descriptor),
                "d" * 64,
                True,
                ("provisioning",),
            ),
        ),
    )
    cloud, submissions = cloud_fixture(monkeypatch)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "resources")
    journal = guest.GuestJournal("project-a", "gateway", "gateway-0", root=tmp_path / "guest")
    observed = {
        "files": {},
        "assets": {},
        "asset_modes": {},
        "boot_id": "boot",
        "package_version": None,
        "config_sha256": None,
        "verified": False,
        "agent_state": "inactive",
        "runtime": {"ready": True},
        "runtime_plan": {"closure": "original"},
        "runtime_pending": None,
        "dependencies": {},
        "requirements": {},
        "markers": {},
        "route_ownership": empty_routes(),
    }
    inspected = []

    def remote(_ssh, target, _instance, _local, request):
        assert target == "203.0.113.10"
        assert request["action"] == "inspect"
        inspected.append(deepcopy(request))
        current = deepcopy(observed)
        if request["manifest"].get("command_approval"):
            current["runtime_plan"] = request["manifest"]["runtime_plan"]
        return planning_result(current)

    monkeypatch.setattr(ordinary_apply, "remote", remote)
    ssh = NS(_build_wheel=lambda **kw: release_wheel, _find_project_root=lambda: None)
    with CommandJournal("project-a", "gateway", root=tmp_path / "command") as command:
        command.begin(envelope, intents={"provisioning": desired, "guest:gateway-0": descriptor})
        command.started("provisioning")
        receipt = execute(object(), child, command, desired, bootstrap=bootstrap)
        command.verified("provisioning", receipt)
        cloud["compute-created"].status = compute.InstanceStatus(
            state=compute.InstanceStatus.InstanceState.RUNNING,
            network_interfaces=[
                compute.NetworkInterfaceStatus(
                    public_ip_address=compute.PublicIPAddressStatus(address="203.0.113.10/32")
                )
            ],
        )
        command.started("guest:gateway-0")
        yield NS(
            command=command,
            child=child,
            journal=journal,
            instance=instance,
            ssh=ssh,
            observed=observed,
            manifest=manifest,
            inspected=inspected,
            cloud=cloud,
            submissions=submissions,
        )


def prepare(scope):
    return guest.prepare(
        object(), scope.command, scope.child, scope.journal, scope.ssh, scope.instance, {}
    )


def healthy(scope, manifest):
    scope.observed.update(
        files=manifest["files"],
        package_version=manifest["version"],
        verified=True,
        agent_state="active",
        config_sha256=manifest["config_sha256"],
        dependencies=manifest["expected_dependencies"],
        requirements=manifest["expected_requirements"],
        assets={path: value["sha256"] for path, value in manifest["assets"].items()},
        asset_modes={path: [value["mode"], 0, 0] for path, value in manifest["assets"].items()},
        runtime_pending=None,
        runtime_completion={
            "approval": manifest["command_approval"],
            "manifest": apply_approval.manifest_identity(manifest),
        },
    )


def test_selected_new_guest_resumes_original_closure_then_completes_without_writes(
    scope, monkeypatch
):
    selected = prepare(scope)
    manifest = deepcopy(selected.manifest)
    selected.close()
    before = scope.journal.path.read_bytes()
    monkeypatch.setattr(
        ordinary_apply, "resolve_dependencies", lambda *a: pytest.fail("re-resolved packages")
    )
    monkeypatch.setattr(ordinary_apply, "restore_dependencies", lambda *a: [])
    scope.observed["runtime_plan"] = {"closure": "new repository candidates"}
    resumed = prepare(scope)
    assert resumed.manifest == manifest
    assert before == scope.journal.path.read_bytes()
    resumed.close()
    # A lost reply after installation is settled by its exact remote receipt.
    healthy(scope, manifest)
    final = prepare(scope)
    receipt = guest.complete(scope.command, scope.child, scope.journal, final)
    scope.command.verified("guest:gateway-0", receipt)
    scope.command.complete()
    final.close()
    before = tuple(j.path.read_bytes() for j in (scope.command, scope.child, scope.journal))
    verified = prepare(scope)
    assert guest.complete(scope.command, scope.child, scope.journal, verified) == receipt
    verified.close()
    assert before == tuple(j.path.read_bytes() for j in (scope.command, scope.child, scope.journal))
    assert [item[0] for item in scope.submissions] == ["disk", "compute"]
    assert "private-fixture-data" not in scope.journal.path.read_text()


@pytest.mark.parametrize(
    "damage",
    [
        "bootstrap",
        "resource",
        "configured",
        "pending",
        "completed",
        "wrong-config",
        "wrong-artifact",
    ],
)
def test_unapproved_guest_state_cannot_select_packages(scope, damage, monkeypatch):
    if damage == "bootstrap":
        scope.cloud["compute-created"].spec.cloud_init_user_data = "changed"
    elif damage == "resource":
        scope.cloud["compute-created"].metadata.id = "same-name-unrelated"
    elif damage == "configured":
        scope.observed["config_sha256"] = "e" * 64
    elif damage == "pending":
        scope.observed["operation"] = {"recoverable": True, "id": "foreign"}
    elif damage == "completed":
        scope.observed["runtime_completion"] = {"manifest": "foreign"}
    elif damage == "wrong-config":
        scope.instance.config_yaml = "gateway: {}\nconnections: []\n#changed\n"
    else:
        original = ordinary_apply.artifact
        monkeypatch.setattr(
            ordinary_apply, "artifact", lambda *a: {**original(*a), "wheel_sha256": "f" * 64}
        )
    with pytest.raises(RuntimeError):
        prepare(scope)
    assert scope.journal.snapshot() is None


@pytest.mark.parametrize("damage", ["predecessor", "terminal", "new-drift", "selection-loss"])
def test_selected_or_completed_guest_rejects_unexplained_drift(scope, monkeypatch, damage):
    selected = prepare(scope)
    manifest = deepcopy(selected.manifest)
    selected.close()
    monkeypatch.setattr(ordinary_apply, "restore_dependencies", lambda *a: [])
    if damage == "predecessor":
        scope.observed["boot_id"] = "another-boot"
    else:
        healthy(scope, manifest)
        if damage == "terminal":
            scope.observed["runtime_completion"]["manifest"] = "f" * 64
        else:
            final = prepare(scope)
            receipt = guest.complete(scope.command, scope.child, scope.journal, final)
            scope.command.verified("guest:gateway-0", receipt)
            final.close()
            if damage == "new-drift":
                scope.observed["agent_state"] = "inactive"
            else:
                scope.journal.path.unlink()
    before = scope.journal.path.read_bytes() if scope.journal.path.exists() else None
    with pytest.raises(RuntimeError):
        prepare(scope)
    assert before == (scope.journal.path.read_bytes() if scope.journal.path.exists() else None)


def prior_creation(scope, *, phase="complete", same_creation=False):
    prepared = prepare(scope)
    prepared.close()
    with scope.journal:
        selected = scope.journal.state["transaction"]
        selected["phase"] = phase
        selected["authority"]["execution"] = "e" * 32
        if not same_creation:
            selected["authority"]["creation"] = "f" * 64
        # The successor must resolve its own closure, not restore this completed one.
        selected["manifest"]["expected_dependencies"]["unrelated-old-package"] = "1.0"
        scope.journal.save()
    scope.inspected.clear()
    return scope.journal.path.read_bytes()


def test_new_creation_archives_completed_guest_only_after_clean_inspection(scope):
    previous = prior_creation(scope)
    prepared = prepare(scope)
    try:
        assert "unrelated-old-package" not in prepared.manifest["expected_dependencies"]
        assert scope.inspected and all(
            not call["manifest"].get("command_approval") for call in scope.inspected
        )
        current = scope.journal.snapshot()["transaction"]
        assert current["phase"] == "selected"
        assert (
            current["authority"]["execution"] == scope.command.state["transaction"]["execution_id"]
        )
        archived = list((scope.journal.root / "history").glob("*.json"))
        assert len(archived) == 1 and archived[0].read_bytes() == previous
    finally:
        prepared.close()


@pytest.mark.parametrize(
    "damage",
    [
        "pending",
        "same-creation",
        "same-execution",
        "bootstrap",
        "configured",
        "operation",
        "completion",
    ],
)
def test_prior_guest_selection_is_preserved_without_new_clean_creation(scope, damage):
    previous = prior_creation(
        scope,
        phase="selected" if damage == "pending" else "complete",
        same_creation=damage == "same-creation",
    )
    if damage == "same-execution":
        with scope.journal:
            scope.journal.state["transaction"]["authority"]["execution"] = scope.command.state[
                "transaction"
            ]["execution_id"]
            scope.journal.save()
        previous = scope.journal.path.read_bytes()
    elif damage == "bootstrap":
        scope.cloud["compute-created"].spec.cloud_init_user_data = "foreign"
    elif damage == "configured":
        scope.observed["config_sha256"] = "a" * 64
    elif damage == "operation":
        scope.observed["operation"] = {"recoverable": True, "id": "foreign"}
    elif damage == "completion":
        scope.observed["runtime_completion"] = {"manifest": "foreign"}
    with pytest.raises(RuntimeError):
        prepare(scope)
    assert scope.journal.path.read_bytes() == previous
    assert not (scope.journal.root / "history").exists()
