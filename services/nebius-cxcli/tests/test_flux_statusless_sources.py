from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import flux_ops


@pytest.mark.parametrize(
    "desired,observed,present",
    [
        ("default", "default", True),
        ("oci", "default", True),
        ("default", "oci", True),
        ("oci", "oci", False),
    ],
)
def test_pending_or_missing_sources_cannot_be_bypassed(
    tmp_path, monkeypatch, desired, observed, present
):
    resource = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "HelmRepository",
        "metadata": {"name": "chart", "namespace": "flux-system"},
        "spec": {"type": desired},
    }
    (tmp_path / "source.yaml").write_text(yaml.safe_dump(resource))
    live = {**resource, "spec": {"type": observed}}
    monkeypatch.setattr(flux_ops, "_require_binary", lambda name: None)
    monkeypatch.setattr(
        flux_ops, "_kubectl_get_target", lambda *a, **kw: (live if present else None, "missing")
    )
    with pytest.raises(RuntimeError, match="did not become Ready"):
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=tmp_path), timeout_seconds=0
        )


@pytest.mark.parametrize("change", ["name", "namespace", "deleting"])
def test_statusless_oci_requires_exact_live_identity(change):
    target = flux_ops.FluxWaitTarget(
        resource_type="helmrepository.source.toolkit.fluxcd.io",
        name="chart",
        namespace="flux-system",
        kind="HelmRepository",
        is_source=True,
        repository_type="oci",
    )
    metadata = {"name": "chart", "namespace": "flux-system"}
    if change == "deleting":
        metadata["deletionTimestamp"] = "2026-01-01T00:00:00Z"
    else:
        metadata[change] = "other"
    result = flux_ops._format_flux_target_summary(
        target,
        {
            "kind": "HelmRepository",
            "metadata": metadata,
            "spec": {"type": "oci"},
        },
        "",
    )
    assert not result.is_ready
