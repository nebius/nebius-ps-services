import copy
import zipfile
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement

from nebius_vpngw import dependency_scope as scope
from nebius_vpngw.deploy import ordinary_apply


def distribution(name, version, requirements=(), prefix="/fixture"):
    return SimpleNamespace(
        metadata={"Name": name}, version=version, requires=list(requirements),
        locate_file=lambda _: prefix,
    )


def inventory(*extra):
    return scope.inventory(
        [
            distribution("nebius-vpngw", "1", ["shared[feature]>=1"]),
            distribution("shared", "1", ['leaf>=2; extra == "feature"']),
            distribution("leaf", "2"),
            distribution("pip", "25"),
            *extra,
        ],
        {"python_version": "3.12", "sys_platform": "linux"},
    )


@pytest.mark.parametrize("versions", [(), ("1",), ("2",), ("1", "2")])
def test_unrelated_inventory_changes_do_not_change_approval_projection(versions):
    before = inventory()
    after = inventory(*(distribution("unrelated", v, ['elsewhere; extra == "x"']) for v in versions))
    manifest = {"requirements": ["shared[feature]>=1"]}
    selected = scope.names(manifest, before, parse_requirement=Requirement)
    assert selected == {"nebius-vpngw", "pip", "shared", "leaf"}
    assert scope.project(before, selected) == scope.project(after, selected)


def test_owned_ambiguity_and_shared_reverse_conflicts_still_block():
    observed = inventory(distribution("shared", "2"))
    with pytest.raises(RuntimeError, match="conflicting installed"):
        scope.project(observed, {"shared"})
    observed = inventory(distribution("unrelated", "1", ["shared<2"]))
    with pytest.raises(RuntimeError, match="conflicts with installed"):
        scope.check_shared_conflicts(observed, {"shared": "2"}, parse_requirement=Requirement)
    # Every duplicate candidate is checked; enumeration cannot hide a conflict.
    observed = inventory(
        distribution("unrelated", "1", ["shared>=1"]),
        distribution("unrelated", "2", ["shared<2"]),
    )
    with pytest.raises(RuntimeError, match="conflicts with installed"):
        scope.check_shared_conflicts(observed, {"shared": "2"}, parse_requirement=Requirement)


def test_closure_unions_transitive_extras_markers_and_terminates_cycles():
    requirements = {
        "root": ["a[left]", "a[right]", 'windows; sys_platform == "win32"'],
        "a": ['b[deep]; extra == "left"', 'c; extra == "right"'],
        "b": ["root", 'd; extra == "deep"'],
    }
    assert scope.closure(
        ["root"], requirements, {"sys_platform": "linux"}, parse_requirement=Requirement
    ) == {"root", "a", "b", "c", "d"}


def test_new_wheel_metadata_replaces_installed_transitive_metadata(tmp_path):
    observed = inventory(distribution("new-leaf", "3"))
    manifest = {"version": "2", "requirements": ["shared>=2"]}
    wheel = tmp_path / "shared-2-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "shared-2.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: shared\nVersion: 2\nRequires-Dist: new-leaf>=3\n",
        )
    ordinary_apply.prepare_dependency_manifest(manifest, observed, [wheel])
    assert manifest["expected_dependencies"] == {
        "nebius-vpngw": "2", "pip": "25", "shared": "2", "leaf": "2", "new-leaf": "3"
    }
    assert manifest["expected_requirements"]["shared"] == ["new-leaf>=3"]


def test_retained_manifest_scope_is_exact_and_not_rewritten():
    manifest = {"expected_dependencies": {"nebius-vpngw": "1", "unrelated": "4"}}
    before = copy.deepcopy(manifest)
    selected = scope.names(manifest, inventory(), parse_requirement=Requirement)
    assert selected == {"nebius-vpngw", "unrelated"}
    assert manifest == before


def test_fresh_dependent_can_block_without_entering_approval_digest():
    initial = inventory()
    added = inventory(distribution("new-dependent", "1", ["shared<2"]))
    selected = scope.names({"requirements": ["shared>=1"]}, initial, parse_requirement=Requirement)
    assert scope.project(initial, selected) == scope.project(added, selected)
    scope.check_shared_conflicts(initial, {"shared": "2"}, parse_requirement=Requirement)
    with pytest.raises(RuntimeError, match="conflicts with installed"):
        scope.check_shared_conflicts(added, {"shared": "2"}, parse_requirement=Requirement)


@pytest.mark.parametrize("change", ["version", "requirements", "service"])
def test_dependency_reinspection_rejects_drift_after_resolution(change):
    initial = inventory()
    manifest = {"expected_dependencies": {"nebius-vpngw": "2", "shared": "1"}}
    selected = set(manifest["expected_dependencies"])
    before = {"service": "active", **scope.project(initial, {"nebius-vpngw"})}
    after = {"service": "active", **scope.project(initial, selected)}
    if change == "version":
        # No shared wheel was downloaded: its original version satisfied the
        # desired requirements. Accepting a changed version would strand apply.
        after["dependencies"]["shared"] = "2"
    elif change == "requirements":
        after["requirements"]["shared"] = ["new-leaf>=3"]
    else:
        after["service"] = "inactive"
    with pytest.raises(RuntimeError, match="changed during dependency planning"):
        ordinary_apply.refresh_dependency_observation(before, after, initial, manifest)


def test_dependency_reinspection_accepts_scope_expansion_from_original_inventory():
    initial = inventory()
    manifest = {"expected_dependencies": {"nebius-vpngw": "2", "shared": "1"}}
    before = {"service": "active", **scope.project(initial, {"nebius-vpngw"})}
    after = {"service": "active", **scope.project(initial, set(manifest["expected_dependencies"]))}
    assert ordinary_apply.refresh_dependency_observation(before, after, initial, manifest) == after
