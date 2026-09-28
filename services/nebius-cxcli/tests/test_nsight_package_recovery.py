import copy

import pytest

from nebius_cxcli import nsight_jail as jail


@pytest.fixture
def package_state(tmp_path, monkeypatch):
    for field in ("PROFILE", "RECEIPT", "INTENT", "DPKG_INFO", "CACHE"):
        monkeypatch.setattr(jail, field, tmp_path / field)
    jail.DPKG_INFO.mkdir()
    admission = {
        "schema": "nebius-cxcli.nsight-packages.v2",
        "baseline": {},
        "preimages": {"profile": {"missing": True}, "receipt": {"missing": True}},
        "artifacts": [
            {
                "package": "nsight-compute-2026.2.1",
                "architecture": "arm64",
                "version": "2026.2.1.5-1",
                "controlSha256": {},
            }
        ],
    }
    artifact = admission["artifacts"][0]
    current = {
        artifact["package"] + ":arm64": {
            "package": artifact["package"],
            "architecture": "arm64",
            "version": artifact["version"],
            "status": "install ok unpacked",
            "awaited": "",
            "pending": "",
        }
    }
    monkeypatch.setattr(jail, "package_state", lambda: copy.deepcopy(current))
    jail.atomic_json(
        jail.INTENT,
        {"admissionSha256": jail.fingerprint(admission), "preimages": admission["preimages"]},
    )
    return admission, current


@pytest.mark.parametrize(
    "status", ["install ok unpacked", "install ok half-configured", "install ok installed"]
)
def test_owned_partial_states_are_admitted(package_state, status):
    admission, current = package_state
    next(iter(current.values()))["status"] = status
    assert jail.check_package_preconditions(admission)["admissionSha256"] == jail.fingerprint(
        admission
    )


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "architecture",
        "half-installed",
        "reinstreq",
        "triggers",
        "unrelated",
        "missing-intent",
        "stale-intent",
    ],
)
def test_unowned_or_ambiguous_partial_state_is_rejected(package_state, change):
    admission, current = package_state
    row = next(iter(current.values()))
    if change in {"version", "architecture"}:
        row[change] = "different"
    elif change == "half-installed":
        row["status"] = "install ok half-installed"
    elif change == "reinstreq":
        row["status"] = "install reinstreq unpacked"
    elif change == "triggers":
        row["pending"] = "unowned-trigger"
    elif change == "unrelated":
        current["foreign:arm64"] = {**row, "package": "foreign"}
    elif change == "missing-intent":
        jail.INTENT.unlink()
    else:
        jail.atomic_json(jail.INTENT, {"admissionSha256": "stale"})
    with pytest.raises(RuntimeError):
        jail.check_package_preconditions(admission)


@pytest.mark.parametrize("status", ["install ok unpacked", "install ok half-configured"])
def test_partial_control_scripts_must_match_archive_including_unexpected_scripts(
    package_state, status
):
    admission, current = package_state
    row = next(iter(current.values()))
    row["status"] = status
    path = jail.DPKG_INFO / (row["package"] + ".postinst")
    path.write_text("unowned script")
    with pytest.raises(RuntimeError, match="control ownership"):
        jail.check_package_preconditions(admission)
    admission["artifacts"][0]["controlSha256"] = {"postinst": jail.sha(path)}
    jail.atomic_json(
        jail.INTENT,
        {"admissionSha256": jail.fingerprint(admission), "preimages": admission["preimages"]},
    )
    jail.check_package_preconditions(admission)
    path.write_text("changed script")
    with pytest.raises(RuntimeError, match="control ownership"):
        jail.check_package_preconditions(admission)


def test_independent_verify_rejects_coherent_dependency_upgrade(package_state):
    admission, current = package_state
    row = next(iter(current.values()))
    row.update(status="install ok installed", version="newer")
    with pytest.raises(RuntimeError, match="not safely resumable"):
        jail.verify(admission)


def test_cache_only_install_does_not_download_missing_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(jail, "CACHE", tmp_path)
    monkeypatch.setattr(
        jail.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("network attempted")
    )
    with pytest.raises(RuntimeError, match="cache"):
        jail.download(
            {
                "package": "nsight",
                "url": "https://developer.download.nvidia.com/tool.deb",
                "sha256": "a" * 64,
            },
            cached_only=True,
        )


def test_request_codec_rejects_trailing_data_old_json_and_expansion_bombs():
    import base64
    import zlib

    value = {"action": "verify", "payload": "x" * 10000}
    assert jail.decode_payload(jail.encode_payload(value)) == value
    for encoded in (
        '{"action":"install"}',
        "nsight-zlib-v1:" + base64.b64encode(zlib.compress(b"{}") + b"trailing").decode(),
        "nsight-zlib-v1:" + base64.b64encode(zlib.compress(b"x" * (8 * 1024 * 1024 + 1))).decode(),
    ):
        with pytest.raises(RuntimeError):
            jail.decode_payload(encoded)
