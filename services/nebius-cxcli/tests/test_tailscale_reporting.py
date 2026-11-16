"""Offline acceptance of complete gateway coverage and separate client evidence."""

import copy
import json
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import tailscale_deploy, tailscale_network, tailscale_verify
from nebius_cxcli.deploy_validation_report import build_deploy_validation_report
from nebius_cxcli.deployment_reports import publish_deployment_reports
from nebius_cxcli.tailscale_config import VERSION, members
from nebius_cxcli.tailscale_verify import verify as verify_client
from test_deployment_campaign import paths
from test_tailscale import accept, healthy, row


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """Real completion/reporting, with only cloud, guest and journal I/O replaced."""

    def create(*, replicas=2, stages=("active",), failures=()):
        rows, records, clouds, guests = [], {}, {}, {}
        for index, stage in enumerate(stages):
            cfg = row()
            instance = f"router-{index}"
            cfg["instance_id"] = instance
            cfg["inputs"]["replicas"] = replicas
            cfg["tailscale"]["stage"] = stage
            if stage == "active":
                cfg["tailscale"]["verification"] = {
                    "targets": [{"url": "https://private.example/readyz"}]
                }
            accept(cfg)
            rows.append(cfg)
            own, cloud, machines = {}, {}, {}
            for member in members(cfg):
                binding = {
                    "instance_id": f"vm-{instance}-{member}",
                    "boot_disk_id": f"disk-{instance}-{member}",
                    "public_allocation_id": f"ip-{instance}-{member}",
                    "network_id": "network-test",
                    "subnet_id": "subnet-test",
                }
                cloud[member] = binding
                node = f"node-{instance}-{member}"
                own[member] = {
                    **binding,
                    "host_public_key": "ssh-ed25519 fixture",
                    "bootstrap_sha256": "fixture",
                    **({"node_id": node} if stage == "active" else {}),
                }
                observed = (
                    {**healthy(member), "node_id": node}
                    if stage == "active"
                    else {"forwarding": True, "version": VERSION}
                )

                def install(_asset, identity=(instance, member)):
                    if identity in failures:
                        raise RuntimeError("Simulated guest failure")

                machines[member] = NS(
                    install=install,
                    call=lambda _operation, result=observed, **_kwargs: copy.deepcopy(result),
                )
            records[instance] = {
                "tailnet": cfg["tailscale"]["tailnet"],
                "members": own,
                "activation_started": stage == "active",
                "infrastructure_ready": {"members": {}},
            }
            clouds[instance], guests[instance] = cloud, machines
        source = paths(tmp_path / "source")
        owner = tailscale_deploy.TailscaleDeployment(
            NS(
                config={"infra": {"components": rows}},
                paths=source,
                report_paths=source,
                options=NS(dry_run=False),
                manifest={},
            )
        )
        journal = NS(value={"gateways": records}, assert_held=lambda: None, save=lambda: None)
        owner.open = lambda: journal
        owner.raw_outputs = lambda: clouds
        owner.guests = lambda cfg, *_: guests[cfg["instance_id"]]
        owner.api = lambda _: None
        monkeypatch.setattr(tailscale_deploy, "outputs", lambda raw, instance: raw[instance])
        monkeypatch.setattr(tailscale_deploy, "asset", lambda *_: {})
        monkeypatch.setattr(
            tailscale_deploy, "ComputeHostIdentityClient", lambda *_: nullcontext(NS())
        )
        monkeypatch.setattr(tailscale_network, "validate_live", lambda *_: None)
        monkeypatch.setattr(
            tailscale_verify, "verify", lambda *_args, **_kwargs: {"status": "PASS"}
        )
        return owner, source

    return create


def read_report(source):
    return json.loads((source.reports_dir / "tailscale-report.json").read_text())


def test_second_member_failure_retains_pending_coverage_and_published_summary(deployment, tmp_path):
    owner, source = deployment(failures={("router-0", "b")})
    with pytest.raises(RuntimeError, match="Simulated guest failure"):
        owner.complete()
    report = read_report(source)
    assert report["gateway_ready"] is False
    assert [(r["member"], r["status"]) for r in report["members"]] == [
        ("a", "READY"),
        ("b", "PENDING"),
    ]
    (source.reports_dir / "deploy-report.md").write_text("Deployment\n")
    destination = paths(tmp_path / "published")
    publish_deployment_reports(
        source,
        destination,
        [],
        build_deploy_validation_report([], reports_dir=source.reports_dir),
        failure=RuntimeError("failed"),
    )
    assert (
        json.loads((destination.reports_dir / "deploy-report.json").read_text())["tailscale"][
            "gateway_ready"
        ]
        is False
    )
    assert "Gateway readiness pending" in (destination.reports_dir / "deploy-report.md").read_text()


@pytest.mark.parametrize("replicas", [1, 2])
def test_complete_member_coverage_is_ready(deployment, replicas):
    owner, source = deployment(replicas=replicas)
    owner.complete()
    report = read_report(source)
    assert report["gateway_ready"] is True
    assert len(report["members"]) == replicas
    assert all(r["ha_configured"] == (replicas == 2) for r in report["members"])


@pytest.mark.parametrize("member", ["a", "b"])
def test_later_component_failure_preserves_earlier_client_evidence(deployment, member):
    owner, source = deployment(stages=("active", "active"), failures={("router-1", member)})
    with pytest.raises(RuntimeError, match="Simulated guest failure"):
        owner.complete()
    report = read_report(source)
    assert report["gateway_ready"] is False
    assert len(report["members"]) == 4
    assert report["client"] == [{"component": "router-0", "status": "PASS"}]


def test_cloud_read_failure_replaces_stale_success_with_pending(deployment):
    owner, source = deployment()
    owner.complete()
    assert read_report(source)["gateway_ready"] is True

    def unavailable():
        raise RuntimeError("Cloud outputs unavailable")

    owner.raw_outputs = unavailable
    with pytest.raises(RuntimeError, match="Cloud outputs"):
        owner.complete()
    report = read_report(source)
    assert report["gateway_ready"] is False
    assert [r["status"] for r in report["members"]] == ["PENDING", "PENDING"]
    assert report["client"][0]["status"] == "PENDING"


def test_mixed_stages_keep_onboarding_separate(deployment):
    owner, source = deployment(stages=("active", "infrastructure"))
    owner.complete()
    report = read_report(source)
    assert report["gateway_ready"] is False
    assert report["onboarding_pending"] is True
    assert [r["status"] for r in report["members"]] == [
        "READY",
        "READY",
        "INFRASTRUCTURE_READY",
        "INFRASTRUCTURE_READY",
    ]
    assert [r["status"] for r in report["client"]] == ["PASS", "NOT_RUN"]


@pytest.mark.parametrize(
    "identities",
    [
        [("router-0", "a")],
        [("router-0", "a"), ("router-0", "a")],
        [("router-0", "a"), ("router-0", "b"), ("other", "a")],
        [],
    ],
)
def test_report_writer_rejects_incomplete_duplicate_or_unexpected_coverage(deployment, identities):
    owner, source = deployment()
    owner.write_report([{"component": c, "member": m, "status": "READY"} for c, m in identities])
    assert read_report(source)["gateway_ready"] is False


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("boundary", ["status", "route"])
def test_client_timeout_respects_required_and_preserves_router_evidence(
    deployment, monkeypatch, required, boundary
):
    owner, source = deployment()
    owner.rows[0]["tailscale"]["verification"]["required"] = required
    # Keep real verify/client_status; only the local executable transport times out.
    monkeypatch.setattr(tailscale_verify, "verify", verify_client)
    monkeypatch.setattr(tailscale_verify.shutil, "which", lambda _: "/test/tailscale")
    monkeypatch.setattr(tailscale_verify.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        tailscale_verify.socket,
        "getaddrinfo",
        lambda *_a, **_k: [(2, 1, 6, "", ("10.20.0.1", 443))],
    )

    def timeout(args, **_kwargs):
        if boundary == "route" and args[1:] == ["status", "--json"]:
            return NS(
                returncode=0,
                stdout=json.dumps(
                    {
                        "BackendState": "Running",
                        "CurrentTailnet": {"Name": "example.ts.net"},
                        "Peer": {
                            "fixture": {
                                "ID": "node-router-0-a",
                                "PrimaryRoutes": ["10.20.0.0/24"],
                                "TailscaleIPs": ["100.64.0.1"],
                            }
                        },
                    }
                ),
            )
        raise subprocess.TimeoutExpired(args, 15, output=b"PRIVATE-SENTINEL")

    monkeypatch.setattr(tailscale_verify.subprocess, "run", timeout)
    if required:
        with pytest.raises(RuntimeError, match="Required Tailscale client verification"):
            owner.complete()
    else:
        owner.complete()
    report = read_report(source)
    assert report["gateway_ready"] is True
    assert report["client"][0]["status"] == "PENDING"
    assert "PRIVATE-SENTINEL" not in json.dumps(report)
