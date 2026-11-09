"""Regional credentials remain scoped through generation and child execution."""

import json
import os
import sys

import pytest
from tests.unit.test_pair_apply import CHILD, prepared_pair
from tests.unit.test_pair_config import seed
from typer.testing import CliRunner

from nebius_vpngw import pair_apply, pair_cli, pair_config
from nebius_vpngw.cli import app


def create(directory, **profiles):
    return pair_config.create(
        directory, "regional", seed("a", True), seed("b", False), mode="bgp", **profiles
    )


def snapshot(directory):
    return {
        str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_profile_intent_survives_interruption_and_exact_repeat(tmp_path, monkeypatch):
    directory = tmp_path / "pair"
    publish = pair_config._publish_exact

    def interrupt(path, content):
        if path.name == "b.config.yaml":
            raise ConnectionError("interrupted configuration publication")
        publish(path, content)

    profiles = {"profile_a": "regional-a", "profile_b": "regional-b"}
    monkeypatch.setattr(pair_config, "_publish_exact", interrupt)
    with pytest.raises(ConnectionError):
        create(directory, **profiles)
    before = snapshot(directory)
    monkeypatch.setattr(pair_config, "_publish_exact", publish)
    monkeypatch.setattr(
        pair_config.secrets, "token_urlsafe", lambda *a: pytest.fail("regenerated secret")
    )
    with pytest.raises(ValueError, match="seed intent changed"):
        create(directory, **{**profiles, "profile_a": "another"})
    assert snapshot(directory) == before
    path = create(directory, **profiles)
    manifest, _ = pair_config.load(path)
    assert manifest["operator_profiles"] == {"a": "regional-a", "b": "regional-b"}
    before = snapshot(directory)
    create(directory, **profiles)
    assert snapshot(directory) == before
    with pytest.raises(ValueError, match="seed intent changed"):
        create(directory)
    assert snapshot(directory) == before


@pytest.mark.parametrize("invalid", ["", "a b", "a\nb", "--option", "../a", "x" * 129, 1, True])
def test_malformed_profile_fails_before_any_publication(tmp_path, invalid):
    directory = tmp_path / "pair"
    with pytest.raises(ValueError, match="profile"):
        create(directory, profile_a=invalid)
    assert not directory.exists()


@pytest.mark.parametrize("changed", [{"a": "another", "b": None}, {"a": None}, None, []])
def test_manifest_profile_drift_fails_read_only(tmp_path, changed):
    path = create(tmp_path / "pair", profile_a="regional-a")
    manifest = json.loads(path.read_bytes())
    manifest["operator_profiles"] = changed
    path.write_text(json.dumps(manifest))
    before = snapshot(path.parent)
    with pytest.raises(ValueError, match="profile|assignments"):
        pair_config.load(path)
    assert snapshot(path.parent) == before


@pytest.mark.parametrize("explicit", [False, True])
def test_regional_environment_preserves_parent_and_inherited_auth(tmp_path, monkeypatch, explicit):
    path = create(tmp_path / "pair", profile_a="regional-a" if explicit else None)
    manifest, _ = pair_config.load(path)
    monkeypatch.setenv("NEBIUS_PROFILE", "parent-profile")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-only-token")
    monkeypatch.setenv("VPNGW_PAIR_CHILD_REQUEST", "stale-request")
    before = dict(os.environ)
    a = pair_apply.environment(manifest, "a")
    b = pair_apply.environment(manifest, "b")
    assert a["NEBIUS_PROFILE"] == ("regional-a" if explicit else "parent-profile")
    assert ("NEBIUS_IAM_TOKEN" not in a) if explicit else a["NEBIUS_IAM_TOKEN"] == "test-only-token"
    assert b["NEBIUS_PROFILE"] == "parent-profile"
    assert b["NEBIUS_IAM_TOKEN"] == "test-only-token"
    assert "VPNGW_PAIR_CHILD_REQUEST" not in a and "VPNGW_PAIR_CHILD_REQUEST" not in b
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "action,options",
    [("prep-network", ("--no-interactive",)), ("apply", ("--dry-run",)), ("status", ())],
)
def test_real_regional_command_selects_its_profile(tmp_path, monkeypatch, action, options):
    path = create(tmp_path / "pair", profile_a="regional-a", profile_b="regional-b")
    manifest, _ = pair_config.load(path)
    monkeypatch.setenv("NEBIUS_PROFILE", "parent-profile")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-only-token")
    # A real subprocess exercises the environment boundary without cloud effects.
    code = "import os,sys; assert os.environ['NEBIUS_PROFILE']==sys.argv[1]; assert 'NEBIUS_IAM_TOKEN' not in os.environ"
    for side in pair_config.SIDES:
        monkeypatch.setattr(
            pair_apply,
            "command",
            lambda side=side: [sys.executable, "-c", code, f"regional-{side}"],
        )
        pair_cli._run(manifest, path, side, action, *options)


def test_real_apply_and_repeat_use_each_profile(tmp_path, monkeypatch):
    path = prepared_pair(tmp_path, profiles={"profile_a": "regional-a", "profile_b": "regional-b"})
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"test candidate bytes")
    monkeypatch.setattr(pair_apply, "artifact", lambda: wheel)
    monkeypatch.setenv("NEBIUS_PROFILE", "parent-profile")
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-only-token")
    code = CHILD.replace(
        "child.save()",
        "assert os.environ['NEBIUS_PROFILE']=='regional-'+child.request['side']\nassert 'NEBIUS_IAM_TOKEN' not in os.environ\nchild.save()",
        1,
    )
    monkeypatch.setattr(pair_apply, "command", lambda: [sys.executable, "-c", code])
    first = pair_apply.apply(path, approvals={"a": None, "b": None})
    repeat = pair_apply.apply(path, approvals={"a": None, "b": None})
    assert first["phase"] == repeat["phase"] == "complete"
    assert repeat["previous"] == first["run"]


def test_public_create_config_persists_regional_profiles(tmp_path, monkeypatch):
    import yaml
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    for side in pair_config.SIDES:
        config = seed(side, True)
        config["gateway_group"]["vm_spec"].pop("ssh_public_key_path")
        config["gateway_group"]["vm_spec"]["ssh_public_key"] = public_key
        (tmp_path / f"{side}.yaml").write_text(yaml.safe_dump(config))
    result = CliRunner().invoke(
        app,
        [
            "pair",
            "create-config",
            str(tmp_path / "pair"),
            "--left",
            str(tmp_path / "a.yaml"),
            "--right",
            str(tmp_path / "b.yaml"),
            "--name",
            "regional",
            "--profile-a",
            "regional-a",
            "--profile-b",
            "regional-b",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest, _ = pair_config.load(tmp_path / "pair" / "pair.json")
    assert manifest["operator_profiles"] == {"a": "regional-a", "b": "regional-b"}


def test_interrupted_apply_rejects_changed_profile_even_if_pair_label_is_retained(
    tmp_path, monkeypatch
):
    path = prepared_pair(tmp_path, profiles={"profile_a": "regional-a"})
    wheel = tmp_path / "candidate.whl"
    wheel.write_bytes(b"test candidate bytes")
    monkeypatch.setattr(pair_apply, "artifact", lambda: wheel)
    monkeypatch.setattr(
        pair_apply, "command", lambda: [sys.executable, "-c", "raise SystemExit(1)"]
    )
    with pytest.raises(RuntimeError, match="did not complete"):
        pair_apply.apply(path, approvals={"a": None, "b": None})
    for target in (path, path.parent / pair_config.ASSIGNMENTS):
        value = json.loads(target.read_bytes())
        value["operator_profiles"]["a"] = "another-profile"
        target.write_text(json.dumps(value))
    before = snapshot(path.parent)
    monkeypatch.setattr(pair_apply, "command", lambda: pytest.fail("changed child was launched"))
    with pytest.raises(RuntimeError, match="Interrupted pair apply"):
        pair_apply.apply(path, approvals={"a": None, "b": None})
    assert snapshot(path.parent) == before


def test_missing_explicit_profile_fails_through_standard_auth_owner(tmp_path, monkeypatch):
    from nebius.aio.cli_config import Config, ConfigError
    from nebius.sdk import SDK

    from nebius_vpngw.nebius_auth import build_operator_sdk_client

    assert SDK  # Load SDK annotations before substituting only its config-file location.

    path = create(tmp_path / "pair", profile_a="missing-profile")
    manifest, _ = pair_config.load(path)
    config_file = tmp_path / "cli.yaml"
    config_file.write_text(
        "default: available\nprofiles:\n  available:\n    endpoint: api.example.invalid\n"
    )
    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "test-only-token")
    selected = pair_apply.environment(manifest, "a")
    monkeypatch.delenv("NEBIUS_IAM_TOKEN")
    monkeypatch.setenv("NEBIUS_PROFILE", selected["NEBIUS_PROFILE"])
    monkeypatch.delenv("NEBIUS_ENDPOINT", raising=False)
    monkeypatch.setattr(
        "nebius.aio.cli_config.Config", lambda **kw: Config(config_file=config_file, **kw)
    )
    with pytest.raises(ConfigError, match="profile"):
        build_operator_sdk_client(explicit_token=selected.get("NEBIUS_IAM_TOKEN"))
