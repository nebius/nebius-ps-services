from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SOPERATOR_COMMANDS = (
    "create",
    "discover",
    "onboard",
    "upgrade",
    "status",
)
NON_CANONICAL_UPGRADE_OPTIONS = (
    "--to-chart-version",
    "--populate-jail-refresh",
    "--jail-persistent-mount",
    "--jail-sfs-resize-policy",
    "--stop-for-remediation-approval",
)


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _squash(value: str) -> str:
    return " ".join(value.split())


def _changelog_history() -> str:
    summary = _read("CHANGELOG.md")
    history = [summary]
    for note in sorted((REPO_ROOT / "docs/releases").glob("*.md")):
        assert f"docs/releases/{note.name}" in summary
        history.append(note.read_text(encoding="utf-8"))
    return "\n".join(history)


def test_specs_expose_only_the_current_canonical_contracts() -> None:
    requirements = _read("docs/requirements.md")
    design = _read("docs/design.md")

    assert re.findall(r"^### (REQ-\d+):", requirements, re.MULTILINE) == [
        f"REQ-{number:03d}" for number in range(13, 41)
    ]
    assert re.findall(r"^### (FEAT-\d+):", design, re.MULTILINE) == [
        f"FEAT-{number:03d}" for number in range(13, 49)
    ]
    assert requirements.count("<!-- REQUIREMENT:") == 28
    assert requirements.count("<!-- /REQUIREMENT:") == 28
    assert design.count("<!-- FEATURE:") == 36
    assert design.count("<!-- /FEATURE:") == 36


def test_docs_separate_bounded_discovery_summary_from_complete_json() -> None:
    readme = _squash(_read("docs/soperator.md"))
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))
    changelog = _squash(_changelog_history())

    assert "concise support-safe Markdown summary" in readme
    assert "never prints individual nodes" in readme
    assert "complete schema-v2 `report.json`" in readme
    assert "4,000 nodes and five groups" in requirements
    assert "complete normalized JSON model" in design
    assert "Ready/Actual/Target counts" in changelog


def test_developer_workflow_uses_one_locked_uv_authority() -> None:
    readme = _read("docs/development.md")
    requirements = _read("docs/requirements.md")
    design = _read("docs/design.md")

    assert "uv `0.12.9`" in readme
    assert "`make lock-check`" in readme
    assert "`UV_PROJECT_ENVIRONMENT`" in readme
    assert "rejects whitespace" in readme
    assert "hashed build constraints" in readme
    assert "exact locked synchronization" in requirements
    assert "Locked uv contributor and CI workflow" in design
    assert "make venv" not in readme
    assert "`.[dev]`" not in readme


def test_docs_define_strict_project_local_ssh_trust() -> None:
    readme = _squash(_read("README.md"))
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))
    changelog = _squash(_changelog_history())
    combined = " ".join((readme, requirements, design, changelog))

    assert "--ssh-known-hosts-file" in readme
    assert "generated/ssh_known_hosts" in combined
    assert "independently verified" in combined
    assert "machine-global" in readme
    assert "accept-new" in requirements
    assert "ssh-keyscan" in requirements


def test_docs_define_one_upstream_soperator_surface() -> None:
    readme = _read("README.md")
    requirements = _read("docs/requirements.md")
    design = _read("docs/design.md")
    active_docs = "\n".join((readme, requirements, design))

    assert "immutable operation snapshot" in active_docs
    assert "thin cxcli adapter" in active_docs
    assert "--to-release" in active_docs
    retired_root_command = "-".join(("ext", "soperator"))
    assert f"nebius-cxcli {retired_root_command}" not in active_docs
    assert "install_mode" not in active_docs
    for option in NON_CANONICAL_UPGRADE_OPTIONS:
        assert option not in active_docs
    for stale_phrase in (
        "legacy-to-latest",
        "Cluster recreation requires",
        "historical profile engine",
        "login endpoint guards",
        "backup receipt",
    ):
        assert stale_phrase not in active_docs
    assert "Deploy Soperator through the standard configuration pipeline" in requirements
    assert "one command family, operation model, and official-upstream delivery path" in _squash(
        design
    )


def test_docs_name_the_exact_public_commands_without_an_upgrade_resume_surface() -> None:
    readme = _read("README.md")
    design = _read("docs/design.md")

    for command in PUBLIC_SOPERATOR_COMMANDS:
        assert f"soperator {command}" in readme
        assert f"soperator {command}" in design

    assert "soperator resume" not in readme
    assert "soperator resume" not in design
    upgrade_blocks = "\n".join(
        block
        for block in re.findall(r"```(?:bash|text)\n(.*?)```", readme, re.DOTALL)
        if "soperator upgrade" in block
    )
    assert upgrade_blocks
    assert "--resume" not in upgrade_blocks


def test_docs_define_current_inputs_and_local_deployment_recovery() -> None:
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))
    readme = _squash(_read("README.md"))
    assert "Terraform" in requirements and "native" in requirements
    assert "FEAT-048" in design
    assert "local" in readme and "semantic" in readme
    assert "no saved binary approval" in design
    assert "--lease-wait" not in readme
    assert "nebius-cxcli operation status" not in readme


def test_docs_keep_terraform_out_of_in_cluster_installation() -> None:
    readme = _squash(_read("README.md"))
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))

    assert "Do not use Terraform as an in-cluster package manager" in readme
    assert "Do not add Terraform resources for in-cluster Soperator installation" in requirements
    assert (
        "Terraform owns creation and reconciliation of managed Nebius resources outside the cluster"
        in design
    )
    assert "Whole-cluster destruction uses the Nebius SDK for both ownership modes" in design


def test_docs_record_dynamic_release_and_delivery_contract() -> None:
    operator_docs = _squash(_read("README.md") + " " + _read("docs/soperator.md"))
    design = _squash(_read("docs/design.md"))
    changelog = _squash(_changelog_history())

    for phrase in (
        "freezes the tag, commit, tree, source archive",
        "There is no local product chart",
        "No OCI mirror, proxy, fallback registry",
        "soperator status --verify-observability",
    ):
        assert phrase in _squash(operator_docs + " " + changelog)
    for phrase in (
        "exact infrastructure",
        "official-upstream release plan",
        "one root group with exactly five public commands",
    ):
        assert phrase in changelog
    assert "one product delivery path" in design
    assert "one bundled-artifact authority" in design


def test_docs_preserve_protected_state_and_slurm_ownership() -> None:
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))
    readme = _squash(_read("README.md"))

    assert "NFS data disk" in requirements
    assert "never recreated" in requirements
    assert "Only operation-owned holds and reservations" in requirements
    assert "protected-storage bindings" in design
    assert "exact Slurm state" in readme


def test_changelog_history_records_the_soperator_contract() -> None:
    changelog = _changelog_history()

    assert "`soperator create --release latest|X.Y.Z`" in changelog
    assert "`soperator onboard`" in changelog
    assert "`soperator upgrade --to-release latest|X.Y.Z`" in changelog
    assert "`migrate node-group`" in changelog
    assert "highest reachable" in changelog
    for command in PUBLIC_SOPERATOR_COMMANDS:
        assert f"soperator {command}" in changelog


def test_docs_define_full_stack_upgrade_and_permanent_node_group_migration() -> None:
    active_docs = _squash(
        " ".join(
            (
                _read("README.md"),
                _read("docs/soperator.md"),
                _read("docs/mk8s.md"),
                _read("docs/requirements.md"),
                _read("docs/design.md"),
            )
        )
    )

    for phrase in (
        "highest reachable supported Kubernetes endpoint",
        "sequential Kubernetes minor hops",
        "Jail CUDA",
        "operation-owned Slurm maintenance",
        "migrate node-group",
        "permanent replacement",
        "forward-only",
        "final Terraform no-op",
    ):
        assert phrase in active_docs
    assert "live executor is not enabled" not in active_docs


def test_examples_do_not_call_removed_soperator_job_commands() -> None:
    example = _read("examples/slurm-jobs/README.md")

    assert "soperator jobs" not in example
    assert "squeue --iterate=5" in example


def test_documented_catalog_pair_loads_with_matching_component_identities(tmp_path) -> None:
    from nebius_cxcli.component_sources import SourceProfile, load_component_sources

    document = _read("docs/configuration-reference.md")
    example = document.split("## Catalog example", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    blocks = re.findall(r"```yaml\n(.*?)```", example, re.DOTALL)
    assert len(blocks) == 2
    sources_path = tmp_path / "component_sources.yaml"
    sources_path.write_text(blocks[0], encoding="utf-8")
    (tmp_path / "component_cli_settings.yaml").write_text(blocks[1], encoding="utf-8")

    sources = load_component_sources(explicit=sources_path, source_profile=SourceProfile.PORTABLE)
    assert [chart.name for chart in sources.helm_charts] == ["grafana"]
    assert sources.helm_charts[0].chart_name == "grafana"


def test_operator_readme_keeps_development_and_experiment_history_out() -> None:
    readme = _read("README.md")
    assert "uv run" not in readme
    assert "Validation evidence boundary" not in readme
    assert "up to 20%" not in readme
    assert not re.search(r"\b20\d{2}-\d{2}-\d{2}\b", readme)


def test_operator_guides_distinguish_upgrade_and_deploy_job_defaults() -> None:
    readme = _squash(_read("README.md"))
    guide = _squash(_read("docs/soperator.md"))
    assert "Unattended `deploy` defaults to `wait-then-cancel` after `1h`" in readme
    assert "`requeue-hold-all`" in guide
    assert "Cancellation is never implicit" in guide
    assert "unattended deployment defaults to `wait-then-cancel`" in guide
