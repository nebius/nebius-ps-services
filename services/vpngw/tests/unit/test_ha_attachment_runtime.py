"""Terminal attachment inspection verifies live bytes without changing owners."""

from copy import deepcopy

import pytest
from tests.unit.test_vm_ha_corrective import reservation_guest as reservation_guest


@pytest.mark.parametrize(
    "fault", [None, "files", "dependencies", "assets", "manifest", "phase", "worker"]
)
def test_terminal_native_probe_verifies_installed_state_without_writes(
    reservation_guest,
    monkeypatch,
    tmp_path,
    fault,
):
    from nebius_vpngw import package_worker

    guest, _request = reservation_guest
    runtime = guest.remote.migration.state()
    manifest = {
        **runtime["manifest"],
        "files": {"module.py": "a" * 64},
        "version": "0.6.5.dev1",
        "expected_dependencies": {"dependency": "1"},
        "expected_requirements": {"dependency": []},
        "assets": {"/etc/example-managed-unit": {"sha256": "b" * 64, "mode": 0o644}},
    }
    runtime["manifest"] = deepcopy(manifest)
    runtime["phase"] = "installed" if fault == "phase" else "complete"
    if fault == "manifest":
        runtime["manifest"]["version"] = "0.6.5.dev2"
    guest.remote.migration.save(runtime)
    installed = dict(
        files=manifest["files"],
        package_version=manifest["version"],
        dependencies=manifest["expected_dependencies"],
        requirements=manifest["expected_requirements"],
        assets={path: item["sha256"] for path, item in manifest["assets"].items()},
        asset_modes={path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()},
    )
    if fault in {"files", "dependencies", "assets"}:
        installed[fault] = {}
    monkeypatch.setattr(guest, "package_observation", lambda _: installed)
    reservation = dict(identity="c" * 64, request=dict(package=dict(desired=manifest)))
    reads = []

    def settled(expected):
        assert expected == reservation
        reads.append(expected["identity"])
        if fault == "worker":
            raise RuntimeError("Previous package worker has unsettled requests")
        return dict(identity=expected["identity"], record="d" * 64, generation=1)

    monkeypatch.setattr(package_worker, "settled_reservation", settled)
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    request = dict(
        action="replan-inspect",
        operation="e" * 64,
        package_reservation=reservation,
        terminal_runtime=True,
    )
    if fault:
        with pytest.raises(RuntimeError):
            guest.replan_inspect(request)
    else:
        result = guest.replan_inspect(request)
        assert result["installed"] == guest.ops.digest(installed)
        assert len(reads) == 2
        assert result["children"]["runtime"]["phase"] == "complete"
    assert before == {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
