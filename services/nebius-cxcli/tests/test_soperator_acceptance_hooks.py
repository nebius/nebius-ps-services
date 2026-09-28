from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_acceptance_hooks as hooks

TEMPLATE_PATH = "templates/wait-for-checks-job.yaml"
REVIEWED_TEMPLATE = (
    b"spec:\n"
    b"      serviceAccountName: waiter\n"
    b"          image: registry.example/soperator-proxy-docker-io/alpine/k8s:1.31.11\n"
    b"          command: [reviewed-waiter]\n"
)


def waiter(events="post-install,post-upgrade"):
    return {
        "kind": "Job",
        "metadata": {"name": "wait-for-active-checks", "annotations": {"helm.sh/hook": events}},
    }


@pytest.fixture
def reviewed(monkeypatch):
    source = REVIEWED_TEMPLATE
    contract = source.replace(b"registry.example/", b"<release-registry>/")
    monkeypatch.setattr(
        hooks, "WAITER_TEMPLATE_CONTRACT_SHA256", hashlib.sha256(contract).hexdigest()
    )
    return {"templates/wait-for-checks-job.yaml": source}


def test_registry_hostname_change_preserves_reviewed_waiter_contract(reviewed):
    source = REVIEWED_TEMPLATE.replace(b"registry.example/", b"new.registry.example/")
    files = {TEMPLATE_PATH: source}
    hooks.verify_waiter_inventory(files, [waiter()])
    assert files[TEMPLATE_PATH] == source


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"alpine/k8s", b"unreviewed/k8s"),
        (b"1.31.11", b"1.31.12"),
        (b"reviewed-waiter", b"unreviewed-command"),
        (b"serviceAccountName: waiter", b"serviceAccountName: privileged"),
        (b"          image: ", b"          unreviewedField: "),
    ],
)
def test_waiter_execution_changes_fail_closed(reviewed, old, new):
    with pytest.raises(ValueError, match="no reviewed acceptance adapter"):
        hooks.verify_waiter_inventory(
            {TEMPLATE_PATH: REVIEWED_TEMPLATE.replace(old, new)}, [waiter()]
        )


def test_missing_or_duplicate_waiter_image_fails_closed(reviewed):
    image = next(line for line in REVIEWED_TEMPLATE.splitlines(keepends=True) if b"image:" in line)
    for source in (b"", REVIEWED_TEMPLATE.replace(image, b""), REVIEWED_TEMPLATE + image):
        with pytest.raises(ValueError, match="no reviewed acceptance adapter"):
            hooks.verify_waiter_inventory({TEMPLATE_PATH: source}, [waiter()])


@pytest.mark.parametrize("documents", [[], [waiter("post-install")], [waiter(), waiter()]])
def test_missing_or_changed_waiter_inventory_fails_closed(reviewed, documents):
    with pytest.raises(ValueError, match="sole waiter"):
        hooks.verify_waiter_inventory(reviewed, documents)


def test_waiter_ownership_accepts_whitespace_but_not_hidden_hooks(reviewed):
    hooks.verify_waiter_inventory(reviewed, [waiter(" post-install, post-upgrade ")])
    with pytest.raises(ValueError, match="sole waiter"):
        hooks.verify_waiter_inventory(
            reviewed,
            [
                waiter(),
                {
                    "kind": "Job",
                    "metadata": {"name": "another", "annotations": {"helm.sh/hook": "pre-upgrade"}},
                },
            ],
        )
    with pytest.raises(ValueError, match="malformed"):
        hooks.verify_waiter_inventory(reviewed, [waiter("post-install,,post-upgrade")])
    with pytest.raises(ValueError, match="reviewed"):
        hooks.verify_waiter_inventory(
            {"templates/wait-for-checks-job.yaml": b"changed"}, [waiter()]
        )


def test_hook_inventory_covers_install_upgrade_and_quiet_desired_values(
    tmp_path, monkeypatch, reviewed
):
    path = tmp_path / "templates/wait-for-checks-job.yaml"
    path.parent.mkdir()
    path.write_bytes(reviewed[path.relative_to(tmp_path).as_posix()])
    calls = []

    def render(command, **kwargs):
        calls.append(("--is-upgrade" in command, yaml.safe_load(kwargs["input"])))
        return SimpleNamespace(returncode=0, stdout=yaml.safe_dump(waiter()))

    monkeypatch.setattr(hooks.kubernetes_process, "run", render)
    hooks.verify_policy_hooks(tmp_path, [{"quiet": True}, {"quiet": False}])
    assert calls == [
        (False, {"quiet": True}),
        (True, {"quiet": True}),
        (False, {"quiet": False}),
        (True, {"quiet": False}),
    ]
