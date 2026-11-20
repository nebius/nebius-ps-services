from types import SimpleNamespace

import pytest

from nebius_vpngw import release_identity
from nebius_vpngw.deploy import ordinary_remote, release_policy


@pytest.mark.parametrize("evidence", ["journal", "config", "module", "duplicates"])
def test_partial_release_still_blocks_admission(monkeypatch, evidence):
    monkeypatch.setattr(
        release_identity.importlib.metadata,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "nebius-vpngw"}, version="0.6.9")] * 2
        if evidence == "duplicates" else [],
    )
    monkeypatch.setattr(
        release_identity.importlib.util, "find_spec",
        lambda _: object() if evidence == "module" else None,
    )
    monkeypatch.setattr(
        release_identity, "Path",
        lambda name: SimpleNamespace(exists=lambda: (
            evidence == "journal" and name.endswith("operation.json")
            or evidence == "config" and name.endswith("config-resolved.yaml")
        )),
    )
    assert release_identity.observe() == {"state": "unknown", "version": None}
    with pytest.raises(release_policy.ReleaseBlocked):
        release_policy.require(release_identity.observe(), "0.6.9")
    expected = "absent" if evidence == "journal" else "unknown"
    assert release_identity.observe_installation()["state"] == expected


@pytest.mark.parametrize("changed", [
    {"state": "present", "version": "0.6.9"},
    {"state": "unknown", "version": None},
])
def test_quiescence_comparison_retains_installed_release_changes(changed):
    before = {
        "installed_release_identity": {"state": "absent", "version": None},
        "release_identity": {"state": "absent", "version": None},
    }
    after = {**before, "installed_release_identity": changed,
             "release_identity": {"state": "unknown", "version": None}}
    assert ordinary_remote.stable_predecessor(before) != ordinary_remote.stable_predecessor(after)
