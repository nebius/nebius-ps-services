from __future__ import annotations

import copy

import pytest

from nebius_cxcli.soperator_passive_scheduler import (
    scheduler_from_cluster_spec,
    verify_scheduler,
)


@pytest.fixture
def cluster_spec():
    return {
        "slurmConfig": {"prolog": "/opt/prolog.sh", "epilog": "/opt/epilog.sh"},
        "healthCheckConfig": {
            "healthCheckInterval": 120,
            "healthCheckProgram": "/opt/hc.sh",
            "healthCheckNodeState": [{"state": "ANY"}, {"state": "CYCLE"}],
        },
    }


@pytest.fixture
def live_config():
    return (
        "HealthCheckInterval = 120 sec\nHealthCheckProgram = /opt/hc.sh\n"
        "HealthCheckNodeState = CYCLE,ANY\n"
        "Prolog[0] = /opt/prolog.sh\nEpilog[0] = /opt/epilog.sh\n"
    )


def test_native_indexed_hooks_and_reordered_flags_match(cluster_spec, live_config):
    original = copy.deepcopy(cluster_spec)
    desired = scheduler_from_cluster_spec(cluster_spec)
    verify_scheduler(live_config, desired)
    assert cluster_spec == original
    assert desired["HealthCheckInterval"] == "120"


def test_custom_scalars_override_and_hooks_accumulate(cluster_spec, live_config):
    cluster_spec["customSlurmConfig"] = (
        "# Comment\nHealthCheckInterval = 240 # Override\n"
        'HealthCheckProgram="/opt/custom hc.sh"\n'
        'HealthCheckNodeState=IDLE,CYCLE\nProlog="/opt/additional hook.sh"\n'
    )
    desired = scheduler_from_cluster_spec(cluster_spec)
    live_config = (
        live_config.replace("= 120", "= 240")
        .replace("/opt/hc.sh", "/opt/custom hc.sh")
        .replace("CYCLE,ANY", "CYCLE,IDLE")
        + "Prolog[1] = /opt/additional hook.sh\n"
    )
    verify_scheduler(live_config, desired)
    assert desired["Prolog"] == ["/opt/prolog.sh", "/opt/additional hook.sh"]


@pytest.mark.parametrize(
    "custom",
    [
        "Include other.conf",
        "include=other.conf",
        "Prolog=/opt/*.sh",
        "HealthCheckInterval=1 extra",
        "SchedulerParameters=x HealthCheckInterval=0",
    ],
)
def test_unresolved_custom_wiring_cannot_be_frozen(cluster_spec, custom):
    cluster_spec["customSlurmConfig"] = custom
    with pytest.raises(ValueError):
        scheduler_from_cluster_spec(cluster_spec)


@pytest.mark.parametrize(
    "before,after",
    [
        ("= 120", "= 0"),
        ("/opt/hc.sh", "(null)"),
        ("CYCLE,ANY", "IDLE"),
        ("Prolog[0] = /opt/prolog.sh", "Prolog = (null)"),
        ("Epilog[0] = /opt/epilog.sh", "Epilog = (null)"),
        ("Prolog[0]", "Prolog[1]"),
        ("Prolog[0]", "Prolog[00]"),
        ("Prolog[0]", "Prolog"),
        ("Prolog[0] = /opt/prolog.sh", ""),
    ],
)
def test_disabled_missing_or_ambiguous_wiring_is_rejected(cluster_spec, live_config, before, after):
    with pytest.raises(RuntimeError):
        verify_scheduler(
            live_config.replace(before, after), scheduler_from_cluster_spec(cluster_spec)
        )


@pytest.mark.parametrize(
    "extra",
    [
        "HealthCheckInterval = 120 sec",
        "Prolog[0] = /opt/prolog.sh",
        "Prolog = (null)",
        "Prolog[1] = /opt/unexpected.sh",
    ],
)
def test_duplicate_or_additional_live_wiring_is_rejected(cluster_spec, live_config, extra):
    with pytest.raises(RuntimeError):
        verify_scheduler(live_config + extra + "\n", scheduler_from_cluster_spec(cluster_spec))


def test_intentionally_absent_hooks_and_scheduler_are_preserved():
    desired = scheduler_from_cluster_spec({})
    verify_scheduler(
        "HealthCheckInterval = 0 sec\nHealthCheckProgram = (null)\nHealthCheckNodeState = ANY\n",
        desired,
    )


@pytest.mark.parametrize("custom", [False, True])
def test_compiler_freezes_rendered_chart_defaults_even_with_null_health_values(
    tmp_path, monkeypatch, custom
):
    import json
    from types import SimpleNamespace

    from nebius_cxcli import soperator_passive_policy as policy

    chart = tmp_path / "helm/slurm-cluster"
    chart.mkdir(parents=True)
    (chart / "values.yaml").write_text("healthCheckConfig: null\nslurmScripts: {builtIn: {}}\n")
    observed = []

    def render(args, **kwargs):
        assert "templates/slurm-cluster-cr.yaml" in args
        observed.append(json.loads(kwargs["input"]))
        # These settings come from the chart template, despite null raw values.
        spec = {
            "slurmConfig": {
                "prolog": "/opt/slurm_scripts/prolog.sh",
                "epilog": "/opt/slurm_scripts/epilog.sh",
            },
            "healthCheckConfig": {
                "healthCheckInterval": 120,
                "healthCheckProgram": "/opt/slurm_scripts/hc_program.sh",
                "healthCheckNodeState": [{"state": "ANY"}, {"state": "CYCLE"}],
            },
        }
        if custom:
            spec["customSlurmConfig"] = "HealthCheckInterval=240"
        return SimpleNamespace(stdout=json.dumps({"kind": "SlurmCluster", "spec": spec}))

    monkeypatch.setattr(policy.kubernetes_process, "run", render)
    monkeypatch.setattr(policy, "REVIEWED_BUNDLES", set())
    overrides = {"healthCheckConfig": None}
    if custom:
        overrides["customSlurmConfig"] = "HealthCheckInterval=240"
    result = policy.compile_passive_policy(
        tmp_path, {"slurmCluster": {"overrideValues": overrides}}
    )
    assert not result["supported"]
    assert observed == [overrides]
    assert result["scheduler"]["HealthCheckInterval"] == ("240" if custom else "120")
    assert result["scheduler"]["Prolog"] == ["/opt/slurm_scripts/prolog.sh"]
    assert result["scheduler"]["Epilog"] == ["/opt/slurm_scripts/epilog.sh"]


@pytest.mark.parametrize("interval", ["120", "120 ms", "120 sec extra", "-1 sec", "1.5 sec"])
def test_native_interval_units_are_not_silently_discarded(cluster_spec, live_config, interval):
    with pytest.raises(RuntimeError, match="scheduler"):
        verify_scheduler(
            live_config.replace("120 sec", interval), scheduler_from_cluster_spec(cluster_spec)
        )
