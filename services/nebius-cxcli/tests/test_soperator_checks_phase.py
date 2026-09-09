import copy
import json
from dataclasses import replace

import pytest

from nebius_cxcli import soperator_passive_policy
from nebius_cxcli.soperator_checks_phase import (
    ChecksPhase,
    ChecksPhaseContext,
    admission_partition_configuration,
)
from nebius_cxcli.soperator_checks_policy import SoperatorChecksPolicy, checks_digest
from nebius_cxcli.soperator_passive_policy import passive_phase_overrides


def test_phase_overlays_keep_desired_policy_immutable():
    partitions = {
        "configType": "structured",
        "partitions": [
            {"name": "gpu", "config": "Default=YES State=UP"},
            {"name": "hidden", "config": "Hidden=YES State=UP"},
        ],
    }
    values = {"slurmCluster": {"overrideValues": {"partitionConfiguration": partitions}}}
    frozen = copy.deepcopy(values)
    policy = SoperatorChecksPolicy("source", checks_digest(values), (), {}, partitions=partitions)
    digest = policy.sha256
    for phase in (ChecksPhase.MAINTENANCE, ChecksPhase.ACCEPTANCE, ChecksPhase.SCHEDULES):
        result = policy.effective_values(
            values, installing=True, context=ChecksPhaseContext(phase, "reserve")
        )
        rows = result["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"]
        assert "State=DOWN" in rows[0]["config"]
        assert "AllowGroups=soperatorchecks" in rows[1]["config"]
        assert ("State=UP" in rows[1]["config"]) == (phase != ChecksPhase.MAINTENANCE)
    ready = policy.effective_values(
        values,
        installing=True,
        context=ChecksPhaseContext(
            ChecksPhase.READY,
            "reserve",
            partition_restoration={
                "gpu": {"State": "INACTIVE"},
                "hidden": {"AllowGroups": "research"},
            },
        ),
    )
    assert (
        "State=INACTIVE"
        in ready["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"][0][
            "config"
        ]
    )
    assert values == frozen and policy.sha256 == digest


@pytest.mark.parametrize("config", ["RootOnly=YES", "ReqResv=YES", "State=UP State=DOWN"])
def test_unsupported_admission_is_rejected_before_mutation(config):
    with pytest.raises(ValueError):
        admission_partition_configuration(
            {"configType": "structured", "partitions": [{"name": "hidden", "config": config}]},
            checks_open=True,
        )


def test_fresh_install_disables_only_reviewed_diagnostic_entries_before_reservation_exists():
    policy = {
        "supported": True,
        "diagnostics": ["boot_disk_full.sh"],
        "entries": {
            "boot_disk_full.sh": {"name": "disk", "contexts": ["any"]},
            "tmpfs_mount.sh": {"name": "mount", "contexts": ["prolog"]},
        },
    }
    assert passive_phase_overrides(policy, "", fresh_install=True) == {
        "boot_disk_full.sh": {"enabled": False}
    }
    upgrade = passive_phase_overrides(policy, "reserve")
    assert "tmpfs_mount.sh" not in upgrade
    assert "reserve" in upgrade["boot_disk_full.sh"]["customConfig"]


def test_passive_compiler_freezes_enabled_proof_roles_into_policy_identity(tmp_path, monkeypatch):
    module = soperator_passive_policy
    chart = tmp_path / "helm/slurm-cluster"
    scripts = chart / "slurm_scripts"
    scripts.mkdir(parents=True)
    enabled = ["boot_disk_full.sh", "alloc_gpus_busy.drain.sh"]
    defaults = {name: {"enabled": name in enabled} for name in module.DIAGNOSTICS}
    (chart / "values.yaml").write_text(json.dumps({"slurmScripts": {"builtIn": defaults}}))
    for name in (*module.BASE_SCRIPTS, *enabled):
        (scripts / name).write_text("native")
    for name in enabled:
        (scripts / (name + ".json")).write_text(json.dumps({"name": name, "command": "./" + name}))
    monkeypatch.setattr(module, "REVIEWED_BUNDLES", {module.passive_bundle_digest(chart)})
    passive = module.compile_passive_policy(tmp_path, {})
    assert passive["proofRoles"] == {
        "boot_disk_full.sh": "required-measurement",
        "alloc_gpus_busy.drain.sh": "supporting-only",
    }
    policy = SoperatorChecksPolicy("source", "values", (), {}, passive=passive)
    digest = policy.sha256
    passive = copy.deepcopy(passive)
    passive["proofRoles"]["boot_disk_full.sh"] = "supporting-only"
    assert replace(policy, passive=passive).sha256 != digest
