from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli.notify_ops import InventoryEmailResult, send_inventory_email
from nebius_cxcli.paths import ProjectPaths


class FakeSMTP:
    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.messages: list[object] = []

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message: object) -> None:
        self.messages.append(message)


def _project_paths(tmp_path: Path) -> ProjectPaths:
    repo_root = tmp_path / "repo"
    generated_dir = (
        repo_root
        / "deployments"
        / "projects"
        / "client-a--tenant-123"
        / "project-456"
        / "generated"
    )
    project_dir = generated_dir.parent
    return ProjectPaths(
        config_path=project_dir / "config.yaml",
        repo_root=repo_root,
        deployments_dir=repo_root / "deployments",
        project_dir=project_dir,
        generated_dir=generated_dir,
        infra_dir=generated_dir / "infra",
        flux_dir=generated_dir / "flux",
        inventory_dir=generated_dir / "inventory",
        path_client_name="client-a",
        path_tenant_id="tenant-123",
        path_project_id="project-456",
    )


def _config(
    *, email: str | None = "ops@example.com", project_id: str = "project-456"
) -> SimpleNamespace:
    return SimpleNamespace(
        client_info=SimpleNamespace(
            notifications=SimpleNamespace(email_enabled=True, email=email),
            nebius=SimpleNamespace(project_id=project_id, tenant_id="tenant-123"),
        )
    )


def test_send_inventory_email_returns_false_when_email_not_configured(tmp_path: Path) -> None:
    paths = _project_paths(tmp_path)

    assert send_inventory_email(_config(email=None), paths) == InventoryEmailResult(
        sent=False,
        reason="recipient_missing",
        message="Inventory email enabled but `client_info.notifications.email` is empty; nothing sent.",
    )


def test_send_inventory_email_returns_false_when_email_notifications_disabled(
    tmp_path: Path,
) -> None:
    paths = _project_paths(tmp_path)
    config = _config()
    config.client_info.notifications.email_enabled = False

    assert send_inventory_email(config, paths) == InventoryEmailResult(
        sent=False,
        reason="disabled",
        message="Inventory email disabled (`client_info.notifications.email_enabled=false`); nothing sent.",
    )


def test_send_inventory_email_warns_when_smtp_host_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _project_paths(tmp_path)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    assert send_inventory_email(_config(), paths) == InventoryEmailResult(
        sent=False,
        reason="smtp_unconfigured",
        message=(
            "Inventory email enabled but SMTP is not configured. "
            "Run `nebius-cxcli email --setup` and `nebius-cxcli bootstrap-ci <config.yaml>` "
            "to sync the GitHub environment, or set SMTP_HOST."
        ),
    )


def test_send_inventory_email_uses_local_smtp_settings_when_env_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    (paths.inventory_dir / "inventory.md").write_text("# Inventory\n\nBody\n", encoding="utf-8")

    sent: list[FakeSMTP] = []

    def _fake_smtp(host: str, port: int, timeout: int) -> FakeSMTP:
        smtp = FakeSMTP(host, port, timeout)
        sent.append(smtp)
        return smtp

    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_STARTTLS", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr("nebius_cxcli.notify_ops.smtplib.SMTP", _fake_smtp)

    assert send_inventory_email(
        _config(),
        paths,
        smtp_settings={
            "host": "smtp.catalog.example.com",
            "port": 2525,
            "starttls": False,
            "from": "catalog@example.com",
        },
    ) == InventoryEmailResult(sent=True, reason="sent", message="Inventory email sent")

    smtp = sent[0]
    assert smtp.host == "smtp.catalog.example.com"
    assert smtp.port == 2525
    assert smtp.started_tls is False
    assert smtp.logged_in is None
    message = smtp.messages[0]
    assert message["From"] == "catalog@example.com"


def test_send_inventory_email_uses_inventory_markdown_and_smtp_login(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    (paths.inventory_dir / "inventory.md").write_text("# Inventory\n\nBody\n", encoding="utf-8")

    sent: list[FakeSMTP] = []

    def _fake_smtp(host: str, port: int, timeout: int) -> FakeSMTP:
        smtp = FakeSMTP(host, port, timeout)
        sent.append(smtp)
        return smtp

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "deployments@example.com")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    monkeypatch.setattr("nebius_cxcli.notify_ops.smtplib.SMTP", _fake_smtp)

    assert send_inventory_email(_config(), paths) == InventoryEmailResult(
        sent=True,
        reason="sent",
        message="Inventory email sent",
    )

    smtp = sent[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 2525
    assert smtp.timeout == 20
    assert smtp.started_tls is True
    assert smtp.logged_in == ("mailer", "secret")
    message = smtp.messages[0]
    assert message["Subject"] == "Nebius inventory: *******-456"
    assert message["From"] == "deployments@example.com"
    assert message["To"] == "ops@example.com"
    assert "# Inventory" in message.get_content()


def test_send_inventory_email_requires_inventory_markdown_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _project_paths(tmp_path)

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="Inventory markdown is missing"):
        send_inventory_email(_config(project_id=""), paths)


def test_send_inventory_email_masks_project_and_tenant_ids_in_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _project_paths(tmp_path)
    paths.inventory_dir.mkdir(parents=True, exist_ok=True)
    (paths.inventory_dir / "inventory.md").write_text(
        "# Inventory: project-456\n\n- Tenant: `tenant-123`\n- Project: `project-456`\n",
        encoding="utf-8",
    )

    sent: list[FakeSMTP] = []

    def _fake_smtp(host: str, port: int, timeout: int) -> FakeSMTP:
        smtp = FakeSMTP(host, port, timeout)
        sent.append(smtp)
        return smtp

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_STARTTLS", "false")
    monkeypatch.setattr("nebius_cxcli.notify_ops.smtplib.SMTP", _fake_smtp)

    assert send_inventory_email(_config(project_id="project-456"), paths) == InventoryEmailResult(
        sent=True,
        reason="sent",
        message="Inventory email sent",
    )

    message = sent[0].messages[0]
    content = message.get_content()
    assert "tenant-123" not in content
    assert "project-456" not in content
    assert "******-123" in content
    assert "*******-456" in content
