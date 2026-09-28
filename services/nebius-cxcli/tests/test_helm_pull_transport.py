from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli import helm_client


@pytest.fixture
def pull_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    roots: list[Path] = []
    original_mkdtemp = helm_client.tempfile.mkdtemp

    def create(**kwargs: object) -> str:
        root = original_mkdtemp(**kwargs, dir=tmp_path)
        roots.append(Path(root))
        return root

    monkeypatch.setattr(helm_client.tempfile, "mkdtemp", create)
    monkeypatch.setattr(helm_client.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(helm_client, "_repo_has_index", lambda _repo: True)
    return roots


def write_chart(root: Path) -> Path:
    chart = root / "demo"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: demo\nversion: 1.0.0\n")
    (chart / "values.yaml").write_text("{}\n")
    (chart / "templates" / "configmap.yaml").write_text("kind: ConfigMap\n")
    return chart


@pytest.mark.parametrize("failure", ["reset", "timeout"])
@pytest.mark.parametrize("repo", ["", "https://charts.example.test"])
def test_materialization_recovers_with_fresh_directory(
    monkeypatch: pytest.MonkeyPatch, pull_roots: list[Path], failure: str, repo: str
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        root = Path(command[command.index("--untardir") + 1])
        assert command[:2] == ["helm", "pull"]
        assert command[command.index("--version") + 1] == "1.0.0"
        if repo:
            assert command[command.index("--repo") + 1] == repo
        if len(commands) == 1:
            (root / "partial").mkdir()
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 120, stderr=b"private transport detail")
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr='Error: failed to perform "Push" on destination: read failed: '
                "read tcp 192.0.2.1:57873->192.0.2.2:443: read: connection reset by peer",
            )
        assert not pull_roots[0].exists()
        assert not list(root.iterdir())
        write_chart(root)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(helm_client.kubernetes_process, "run", run)
    reference = helm_client.HelmChartReference(
        chart_name="demo" if repo else "oci://registry.example.test/demo",
        chart_repo=repo,
        chart_version="1.0.0",
    )
    with helm_client._materialize_chart_dir(reference) as chart:
        assert (chart / "Chart.yaml").is_file()
        assert len(commands) == 2
        assert pull_roots[1].exists()
    assert all(not root.exists() for root in pull_roots)


@pytest.mark.parametrize("failure", ["reset", "timeout"])
def test_pull_exhaustion_is_bounded_clean_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, pull_roots: list[Path], failure: str
) -> None:
    timeouts: list[int] = []

    def run(command: list[str], *, timeout: int, **_kwargs: object) -> SimpleNamespace:
        timeouts.append(timeout)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout, stderr=b"private transport detail")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr='Get "https://storage.example.test/blob?X-Amz-Signature=test-signature": '
            "read: connection reset by peer",
        )

    monkeypatch.setattr(helm_client.kubernetes_process, "run", run)
    monkeypatch.setenv(helm_client.HELM_TIMEOUT_ENV, "7")
    with pytest.raises(RuntimeError, match="3 attempts") as error:
        helm_client._run_helm_pull("oci://registry.example.test/demo", version="1.0.0")
    assert timeouts == [7, 7, 7]
    assert all(not root.exists() for root in pull_roots)
    assert "test-signature" not in str(error.value)
    assert "private transport detail" not in str(error.value)
    assert error.value.__context__ is None
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "failure", ["unauthorized", "certificate expired", "not found", "digest mismatch"]
)
def test_pull_permanent_failure_is_immediate_clean_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, pull_roots: list[Path], failure: str
) -> None:
    monkeypatch.setattr(
        helm_client.kubernetes_process,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=f"{failure}: https://storage.example.test/blob?X-Amz-Signature=test-signature",
        ),
    )
    with pytest.raises(RuntimeError, match=failure) as error:
        helm_client._run_helm_pull("oci://registry.example.test/demo", version="1.0.0")
    assert len(pull_roots) == 1 and not pull_roots[0].exists()
    assert "test-signature" not in str(error.value)


@pytest.mark.parametrize("failure", [KeyboardInterrupt, OSError])
def test_pull_cleans_temporary_directory_on_interruption_or_launch_failure(
    monkeypatch: pytest.MonkeyPatch, pull_roots: list[Path], failure: type[BaseException]
) -> None:
    def run(*_args: object, **_kwargs: object) -> None:
        raise failure()

    monkeypatch.setattr(helm_client.kubernetes_process, "run", run)
    with pytest.raises(failure):
        helm_client._run_helm_pull("oci://registry.example.test/demo", version="1.0.0")
    assert len(pull_roots) == 1 and not pull_roots[0].exists()


def test_pull_rejects_malformed_success_without_retry(
    monkeypatch: pytest.MonkeyPatch, pull_roots: list[Path]
) -> None:
    monkeypatch.setattr(
        helm_client.kubernetes_process,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    with pytest.raises(RuntimeError, match="exactly one chart directory"):
        helm_client._run_helm_pull("oci://registry.example.test/demo", version="1.0.0")
    assert len(pull_roots) == 1 and not pull_roots[0].exists()


def test_contract_validation_rechecks_failed_materialization(
    monkeypatch: pytest.MonkeyPatch, pull_roots: list[Path]
) -> None:
    failing = True

    def run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if failing:
            return SimpleNamespace(returncode=1, stdout="", stderr="connection reset by peer")
        write_chart(Path(command[command.index("--untardir") + 1]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(helm_client.kubernetes_process, "run", run)
    arguments = dict(
        chart_name="demo",
        chart_repo="oci://registry.example.test/cache-demo",
        chart_version="1.0.0",
    )
    issues, _ = helm_client.chart_cli_contract_findings(**arguments)
    assert any("could not materialize" in issue for issue in issues)
    failing = False
    issues, warnings = helm_client.chart_cli_contract_findings(**arguments)
    assert issues == () and warnings == ()
    successful_calls = len(pull_roots)
    assert helm_client.chart_cli_contract_findings(**arguments) == ((), ())
    assert len(pull_roots) == successful_calls
    assert all(not root.exists() for root in pull_roots)
