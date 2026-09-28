"""An existing installation is inspected without extending its chart or catalog."""

import copy
import json

import pytest
from test_monitoring_setup import owned_monitoring as monitoring_fixture

owned_monitoring = monitoring_fixture


def test_existing_grafana_values_survive_discovery(owned_monitoring):
    m, args, s = owned_monitoring
    s.grafana["spec"]["values"].update(
        {
            "extraObjects": [{"kind": "ConfigMap", "metadata": {"name": "unrelated"}}],
            "admin": {"existingSecret": "private-reference"},
            "dashboards": {"existing": {"json": "preserved"}},
        }
    )
    s.live_grafana["spec"] = copy.deepcopy(s.grafana["spec"])
    s.save()
    before = copy.deepcopy(s.live_grafana)
    snapshot = {p: p.read_bytes() for p in s.generated.rglob("*") if p.is_file()}
    m.write_connections(args, m.discover(args))
    assert s.live_grafana == before
    assert all(p.read_bytes() == content for p, content in snapshot.items())
    receipt = json.loads((args.output_dir / "monitoring.json").read_text())
    assert "private-reference" not in json.dumps(receipt)
    assert "extraObjects" not in json.dumps(receipt)


def test_public_existing_grafana_is_not_silently_converted(owned_monitoring):
    m, args, s = owned_monitoring
    s.grafana["spec"]["values"]["ingress"] = {"enabled": True}
    s.live_grafana["spec"] = copy.deepcopy(s.grafana["spec"])
    s.save()
    with pytest.raises(ValueError, match="private"):
        m.discover(args)
    assert s.live_grafana["spec"]["values"]["ingress"]["enabled"] is True
    assert not args.output_dir.exists()


def test_api_defaulted_helmrelease_matches_rendered_intent(owned_monitoring):
    m, args, s = owned_monitoring
    for expected, live in ((s.grafana, s.live_grafana), (s.gateway, s.live_gateway)):
        expected["spec"]["chart"] = {"spec": {"chart": "fixture"}}
        expected["spec"]["uninstall"] = {}
        live["spec"] = copy.deepcopy(expected["spec"])
        live["spec"]["suspend"] = False
        live["spec"]["chart"]["spec"]["reconcileStrategy"] = "ChartVersion"
        live["spec"]["uninstall"]["deletionPropagation"] = "background"
    s.save()
    assert m.discover(args)["datasource_uid"] == "cxcli-fixture-metrics"
