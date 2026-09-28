from __future__ import annotations

import pytest

from nebius_cxcli.soperator_install_policy import validate_soperator_install_configuration
from nebius_cxcli.soperator_values import seed_soperator_values
from soperator_fixtures import sample_snapshot
from test_ssh_public_keys import _VALID_ED25519_PUBLIC_KEY


@pytest.mark.parametrize("override", [{}, {"enabled": True}])
def test_install_uses_upstream_certificate_owner(override) -> None:
    release = sample_snapshot()
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "version": release.release,
                    "values": {"certManager": override},
                }
            ]
        }
    }
    seed_soperator_values(payload, {"slurmNodes": {"login": {"sshRootPublicKeys": []}}})
    validate_soperator_install_configuration(payload, release)


@pytest.mark.parametrize("unowned_keys", [None, [], [_VALID_ED25519_PUBLIC_KEY]])
def test_install_rejects_missing_explicit_root_key_selection(unowned_keys) -> None:
    release = sample_snapshot()
    values = (
        {}
        if unowned_keys is None
        else {"slurmNodes": {"login": {"sshRootPublicKeys": unowned_keys}}}
    )
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "version": release.release,
                    "values": values,
                }
            ]
        }
    }
    with pytest.raises(ValueError, match="requires an explicit root SSH key selection"):
        validate_soperator_install_configuration(payload, release)


@pytest.mark.parametrize(
    "failure",
    [
        "disabled-certificate-owner",
        "duplicate-certificate-owner",
        "release-changed",
        "missing-soperator",
    ],
)
def test_install_rejects_changed_frozen_release_or_invalid_certificate_owner(failure: str) -> None:
    release = sample_snapshot()
    row = {"id": "soperator", "enabled": True, "version": release.release, "values": {}}
    rows = [row]
    if failure == "disabled-certificate-owner":
        row["values"] = {"certManager": {"enabled": False}}
    elif failure == "duplicate-certificate-owner":
        rows.append({"id": "cert-manager", "enabled": True})
    elif failure == "release-changed":
        row["version"] = "4.1.8"
    else:
        rows.clear()
    with pytest.raises(ValueError):
        validate_soperator_install_configuration({"apps": {"charts": rows}}, release)
