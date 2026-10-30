import json
import shutil

import pytest

from nebius_cxcli.deploy_validation_report import build_deploy_validation_report
from nebius_cxcli.deployment_reports import publish_deployment_reports
from test_deployment_campaign import paths


def test_tailscale_infrastructure_report_does_not_claim_gateway_ready(tmp_path):
    source, destination = paths(tmp_path / "private"), paths(tmp_path / "workspace")
    source.reports_dir.mkdir(parents=True)
    (source.reports_dir / "tailscale-report.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "gateway_ready": False,
                "onboarding_pending": True,
                "members": [{"member": "a", "status": "INFRASTRUCTURE_READY"}],
            }
        )
    )
    (source.reports_dir / "deploy-report.md").write_text("Infrastructure validation complete.\n")
    report = build_deploy_validation_report([], reports_dir=source.reports_dir)
    publish_deployment_reports(source, destination, [], report)
    summary = json.loads((destination.reports_dir / "deploy-report.json").read_text())
    assert summary["tailscale"]["gateway_ready"] is False
    assert summary["tailscale"]["onboarding_pending"] is True
    assert (
        "administrator handoff pending"
        in (destination.reports_dir / "deploy-report.md").read_text()
    )


def test_published_failure_summary_survives_private_cache_and_excludes_raw_logs(tmp_path):
    source, destination = paths(tmp_path / "private"), paths(tmp_path / "workspace")
    source.reports_dir.mkdir(parents=True)
    specs = [{"kind": "soperator_cluster_smoke", "report_file": "result.json"}]
    (source.reports_dir / "result.json").write_text(
        json.dumps(
            {
                "passed": False,
                "summary": "Native diagnostic failed.",
                "stdout": "PRIVATE-LOG-SENTINEL",
                "token": "SECRET-SENTINEL",
            }
        )
    )
    (source.reports_dir / "receipt.json").write_text("PRIVATE-RECEIPT")
    (source.reports_dir / "deploy-report.md").write_text(
        f"Detail: {source.reports_dir / 'result.json'}\n"
    )
    report = build_deploy_validation_report(specs, reports_dir=source.reports_dir)
    published = publish_deployment_reports(source, destination, specs, report)
    shutil.rmtree(source.project_dir)
    assert published.overall_status == "failed"
    assert published.markdown_path.exists()
    assert published.results[0].report_path.exists()
    assert {p.name for p in destination.reports_dir.iterdir()} == {
        "deploy-report.md",
        "deploy-report.json",
    }
    for path in destination.reports_dir.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert "SENTINEL" not in path.read_text()
        assert str(source.project_dir) not in path.read_text()
    assert "deploy-report.json" in published.markdown_path.read_text()


@pytest.mark.parametrize("unsafe", ["../escape.json", "/tmp/escape.json"])
def test_report_publication_rejects_nonlocal_names(tmp_path, unsafe):
    with pytest.raises(RuntimeError, match="local filename"):
        publish_deployment_reports(
            paths(tmp_path / "source"),
            paths(tmp_path / "destination"),
            [{"kind": "soperator_cluster_smoke", "report_file": unsafe}],
            None,
        )


def test_report_publication_rejects_symlink_destination(tmp_path):
    source, destination = paths(tmp_path / "source"), paths(tmp_path / "destination")
    destination.generated_dir.mkdir()
    (tmp_path / "foreign").mkdir()
    destination.reports_dir.symlink_to(tmp_path / "foreign", target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlinked"):
        publish_deployment_reports(source, destination, [], None)
    assert list((tmp_path / "foreign").iterdir()) == []


def test_failed_native_identity_is_retained_without_raw_error_payloads(tmp_path):
    from nebius_cxcli.soperator_checks import SoperatorAcceptanceJobError

    source, destination = paths(tmp_path / "private"), paths(tmp_path / "workspace")
    source.reports_dir.mkdir(parents=True)
    (source.reports_dir / "deploy-report.md").write_text("Partial results\n")
    report = build_deploy_validation_report([], reports_dir=source.reports_dir)
    failure = SoperatorAcceptanceJobError(
        job_id="7", name="native-check", state="FAILED", exit_code="125:0"
    )
    published = publish_deployment_reports(source, destination, [], report, failure=failure)
    assert "state=FAILED, exit=125:0" in published.markdown_path.read_text()
    evidence = json.loads((destination.reports_dir / "deploy-report.json").read_text())[
        "execution_failure"
    ]
    assert evidence == {
        "job_id": "7",
        "job_name": "native-check",
        "state": "FAILED",
        "exit_code": "125:0",
    }


def test_current_pending_replaces_prior_pass_in_combined_summary(tmp_path):
    from nebius_cxcli.wireguard_verification import write_report

    source, destination = paths(tmp_path / "private"), paths(tmp_path / "workspace")
    source.reports_dir.mkdir(parents=True)
    (source.reports_dir / "deploy-report.md").write_text("Infrastructure results\n")
    report = build_deploy_validation_report([], reports_dir=source.reports_dir)
    for status in ("PASS", "PENDING"):
        write_report(source.reports_dir, [{"status": status, "reason": "fixture"}])
        publish_deployment_reports(source, destination, [], report)
        combined = json.loads((destination.reports_dir / "deploy-report.json").read_text())
        assert combined["wireguard_client_verification"]["status"] == status
        assert combined["infrastructure_status"] == report.overall_status
