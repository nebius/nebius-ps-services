"""Resume before staging without consulting a different package resolution."""

import base64
import io
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.apply_approval import digest, manifest_identity
from nebius_vpngw.deploy import ha_command, ordinary_apply, vm_ha_package
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus


@pytest.fixture
def packages(monkeypatch, tmp_path):
    raw = b"approved dependency bytes"
    filename = "example_dep-1.2-py3-none-any.whl"
    wheel = tmp_path / "product.whl"
    wheel.write_bytes(b"approved product bytes")
    manifest = {
        "wheel_sha256": ordinary_apply.sha(wheel.read_bytes()),
        "config_sha256": "a" * 64,
        "files": {},
        "assets": {},
        "version": "test",
        "requirements": [],
        "runtime_plan": {"approved": "exact apt closure"},
        "dependency_wheels": {filename: ordinary_apply.sha(raw)},
        "expected_dependencies": {"example-dep": "1.2"},
        "expected_requirements": {"example-dep": []},
    }
    observed = {
        "runtime_plan": manifest["runtime_plan"],
        "runtime_pending": None,
        "files": {},
        "assets": {},
        "asset_modes": {},
        "package_version": "old",
        "wheel_tags": ["py3-none-any"],
    }
    monkeypatch.setattr(vm_ha_package, "artifact", lambda *args: deepcopy(manifest))
    monkeypatch.setattr(vm_ha_package, "package_predecessor", deepcopy)
    monkeypatch.setattr(
        ordinary_apply, "resolve_dependencies", lambda *args: pytest.fail("resolved new packages")
    )
    requests = []

    def download(url, **kwargs):
        requests.append(url)
        if url == "https://pypi.org/pypi/example-dep/1.2/json":
            return io.BytesIO(
                json.dumps(
                    {
                        "urls": [
                            {
                                "filename": filename,
                                "packagetype": "bdist_wheel",
                                "yanked": False,
                                "digests": {"sha256": ordinary_apply.sha(raw)},
                                "url": "https://files.pythonhosted.org/packages/approved.whl",
                            }
                        ]
                    }
                ).encode()
            )
        assert url == "https://files.pythonhosted.org/packages/approved.whl"
        return io.BytesIO(raw)

    monkeypatch.setattr(ordinary_apply, "urlopen", download)

    def remote(_ssh, _target, _instance, _local, request):
        if request["action"] == "inspect-package":
            assert request["manifest"]["runtime_plan"] == manifest["runtime_plan"]
            return {"observation": deepcopy(observed)}
        assert request["action"] == "stage-package"
        assert base64.b64decode(request["dependency_wheels"][filename]) == raw
        return {
            "status": "staged",
            "receipt": {
                "manifest": digest(request["manifest"]),
                "runtime_plan": digest(manifest["runtime_plan"]),
            },
        }

    monkeypatch.setattr(ordinary_apply, "remote", remote)
    return NS(
        manifest=manifest,
        wheel=wheel,
        raw=raw,
        filename=filename,
        requests=requests,
        observed=observed,
    )


def test_saved_package_intent_restores_missing_payload_before_first_lifecycle(
    packages, tmp_path, monkeypatch
):
    from nebius_vpngw import runtime_migration as migration

    # A previous completed native operation must not become this command's
    # pending manifest just because it belonged to the old ACTIVE lifecycle.
    native = {
        "phase": "complete",
        "operation": "f" * 32,
        "manifest": {**packages.manifest, "ha_parent": "e" * 64, "dependency_wheels": {}},
    }
    monkeypatch.setattr(migration, "state", lambda: deepcopy(native))
    monkeypatch.setattr(migration, "Path", lambda _path: tmp_path / "absent-ordinary-journal")
    assert migration.pending("e" * 64) is not None
    remote = ordinary_apply.remote

    def observe(*args):
        result = remote(*args)
        if args[-1]["action"] == "inspect-package":
            result["observation"]["runtime_pending"] = migration.pending(
                args[-1]["manifest"]["ha_parent"]
            )
        return result

    monkeypatch.setattr(ordinary_apply, "remote", observe)
    old = NS(record_sha256="1" * 64, status=VMHALifecycleStatus.ACTIVE, transaction=None)
    intent = ha_command.package_intent(old, "2" * 64, {"gateway-a": packages.manifest})
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "parent")
    command = ha_command.create(
        parent,
        scope="3" * 64,
        artifact="4" * 64,
        action_scope="5" * 64,
        targets=("compute-a",),
        approval="6" * 64,
        runtime_scope="7" * 64,
        report={},
        attachment_plan=None,
        packages=intent,
    )
    with parent:
        command.begin()
        command.start_runtime()
    saved = parent.path.read_bytes()
    resumed = ha_command.resume(
        parent, scope="3" * 64, artifact="4" * 64, action_scope="5" * 64, targets=("compute-a",)
    )
    operation, manifests = resumed.package_resume(old)
    assert operation == digest(
        {
            "domain": "nebius-vpngw/vm-ha-operation-v1",
            "approval_digest": "2" * 64,
            "predecessor_sha256": "1" * 64,
        }
    )
    plan = vm_ha_package.inspect_plan(
        object(),
        "host",
        NS(config_yaml="test", hostname="gateway-a"),
        {},
        target_identity="compute-a",
        wheel=packages.wheel,
        resume_parent=operation,
        resume_manifest=manifests["gateway-a"],
    )
    try:
        assert manifest_identity(plan.manifest) == manifest_identity(packages.manifest)
        assert plan.dependency_paths[0].read_bytes() == packages.raw
        vm_ha_package.stage(
            object(), plan, {}, artifact=NS(open_verified=lambda: packages.wheel.open("rb"))
        )
        assert parent.path.read_bytes() == saved
    finally:
        plan.close()
    child = NS(
        record_sha256="9" * 64,
        transaction=NS(
            operation_id=operation,
            resource_bindings={
                "runtime-command-execution": command.execution_id,
                "runtime-package-manifests": json.dumps(
                    {
                        "gateway-a": {
                            **packages.manifest,
                            "ha_parent": operation,
                        }
                    }
                ),
            },
        ),
    )
    assert resumed.package_resume(child) == (operation, manifests)
    for mutation in ("checkpoint", "operation", "execution", "manifest"):
        changed = deepcopy(child)
        if mutation == "checkpoint":
            changed = NS(record_sha256="9" * 64, transaction=None)
        elif mutation == "operation":
            changed.transaction.operation_id = "0" * 64
        elif mutation == "execution":
            changed.transaction.resource_bindings["runtime-command-execution"] = "0" * 32
        else:
            changed.transaction.resource_bindings["runtime-package-manifests"] = "{}"
        with pytest.raises(RuntimeError, match="lifecycle authority"):
            resumed.package_resume(changed)
        assert parent.path.read_bytes() == saved


@pytest.mark.parametrize("change", ["closure", "pending"])
def test_frozen_manifest_cannot_be_replaced_by_another_native_plan(packages, change):
    if change == "closure":
        packages.observed["runtime_plan"] = {"unapproved": "new apt candidates"}
    else:
        saved = deepcopy(packages.manifest)
        saved["dependency_wheels"] = {}
        saved["ha_parent"] = "8" * 64
        packages.observed["runtime_pending"] = {"manifest": saved}
    with pytest.raises(RuntimeError, match="approved|authority"):
        vm_ha_package.inspect_plan(
            object(),
            "host",
            NS(config_yaml="test", hostname="gateway-a"),
            {},
            target_identity="compute-a",
            wheel=packages.wheel,
            resume_parent="8" * 64,
            resume_manifest=packages.manifest,
        )
    assert packages.requests == []


@pytest.mark.parametrize("change", ["missing", "hash", "bytes", "origin", "filename", "tags"])
def test_package_restore_never_substitutes_a_wheel(packages, monkeypatch, tmp_path, change):
    download = ordinary_apply.urlopen

    def changed(url, **kwargs):
        response = download(url, **kwargs)
        if url == "https://pypi.org/pypi/example-dep/1.2/json":
            metadata = json.load(response)
            if change == "missing":
                metadata["urls"] = []
            elif change in {"hash", "origin", "filename"}:
                item = metadata["urls"][0]
                if change == "hash":
                    item["digests"]["sha256"] = "0" * 64
                elif change == "origin":
                    item["url"] = "https://untrusted.invalid/approved.whl"
                else:
                    item["filename"] = "example_dep-1.3-py3-none-any.whl"
            return io.BytesIO(json.dumps(metadata).encode())
        return io.BytesIO(b"unapproved wheel bytes") if change == "bytes" else response

    monkeypatch.setattr(ordinary_apply, "urlopen", changed)
    if change == "tags":
        packages.observed["wheel_tags"] = []
    target = tmp_path / "restore"
    target.mkdir()
    with pytest.raises(RuntimeError):
        ordinary_apply.restore_dependencies(packages.manifest, packages.observed, target)
    assert list(target.iterdir()) == []


def test_new_artifact_without_old_reservation_still_refuses_pending_native_work(packages):
    packages.observed["runtime_pending"] = {
        "manifest": {**packages.manifest, "wheel_sha256": "0" * 64}
    }
    with pytest.raises(vm_ha_package.VMHAAgentPackageError, match="same approved artifact"):
        vm_ha_package.inspect_plan(
            NS(),
            "target",
            NS(hostname="gateway-a", config_yaml="approved configuration"),
            {},
            target_identity="compute-a",
            wheel=packages.wheel,
        )
    assert packages.requests == []


@pytest.mark.parametrize(
    ("phase", "ordinary_state", "blocked"),
    [
        ("complete", None, False),
        ("complete", "complete", False),
        ("packages", "complete", True),
        ("complete", "installing", True),
    ],
)
def test_successor_ignores_only_completed_prior_package_work(
    packages, tmp_path, monkeypatch, phase, ordinary_state, blocked
):
    from nebius_vpngw import runtime_migration as migration

    native = {
        "phase": phase,
        "operation": "f" * 32,
        "manifest": {**packages.manifest, "ha_parent": "e" * 64, "wheel_sha256": "0" * 64},
    }
    journal = tmp_path / "ordinary.json"
    if ordinary_state is not None:
        journal.write_text(json.dumps({"id": native["operation"], "state": ordinary_state}))
    monkeypatch.setattr(migration, "state", lambda: deepcopy(native))
    monkeypatch.setattr(migration, "Path", lambda _path: journal)
    assert migration.pending("e" * 64) is not None
    packages.observed.update(dependencies={}, requirements={})
    remote = ordinary_apply.remote

    def observe(*args):
        result = remote(*args)
        result["observation"]["runtime_pending"] = migration.pending(
            args[-1]["manifest"].get("ha_parent")
        )
        return result

    monkeypatch.setattr(ordinary_apply, "remote", observe)
    resolved = []

    def resolve(*args):
        resolved.append(True)
        return []

    monkeypatch.setattr(ordinary_apply, "resolve_dependencies", resolve)

    def inspect():
        return vm_ha_package.inspect_plan(
            NS(),
            "target",
            NS(hostname="gateway-a", config_yaml="approved configuration"),
            {},
            target_identity="compute-a",
            wheel=packages.wheel,
        )

    if blocked:
        with pytest.raises(vm_ha_package.VMHAAgentPackageError, match="same approved artifact"):
            inspect()
        assert resolved == []
    else:
        plan = inspect()
        try:
            assert "ha_parent" not in plan.manifest and resolved == [True]
            assert plan.manifest["wheel_sha256"] == packages.manifest["wheel_sha256"]
        finally:
            plan.close()


def test_native_pending_uses_its_existing_staged_bytes(packages):
    packages.observed["runtime_pending"] = {
        "manifest": {
            **packages.manifest,
            "ha_parent": "8" * 64,
        }
    }
    plan = vm_ha_package.inspect_plan(
        object(),
        "host",
        NS(config_yaml="test", hostname="gateway-a"),
        {},
        target_identity="compute-a",
        wheel=packages.wheel,
        resume_parent="8" * 64,
        resume_manifest=packages.manifest,
    )
    try:
        assert plan.dependency_paths == [] and packages.requests == []
    finally:
        plan.close()
