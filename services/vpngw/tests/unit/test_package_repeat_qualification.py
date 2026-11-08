"""Negative controls for package replay evidence; these do not qualify containers."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / "systemd/package_repeat.py"
_SPEC = importlib.util.spec_from_file_location("package_repeat", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
repeat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repeat)


@pytest.fixture
def observation():
    authority = {
        "schema": "operation-v1", "id": "operation-1", "config": "config-1",
        "artifact": "artifact-1", "predecessor": "predecessor-1",
        "purpose": "ha-handoff", "network": {"interface": "eth0"},
    }
    return {
        "schema": "nebius-vpngw.package-repeat-observation.v1",
        "topology": "ha",
        "runtime": {"phase": "installed", "operation": "operation-1", "policy": "saved"},
        "journal_authority": authority,
        "journal": {
            **authority, "state": "complete", "owner": {"pid": 1},
            "effects": [{"state": "settled", "writer": {"pid": 2}}],
        },
        "guard_authority": {"schema": "guard-v1", "guard_boot_id": "boot-1", "data_plane_mode": "blocked"},
        "guard": {"schema": "guard-v1", "guard_boot_id": "boot-1", "data_plane_mode": "blocked", "installed_at": 1},
        "native": {"native-package": {"/usr/bin/native": {"sha256": "payload-1"}}},
        "native_versions": ["native-package\t1.0\tinstall ok installed"],
        "python": {"product==1.0": {"files": {"product.py": "payload-1"}}},
        "python_roots": {"system-root": {"product.py": "payload-1"}},
        "assets": {"service": {"mode": 0o644, "sha256": "asset-1"}},
        "staged": {"wheel": "selected-1"},
        "request": "request-1", "selected_manifest": "manifest-1",
        "config": "config-1", "boot": "boot-1", "unit_policy": "policy-1",
        "startup_files": {"inhibited": "present"},
        "ha_files": {"apply.lock": "lock-1", "controller-checkpoint.json": "checkpoint-1"},
        "ha_marker": "enabled",
        "safety": {
            key: True for key in (
                "inhibited", "activation_token_absent", "writers_quiescent", "journal_complete",
                "effects_settled", "dpkg_clean", "pip_check", "forwarding_blocked",
                "standby_readiness_absent",
            )
        },
    }


@pytest.mark.parametrize("field", [
    "native", "native_versions", "python", "python_roots", "assets", "staged", "request",
    "selected_manifest", "config", "boot", "unit_policy", "startup_files", "ha_files",
])
def test_payload_authority_policy_or_checkpoint_drift_rejected(observation, field):
    after = copy.deepcopy(observation)
    after[field] = {"changed": "unexpected"}
    with pytest.raises(AssertionError):
        repeat.verify_repeat(observation, after)


def test_execution_metadata_changes_are_explicitly_permitted(observation):
    after = copy.deepcopy(observation)
    after["journal"]["owner"] = {"pid": 101}
    after["journal"]["effects"] = [{"state": "settled", "writer": {"pid": 102}}]
    after["guard"]["installed_at"] = 2
    result = repeat.verify_repeat(observation, after)
    assert result["status"] == "passed"
    assert result["full_apply_noop"] == "not-qualified-here-required-on-live-vms"


@pytest.mark.parametrize("change", ["operation", "active-journal", "unsettled", "guard", "missing-inhibition", "false-inhibition"])
def test_recovery_or_safety_drift_cannot_hide_in_execution_metadata(observation, change):
    after = copy.deepcopy(observation)
    if change == "operation":
        after["journal"]["id"] = "new-operation"
    elif change == "active-journal":
        after["journal"]["state"] = "active"
    elif change == "unsettled":
        after["journal"]["effects"][0]["state"] = "running"
    elif change == "guard":
        after["guard"]["data_plane_mode"] = "active"
    elif change == "missing-inhibition":
        del after["safety"]["inhibited"]
    else:
        after["safety"]["inhibited"] = False
    with pytest.raises(AssertionError):
        repeat.verify_repeat(observation, after)


def test_native_payload_metadata_excludes_time_but_keeps_bytes_mode_and_links(tmp_path):
    payload = tmp_path / "payload"
    payload.write_bytes(b"original")
    before = repeat.file_identity(payload)
    payload.touch()
    assert repeat.file_identity(payload) == before
    payload.write_bytes(b"modified")
    assert repeat.file_identity(payload) != before
    payload.write_bytes(b"original")
    payload.chmod(0o700)
    assert repeat.file_identity(payload) != before
    link = tmp_path / "link"
    link.symlink_to("payload")
    before_link = repeat.file_identity(link)
    link.unlink()
    link.symlink_to("unrelated")
    assert repeat.file_identity(link) != before_link


def test_retained_required_native_payload_is_covered(monkeypatch):
    contents = {"retained": "original", "install": "original"}
    monkeypatch.setattr(repeat, "native_payload", lambda name: {"payload": contents[name]})
    plan = {"desired": {"retained": ["1", "installed"], "install": ["2", "installed"]}, "install": ["install"]}
    before = repeat.native_selection_payload(plan)
    contents["retained"] = "tampered"
    assert repeat.native_selection_payload(plan) != before


@pytest.mark.parametrize("metadata_name", ["example.egg-info", "example-1.0.dist-info"])
def test_metadata_only_or_sources_metadata_uses_complete_installed_root(tmp_path, monkeypatch, metadata_name):
    root = tmp_path / "dist-packages"
    root.mkdir()
    metadata = root / metadata_name
    metadata.mkdir()
    (metadata / "SOURCES.txt").write_text("missing-build-source.py\n")
    (root / "example.py").write_text("payload = 1\n")

    class Distribution:
        metadata = {"Name": "example", "Version": "1.0"}
        version = "1.0"
        requires = []

        def locate_file(self, path):
            return root / path

        def read_text(self, name):
            assert name == "RECORD"
            return None

        @property
        def files(self):
            raise AssertionError("SOURCES is not an installed-file inventory")

    monkeypatch.setattr(repeat.importlib.metadata, "distributions", lambda: [Distribution()])
    monkeypatch.setattr(repeat, "system_distribution_roots", lambda: {root})
    before = repeat.python_payload()
    assert "example.py" in before["roots"][str(root)]
    (root / "unlisted.py").write_text("unexpected = True\n")
    assert repeat.python_payload() != before
    (root / "unlisted.py").unlink()
    (root / "example.py").write_text("payload = 2\n")
    assert repeat.python_payload() != before


def test_generated_bytecode_exclusion_keeps_bytecode_only_payload_and_links(tmp_path):
    (tmp_path / "module.py").write_text("payload = 1\n")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    generated = cache / "module.cpython-312.pyc"
    generated.write_bytes(b"generated")
    bytecode_only = tmp_path / "bytecode_only.pyc"
    bytecode_only.write_bytes(b"payload")
    before = repeat.tree_payload(tmp_path)
    generated.write_bytes(b"regenerated")
    assert repeat.tree_payload(tmp_path) == before
    bytecode_only.write_bytes(b"tampered")
    assert repeat.tree_payload(tmp_path) != before
    generated.unlink()
    generated.symlink_to(bytecode_only)
    assert not repeat.generated_bytecode(generated)


def test_record_excludes_only_bytecode_with_corresponding_source(tmp_path):
    (tmp_path / "module.py").write_text("payload = 1\n")
    metadata = tmp_path / "example-1.0.dist-info"
    metadata.mkdir()
    record = metadata / "RECORD"
    record.write_text("module.py,sha256=payload,12\n__pycache__/module.cpython-312.pyc,,\nbytecode_only.pyc,,\n")
    identity = repeat.file_identity(record)
    assert ["bytecode_only.pyc", "", ""] in identity["record_rows"]
    assert not any("__pycache__" in row[0] for row in identity["record_rows"])
    record.write_text("module.py,sha256=tampered,12\nbytecode_only.pyc,,\n")
    assert repeat.file_identity(record) != identity


@pytest.mark.parametrize("field,value", [
    ("predecessor", "0.6.1"), ("mode", "bgp"), ("topology", "ha"),
    ("interruption", "native-unpack"), ("boundary", "unqualified"),
])
def test_case_label_cannot_hide_misfiled_or_duplicated_coverage(field, value):
    result = {
        "predecessor": "0.6.0", "mode": "static", "topology": "ordinary",
        "interruption": None,
        "boundary": "ordinary-shared-package-components-without-apply-activation",
    }
    repeat.verify_case_identity("0.6.0-static-ordinary", result)
    result[field] = value
    with pytest.raises(AssertionError):
        repeat.verify_case_identity("0.6.0-static-ordinary", result)


def test_effective_dependency_precedence_retains_shadow_payload(tmp_path, monkeypatch):
    local = tmp_path / "local/dist-packages"
    system = tmp_path / "system/dist-packages"
    for root in (local, system):
        root.mkdir(parents=True)
        (root / "example.py").write_text("payload = 1\n")

    class Distribution:
        requires = []

        def __init__(self, root, version):
            self.root, self.version = root, version
            self.metadata = {"Name": "example_name", "Version": version}

        def locate_file(self, path):
            return self.root / path

        def read_text(self, name):
            assert name == "RECORD"
            return None

    monkeypatch.setattr(repeat.importlib.metadata, "distributions", lambda: [Distribution(local, "2"), Distribution(system, "1")])
    monkeypatch.setattr(repeat, "system_distribution_roots", lambda: {local, system})
    before = repeat.python_payload()
    assert before["effective_dependencies"] == {"example-name": "2"}
    assert before["precedence"]["example-name"] == [{"version": "2", "root": str(local)}, {"version": "1", "root": str(system)}]
    assert len(before["distributions"]) == 2
    (system / "example.py").write_text("shadow_payload = 2\n")
    assert repeat.python_payload() != before
