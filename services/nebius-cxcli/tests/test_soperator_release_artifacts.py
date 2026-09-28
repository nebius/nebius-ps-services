from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import nebius_cxcli.soperator_release_artifacts as release_artifacts
from nebius_cxcli.soperator_release_artifacts import (
    _cache_chart_package,
    _run,
)
from soperator_fixtures import sample_snapshot


def test_final_consumers_replay_saved_patches_and_bind_generated_jail_storage(monkeypatch):
    import copy
    import shutil

    import yaml

    from nebius_cxcli.soperator_jail_logs_binding import _SOURCE_AFFINITY, JAIL_LOGS_RELEASE
    from nebius_cxcli.soperator_release import (
        SOPERATOR_MAIN_RELEASE_NAME,
        seal_soperator_release_snapshot,
    )
    from soperator_fixtures import sample_jail_logs_binding

    if not shutil.which("kustomize") and not shutil.which("kubectl"):
        pytest.skip("Kustomize is required to validate final consumers")
    values, storage = sample_jail_logs_binding()
    values["observability"]["publicEndpointEnabled"] = False
    storage[1]["spec"]["local"]["path"] = "/mnt/jail-store/rootfs/slot-b"
    snapshot = sample_snapshot(release_names=(SOPERATOR_MAIN_RELEASE_NAME, JAIL_LOGS_RELEASE))
    snapshot = seal_soperator_release_snapshot(
        replace(
            snapshot,
            post_render_patches=(
                {
                    "target": {"kind": "HelmRelease", "name": "disabled-helper"},
                    "patch": "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata:\n  name: disabled-helper\n$patch: delete\n",
                },
            ),
            snapshot_sha256="",
        )
    )
    repository = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": "soperator", "namespace": "flux-system"},
        "spec": {"url": snapshot.registry, "type": "oci"},
    }
    consumer = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": JAIL_LOGS_RELEASE, "namespace": "flux-system"},
        "spec": {
            "chart": {
                "spec": {
                    "chart": snapshot.umbrella.name,
                    "version": snapshot.release,
                    "sourceRef": {"kind": "HelmRepository", "name": "soperator"},
                }
            },
            "values": {
                "affinity": copy.deepcopy(_SOURCE_AFFINITY),
                "extraVolumes": [
                    {"name": "jail", "hostPath": {"path": "/mnt/jail", "type": "Directory"}}
                ],
            },
        },
    }
    main = copy.deepcopy(consumer)
    main["metadata"]["name"] = SOPERATOR_MAIN_RELEASE_NAME
    main["spec"]["values"] = {}
    consumer["spec"]["dependsOn"] = [{"name": SOPERATOR_MAIN_RELEASE_NAME}]
    helper = copy.deepcopy(consumer)
    helper["metadata"]["name"] = "disabled-helper"
    documents = [repository, main, consumer, helper]
    monkeypatch.setattr(
        release_artifacts, "render_soperator_source_documents", lambda *a, **k: tuple(documents)
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_release_resolver._render_upstream_umbrella",
        lambda *a, **k: documents,
    )
    final = release_artifacts.render_soperator_consumers(
        snapshot,
        SimpleNamespace(source_dir="unused"),
        values,
        adapter_documents=storage,
    )
    assert {doc["metadata"]["name"] for doc in final} == {
        SOPERATOR_MAIN_RELEASE_NAME,
        JAIL_LOGS_RELEASE,
    }
    final = [doc for doc in final if doc["metadata"]["name"] == JAIL_LOGS_RELEASE]
    assert (
        final[0]["spec"]["values"]["extraVolumes"][0]["hostPath"]["path"]
        == "/mnt/jail-store/rootfs/slot-b"
    )
    assert "chart" not in final[0]["spec"]
    assert (
        len(
            final[0]["spec"]["values"]["affinity"]["nodeAffinity"][
                "requiredDuringSchedulingIgnoredDuringExecution"
            ]["nodeSelectorTerms"][0]["matchExpressions"]
        )
        == 2
    )
    # The saved deletion is part of replay authority, and omissions require a declared phase.
    without_patch = replace(snapshot, post_render_patches=())
    with pytest.raises(ValueError):
        release_artifacts._bind_rendered_consumers(
            without_patch,
            documents,
            values,
            source_documents=documents,
            adapter_documents=storage,
        )
    with pytest.raises(ValueError, match="admitted operation stages"):
        release_artifacts._verify_rendered_release_graph(
            yaml.safe_dump_all([repository, main]).encode(), snapshot
        )


@pytest.mark.parametrize("changed_file", [None, "values.yaml", "templates/tests/connect.yaml"])
def test_source_equality_rejects_registry_host_changes(tmp_path, monkeypatch, changed_file):
    """A registry relocation is a changed artifact, even with the same chart version."""
    snapshot = sample_snapshot()
    chart = replace(snapshot.umbrella, name="helm-nfs-server")
    snapshot = replace(snapshot, charts={"umbrella": chart}, third_party_charts={})
    source = SimpleNamespace(
        release=snapshot.release,
        manifest_sha256=snapshot.source_manifest_sha256,
        source_dir=str(tmp_path),
    )

    def package(name, changed):
        path = tmp_path / name
        files = {
            "Chart.yaml": b"apiVersion: v2\nname: helm-nfs-server\nversion: 1.2.0\n",
            "values.yaml": b"image: registry.example.invalid/nfs:1.0\n",
            "templates/tests/connect.yaml": b"image: registry.example.invalid/test:1.0\n",
        }
        if changed:
            files[changed] = files[changed].replace(
                b"registry.example.invalid", b"regional.example.invalid"
            )
        with tarfile.open(path, "w:gz") as bundle:
            for filename, content in files.items():
                member = tarfile.TarInfo("nfs/" + filename)
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
        return path

    original = package("source.tgz", None)
    official = package("official.tgz", changed_file)
    monkeypatch.setattr(release_artifacts.shutil, "which", lambda _: "helm")
    monkeypatch.setattr(release_artifacts, "_cache_chart_package", lambda **kw: official)
    monkeypatch.setattr(
        release_artifacts, "_package_verified_source_chart", lambda *a, **kw: original
    )
    renders = []

    def render(command, **kw):
        renders.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="same render", stderr="")

    monkeypatch.setattr(release_artifacts, "_run", render)
    if changed_file:
        with pytest.raises(
            ValueError, match="official OCI chart helm-nfs-server differs from release source"
        ):
            release_artifacts.verify_soperator_release_artifacts(
                snapshot, source, cache_root=tmp_path / "cache"
            )
        assert not renders
    else:
        receipt = release_artifacts.verify_soperator_release_artifacts(
            snapshot, source, cache_root=tmp_path / "cache"
        )
        assert receipt.chart_package_sha256 == (chart.package_sha256,)
        assert len(renders) == 2


def test_child_validation_checks_third_party_schema_and_hides_diagnostics(tmp_path):
    import json
    import shutil

    if not shutil.which("helm"):
        pytest.skip("helm is required to verify child schemas")
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: example\nversion: 1.0.0\n")
    (chart / "values.yaml").write_text("replicas: 1\n")
    (chart / "values.schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"replicas": {"type": "integer"}},
            }
        )
    )
    lock = SimpleNamespace(
        release_graph=(
            SimpleNamespace(
                release_name="dependency",
                chart_key="certManager",
                owner="third-party",
            ),
        )
    )
    consumers = (
        {
            "metadata": {"name": "dependency"},
            "spec": {
                "releaseName": "example",
                "targetNamespace": "consumer",
                "values": {"replicas": "SENSITIVE"},
            },
        },
    )
    with pytest.raises(ValueError, match="certManager chart rejected") as error:
        release_artifacts._validate_child_renders(
            lock,
            consumers,
            {"certManager": chart},
            helm="helm",
            directory=tmp_path,
        )
    assert "SENSITIVE" not in str(error.value)
    consumers[0]["spec"]["values"] = {"replicas": 2}
    release_artifacts._validate_child_renders(
        lock,
        consumers,
        {"certManager": chart},
        helm="helm",
        directory=tmp_path,
    )


def test_artifact_chart_reader_counts_pax_headers_in_tar_stream_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "chart.tgz"
    with tarfile.open(package, mode="w:gz") as bundle:
        for name, payload in (
            ("chart/Chart.yaml", b"name: chart\nversion: 1.0.0\n"),
            ("chart/values.yaml", b"{}\n"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.pax_headers = {"comment": "x" * 4096}
            bundle.addfile(member, io.BytesIO(payload))
    monkeypatch.setattr(release_artifacts, "_MAX_CHART_TAR_BYTES", 1024)

    with pytest.raises(ValueError, match="decompressed tar-stream limit"):
        release_artifacts._chart_file_map(package)


def test_artifact_command_failure_redacts_and_bounds_subprocess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-expose-this-token"
    private_url = f"https://user:{secret}@private.example.invalid/chart"
    raw_detail = f'pull failed for {private_url}\n{{"access_token":"{secret}"}}\n' + ("x" * 4096)
    monkeypatch.setattr(
        release_artifacts.kubernetes_process,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            1,
            stdout="",
            stderr=raw_detail,
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        _run(["helm", "pull"], label="download chart")

    message = str(exc_info.value)
    assert secret not in message
    assert "private.example.invalid" not in message
    assert "<url>" in message
    assert "<redacted>" in message
    assert len(message) < 2300


def test_artifact_command_timeout_redacts_captured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "do-not-expose-timeout-token"

    def _time_out(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise subprocess.TimeoutExpired(
            cmd=("helm", "pull"),
            timeout=300,
            stderr=f'{{"password":"{secret}"}}'.encode(),
        )

    monkeypatch.setattr(release_artifacts.kubernetes_process, "run", _time_out)

    with pytest.raises(RuntimeError, match="timed out after 300 seconds") as exc_info:
        _run(["helm", "pull"], label="download chart")

    message = str(exc_info.value)
    assert secret not in message
    assert "<redacted>" in message


def test_corrupt_chart_cache_is_refetched_from_exact_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_bytes = b"exact official chart bytes"
    expected = f"sha256:{hashlib.sha256(package_bytes).hexdigest()}"
    calls = 0

    def _fake_run(command: list[str], *, label: str) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del label
        calls += 1
        destination = Path(command[command.index("--destination") + 1])
        (destination / "raw-2.0.0.tgz").write_bytes(package_bytes)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(release_artifacts, "_run", _fake_run)
    cache = tmp_path / "cache"
    first = _cache_chart_package(
        helm="helm",
        chart="raw",
        version="2.0.0",
        repository="https://bedag.github.io/helm-charts",
        expected_sha256=expected,
        expected_oci_digest=None,
        cache_dir=cache,
    )
    first.chmod(0o600)
    first.write_bytes(b"corrupt")
    repaired = _cache_chart_package(
        helm="helm",
        chart="raw",
        version="2.0.0",
        repository="https://bedag.github.io/helm-charts",
        expected_sha256=expected,
        expected_oci_digest=None,
        cache_dir=cache,
    )

    assert calls == 2
    assert repaired.read_bytes() == package_bytes


@pytest.mark.parametrize("drift", [None, "package", "manifest"])
def test_cold_oci_cache_uses_frozen_manifest_when_version_tag_moves(tmp_path, monkeypatch, drift):
    original = b"approved chart"
    sha = "sha256:" + hashlib.sha256(original).hexdigest()
    manifest = "sha256:" + "a" * 64

    def pull(command, *, label):
        assert command[2] == "oci://registry.example.invalid/charts/checks@" + manifest
        assert "--version" not in command
        destination = Path(command[command.index("--destination") + 1])
        (destination / "checks-1.0.0.tgz").write_bytes(
            b"changed tag" if drift == "package" else original
        )
        return subprocess.CompletedProcess(
            command, 0, "", "Digest: " + ("sha256:wrong" if drift == "manifest" else manifest)
        )

    monkeypatch.setattr(release_artifacts, "_run", pull)
    kwargs = dict(
        helm="helm",
        chart="checks",
        version="1.0.0",
        repository="oci://registry.example.invalid/charts",
        expected_sha256=sha,
        expected_oci_digest=manifest,
        cache_dir=tmp_path / "cache",
    )
    if drift:
        with pytest.raises(ValueError, match="digest mismatch"):
            _cache_chart_package(**kwargs)
        assert not list((tmp_path / "cache").glob("*.tgz"))
    else:
        assert _cache_chart_package(**kwargs).read_bytes() == original
