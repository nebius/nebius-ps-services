from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SOPERATOR_COMMANDS = (
    "install",
    "discover",
    "onboard",
    "upgrade",
    "status",
    "destroy",
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


def test_specs_expose_only_the_current_canonical_contracts() -> None:
    requirements = _read("docs/requirements.md")
    design = _read("docs/design.md")

    assert re.findall(r"^### (REQ-\d+):", requirements, re.MULTILINE) == [
        f"REQ-{number:03d}" for number in range(13, 28)
    ]
    assert re.findall(r"^### (FEAT-\d+):", design, re.MULTILINE) == [
        f"FEAT-{number:03d}" for number in range(13, 30)
    ]
    assert requirements.count("<!-- REQUIREMENT:") == 15
    assert requirements.count("<!-- /REQUIREMENT:") == 15
    assert design.count("<!-- FEATURE:") == 17
    assert design.count("<!-- /FEATURE:") == 17


def test_docs_separate_bounded_discovery_summary_from_complete_json() -> None:
    readme = _squash(_read("README.md"))
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))
    changelog = _squash(_read("CHANGELOG.md"))

    assert "concise support-safe Markdown summary" in readme
    assert "never prints individual nodes" in readme
    assert "complete schema-v2 `report.json`" in readme
    assert "4,000 nodes and five groups" in requirements
    assert "complete normalized JSON model" in design
    assert "Ready/Actual/Target counts" in changelog


def test_developer_workflow_uses_one_locked_uv_authority() -> None:
    readme = _read("README.md")
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
    changelog = _squash(_read("CHANGELOG.md"))
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
    assert "The only Soperator-related root command is `soperator`" in requirements
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


def test_docs_define_guarded_pre_execution_install_replan() -> None:
    readme = _squash(_read("README.md"))
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))
    changelog = _squash(_read("CHANGELOG.md"))

    assert "`install`, `discover`, `onboard`, `upgrade`, `status`, and `destroy`" in requirements
    for surface in (readme, requirements, design, changelog):
        assert "never-executed" in surface
    assert "prior fingerprint cannot authorize the replacement" in readme
    assert "partial-apply plan" not in " ".join((readme, requirements, design, changelog))


def test_docs_keep_terraform_out_of_in_cluster_installation() -> None:
    readme = _squash(_read("README.md"))
    requirements = _squash(_read("docs/requirements.md"))
    design = _squash(_read("docs/design.md"))

    assert "Do not use Terraform as an in-cluster package manager" in readme
    assert "Do not add Terraform resources for in-cluster Soperator installation" in requirements
    assert "Terraform owns Nebius resources outside the cluster" in design


def test_docs_record_dynamic_release_and_delivery_contract() -> None:
    readme = _squash(_read("README.md"))
    design = _squash(_read("docs/design.md"))
    changelog = _squash(_read("CHANGELOG.md"))

    for phrase in (
        "freezes the tag, commit, tree, source archive",
        "There is no local product chart",
        "No OCI mirror, proxy, fallback registry",
        "soperator status --verify-observability",
    ):
        assert phrase in _squash(readme + " " + changelog)
    for phrase in (
        "exact infrastructure",
        "official-upstream release plan",
        "one root group with exactly six public commands",
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


def test_unreleased_changelog_names_the_current_soperator_contract() -> None:
    changelog = _read("CHANGELOG.md")
    unreleased = changelog.split("## [Unreleased]", maxsplit=1)[1].split("\n## [", maxsplit=1)[0]

    assert "`soperator install --release latest|X.Y.Z`" in unreleased
    assert "`soperator onboard`" in unreleased
    assert "`soperator upgrade --to-release latest|X.Y.Z`" in unreleased
    assert "`migrate node-group`" in unreleased
    assert "highest reachable" in unreleased
    for command in PUBLIC_SOPERATOR_COMMANDS:
        assert f"soperator {command}" in unreleased


def test_docs_define_full_stack_upgrade_and_permanent_node_group_migration() -> None:
    active_docs = _squash(
        " ".join(
            (
                _read("README.md"),
                _read("docs/requirements.md"),
                _read("docs/design.md"),
            )
        )
    )

    for phrase in (
        "highest reachable provider-supported endpoint",
        "sequential minor",
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
