import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

HELPER = Path(__file__).resolve().parents[2] / "misc" / "gcp_vpngw_credentials.py"
spec = importlib.util.spec_from_file_location("gcp_credentials", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_mixed_existing_and_new_tunnels_retain_only_known_credentials():
    state = helper.prepare(
        None, ("existing", "new"), (True, False), False, ("ignored-existing", "new-value")
    )
    assert state["tunnels"] == {"existing": None, "new": "new-value"}
    # A create may have reached GCP even if its response was lost. The next
    # invocation uses exactly the persisted keys, regardless of existence reads.
    assert helper.prepare(state, ("existing", "new"), (True, True), False, ("", "")) == state


def test_pending_generation_is_never_rotated_on_retry():
    state = helper.prepare(None, ("one", "two"), (False, False), True, ("", ""))
    assert state["tunnels"]["one"] != state["tunnels"]["two"]
    assert helper.prepare(state, ("one", "two"), (True, False), True, ("", "")) == state
    with pytest.raises(ValueError, match="retry the same"):
        helper.prepare(state, ("one", "two"), (True, False), True, ("different", ""))


def test_secret_uses_descriptor_and_is_removed_from_child_environment(monkeypatch):
    canary = "test-only-credential-canary"
    monkeypatch.setenv("PSK1", canary)
    monkeypatch.setenv("CUSTOM_SECRET_ALIAS", canary)
    seen = []

    def run(command, **kwargs):
        assert canary not in repr(command)
        assert canary not in repr(kwargs["env"])
        descriptor = kwargs["pass_fds"][0]
        assert json.loads(os.read(descriptor, 8192)) == {"--shared-secret": canary}
        seen.append(command)
        return SimpleNamespace(returncode=1, stdout=canary, stderr=canary)

    monkeypatch.setattr(helper.subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        helper.create(["compute", "vpn-tunnels", "create", "test"], canary)
    assert canary not in str(error.value)
    assert len(seen) == 1


def test_private_state_publication_and_symlink_refusal(tmp_path):
    root = tmp_path / "state"
    helper.private_directory(root)
    path = root / "credentials.json"
    helper.publish(path, '{"schema":1}')
    assert root.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert helper.read(path) == {"schema": 1}
    path.unlink()
    path.symlink_to(tmp_path / "foreign")
    with pytest.raises(ValueError, match="Unsafe"):
        helper.publish(path, "{}")
    assert not (tmp_path / "foreign").exists()


@pytest.fixture
def gcp_shell(tmp_path):
    import sys

    executable = tmp_path / "gcloud"
    executable.write_text("""#!/usr/bin/env python3
import hashlib,json,os,sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ['FIXTURE_CLOUD'])
with Path(os.environ['FIXTURE_CALLS']).open('a') as output:
    output.write(json.dumps(args)+'\\n')
if args[:2]==['auth','list']:
    print('fixture@example.invalid');sys.exit(0)
if len(args)>3 and args[0]=='compute' and args[2]=='describe':
    if args[1]=='vpn-tunnels' and (root/args[3]).exists():
        print('nebius-192-0-2-10-peer,ha-gw-nebius,cr-nebius-ha,'+('0' if args[3].endswith('-1') else '1')+',0');sys.exit(0)
    print('ERROR: resource was not found',file=sys.stderr);sys.exit(1)
if len(args)>3 and args[:3]==['compute','vpn-tunnels','create']:
    descriptor=next(item.split('=',1)[1] for item in args if item.startswith('--flags-file='))
    value=json.loads(Path(descriptor).read_text())['--shared-secret']
    assert value not in repr(args)
    assert value not in repr(os.environ)
    (root/args[3]).write_text(hashlib.sha256(value.encode()).hexdigest())
    print('simulated lost create response',file=sys.stderr);sys.exit(1)
if len(args)>2 and args[0]=='compute' and args[2]=='list':
    sys.exit(0)
if len(args)>2 and args[0]=='compute' and args[2]=='create':
    sys.exit(0)
if args[:2]==['config','set']:
    sys.exit(0)
sys.exit(2)
""")
    executable.chmod(0o755)
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    env = {
        **os.environ,
        "HOME": str(home),
        "FIXTURE_CLOUD": str(cloud),
        "FIXTURE_CALLS": str(tmp_path / "calls.jsonl"),
        "PATH": os.pathsep.join(
            (str(tmp_path), str(Path(sys.executable).parent), os.environ["PATH"])
        ),
        "GCP_PROJECT_ID": "fixture-project",
        "REGION": "fixture-region",
        "NEBIUS_PUBLIC_IP": "192.0.2.10",
        "NEBIUS_ASN": "65010",
        "CONNECTION_NAME": "site",
        "PSK1": "",
        "PSK2": "",
    }
    command = ["bash", str(HELPER.with_name("gcp-vpngw.sh"))]
    root = home / ".local/state/nebius-vpngw/gcp/fixture-project/fixture-region/site"
    return command, env, cloud, root


def test_real_shell_entrypoint_reuses_credentials_after_lost_create_response(gcp_shell):
    import subprocess

    command, env, cloud, root = gcp_shell
    first = subprocess.run(
        command, env=env, input="y\n", text=True, capture_output=True, timeout=30
    )
    assert first.returncode != 0
    assert (root / "credentials.json").exists(), first.stdout + first.stderr
    retained = (root / "credentials.json").read_bytes()
    values = json.loads(retained)["tunnels"]
    assert len(list(cloud.iterdir())) == 1
    second = subprocess.run(
        command, env=env, input="y\n", text=True, capture_output=True, timeout=30
    )
    assert second.returncode != 0
    assert (root / "credentials.json").read_bytes() == retained
    assert len(list(cloud.iterdir())) == 2, second.stdout + second.stderr
    for value in values.values():
        assert value not in first.stdout + first.stderr + second.stdout + second.stderr


def test_credential_directory_rejects_a_writable_ancestor(tmp_path):
    ancestor = tmp_path / "shared"
    ancestor.mkdir(mode=0o777)
    ancestor.chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe credential directory"):
        helper.private_directory(ancestor / "private")
    assert not (ancestor / "private").exists()


@pytest.mark.parametrize("invalid", [None, "", "x" * 4097, "line\nbreak", "é", "\x7f"])
@pytest.mark.parametrize("index", [0, 1])
def test_required_pending_credentials_validate_before_reuse(invalid, index):
    import copy

    names = ("one", "two")
    state = {"schema": 1, "pending": True, "tunnels": dict.fromkeys(names, "valid-key")}
    state["tunnels"][names[index]] = invalid
    original = copy.deepcopy(state)
    with pytest.raises(ValueError):
        helper.prepare(state, names, (True, True), True, ("", ""))
    assert state == original


def test_pending_unknown_key_cannot_recreate_a_disappeared_tunnel():
    state = {"schema": 1, "pending": True, "tunnels": {"one": None, "two": "known"}}
    assert helper.prepare(state, ("one", "two"), (True, True), False, ("", "")) == state
    with pytest.raises(ValueError):
        helper.prepare(state, ("one", "two"), (False, True), False, ("", ""))


@pytest.mark.parametrize("invalid", ["x" * 4097, "line\nbreak", "é", "\x7f"])
@pytest.mark.parametrize("index", [0, 1])
def test_supplied_credentials_validate_as_a_complete_set(invalid, index):
    supplied = ["valid-key", "valid-key"]
    supplied[index] = invalid
    with pytest.raises(ValueError):
        helper.prepare(None, ("one", "two"), (True, True), True, tuple(supplied))


@pytest.mark.parametrize("invalid", [None, "", "x" * 4097, "line\nbreak", "é", "\x7f"])
def test_create_uses_same_credential_validation(invalid, monkeypatch):
    monkeypatch.setattr(helper.subprocess, "run", lambda *a, **k: pytest.fail("provider called"))
    with pytest.raises(ValueError):
        helper.create([], invalid)


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("invalid", ["x" * 4097, "not\nprintable", "é", "\x7f"])
def test_real_shell_invalid_pair_never_deletes_or_publishes(gcp_shell, index, invalid):
    import subprocess

    command, env, cloud, root = gcp_shell
    env.update(TUNNEL1_NAME="fixture-1", TUNNEL2_NAME="fixture-2")
    (cloud / "fixture-1").write_text("existing")
    (cloud / "fixture-2").write_text("existing")
    env["PSK" + str(index)] = invalid
    result = subprocess.run(
        command + ["--rotate-existing-tunnels"],
        env=env,
        input="y\n",
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Private credential operation failed" in result.stderr
    calls = [json.loads(line) for line in Path(env["FIXTURE_CALLS"]).read_text().splitlines()]
    assert not any(
        "delete" in call or "remove-bgp-peer" in call or "remove-interface" in call
        for call in calls
    )
    assert not (root / "credentials.json").exists()
    assert invalid not in result.stdout + result.stderr
