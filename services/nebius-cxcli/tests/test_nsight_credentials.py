import base64
import io
import json

import pytest
import typer
from typer.testing import CliRunner

from nebius_cxcli import nsight_credentials as credentials


class Kube:
    def __init__(self, secret=None, failure=False):
        self.secret = secret
        self.failure = failure
        self.writes = []

    def run(self, args, *, input_text=None):
        if "create" in args:
            payload = json.loads(input_text)
            self.writes.append(payload)
            self.secret = {
                "metadata": {**payload["metadata"], "uid": "secret-uid"},
                "data": {
                    k: base64.b64encode(v.encode()).decode()
                    for k, v in payload["stringData"].items()
                },
            }
            if self.failure:
                raise RuntimeError("lost response")
            return "secret/login"
        if "go-template" in args[-1]:
            return "login\nusername\npassword\n"
        return json.dumps(self.secret) if self.secret else ""


def prepare(monkeypatch, kube, value="sample-private-password\n", **kwargs):
    monkeypatch.setattr(credentials, "NsightKubernetes", lambda _: kube)
    monkeypatch.setattr(credentials.sys, "stdin", io.StringIO(value))
    credentials.prepare_login_credentials(
        env={},
        name="login",
        username="admin",
        interactive=False,
        password_stdin=True,
        fence=lambda: None,
        **kwargs,
    )


@pytest.mark.parametrize("lost_response", [False, True])
def test_stdin_creation_and_equal_replay_preserve_secret(monkeypatch, capsys, lost_response):
    kube = Kube(failure=lost_response)
    prepare(monkeypatch, kube)
    original = json.dumps(kube.secret)
    prepare(monkeypatch, kube)
    assert len(kube.writes) == 1 and json.dumps(kube.secret) == original
    assert kube.writes[0]["stringData"]["username"] == "admin"
    assert capsys.readouterr().out == ""


def test_different_existing_credentials_are_never_changed(monkeypatch):
    kube = Kube()
    prepare(monkeypatch, kube)
    before = json.dumps(kube.secret)
    with pytest.raises(RuntimeError, match="credentials were not changed") as error:
        prepare(monkeypatch, kube, "different-private-password")
    assert json.dumps(kube.secret) == before and len(kube.writes) == 1
    assert "different-private-password" not in str(error.value)


@pytest.mark.parametrize("value", ["", "\n", " \t\n", "\u2003", "two\nlines", "a" * 4097, "x\0y"])
def test_invalid_input_never_creates_secret(monkeypatch, value):
    kube = Kube()
    with pytest.raises(ValueError, match="password must"):
        prepare(monkeypatch, kube, value)
    assert kube.writes == []


def test_masked_wizard_defaults_admin_and_reuses_existing_without_prompt(monkeypatch):
    kube = Kube()
    monkeypatch.setattr(credentials, "NsightKubernetes", lambda _: kube)
    stream = io.StringIO()
    monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setattr(credentials.sys, "stdin", stream)
    calls = []

    def prompt(label, **kwargs):
        calls.append((label, kwargs))
        return "admin" if "username" in label else "sample-private-password"

    monkeypatch.setattr(credentials.typer, "prompt", prompt)
    for _ in range(2):
        credentials.prepare_login_credentials(
            env={},
            name="login",
            username="admin",
            interactive=True,
            password_stdin=False,
            fence=lambda: None,
        )
    assert calls[0][1] == {"default": "admin"}
    assert calls[1][1] == {"hide_input": True, "confirmation_prompt": True}
    assert len(calls) == 2 and len(kube.writes) == 1


def test_secret_read_failure_is_redacted(monkeypatch):
    class Forbidden(Kube):
        def run(self, *a, **kw):
            raise RuntimeError("secret-private-value")

    with pytest.raises(RuntimeError, match="check access") as error:
        prepare(monkeypatch, Forbidden())
    assert "secret-private-value" not in str(error.value)


def test_wizard_retries_blank_and_mismatched_passwords_without_echo(monkeypatch):
    kube = Kube()
    monkeypatch.setattr(credentials, "NsightKubernetes", lambda _: kube)
    app = typer.Typer()

    @app.command()
    def wizard():
        monkeypatch.setattr(credentials.sys.stdin, "isatty", lambda: True)
        credentials.prepare_login_credentials(
            env={},
            name="login",
            username="admin",
            interactive=True,
            password_stdin=False,
            fence=lambda: None,
        )

    password = "  synthetic password $!  "
    result = CliRunner().invoke(
        app, [], input=f"\n\n   \n   \n{password}\nmismatch\n{password}\n{password}\n"
    )
    assert result.exit_code == 0, result.output
    assert "[admin]" in result.output
    assert "password must be nonblank" in result.output
    assert "do not match" in result.output
    assert password not in result.output and "mismatch" not in result.output
    assert kube.writes[0]["stringData"] == {"username": "admin", "password": password}
    assert len(kube.writes) == 1


def test_stdin_preserves_nonblank_password_bytes(monkeypatch):
    kube = Kube()
    password = "  synthetic password\t "
    prepare(monkeypatch, kube, password + "\r\n")
    assert kube.writes[0]["stringData"]["password"] == password


def test_existing_secret_is_reused_in_headless_wizard(monkeypatch):
    kube = Kube()
    prepare(monkeypatch, kube)
    before = json.dumps(kube.secret)
    monkeypatch.setattr(credentials.typer, "prompt", lambda *a, **kw: pytest.fail("prompted"))
    credentials.prepare_login_credentials(
        env={},
        name="login",
        username="admin",
        interactive=True,
        password_stdin=False,
        fence=lambda: None,
    )
    assert json.dumps(kube.secret) == before and len(kube.writes) == 1


@pytest.mark.parametrize("value", [None, "", " \t"])
def test_missing_or_blank_runtime_password_is_invalid(value):
    with pytest.raises(ValueError, match="nonblank"):
        credentials._validate_password(value)
