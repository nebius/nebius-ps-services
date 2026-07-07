#!/usr/bin/env python3
"""Self-test the skill structure validator against temporary fixtures."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


LEARNING_LOOP = """## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.
"""


def write_skill(
    skill_dir: Path,
    name: str,
    body: str = "",
    *,
    description: str | None = None,
    include_learning_loop: bool = True,
    allow_implicit_invocation: str | None = "true",
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    sections = [body]
    if include_learning_loop:
        sections.append(LEARNING_LOOP)
    if description is None:
        description = f"Test fixture for {name}."
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "---",
                "",
                f"# {name}",
                "",
                "\n\n".join(section for section in sections if section),
            ]
        ),
        encoding="utf-8",
    )
    if allow_implicit_invocation is not None:
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "openai.yaml").write_text(
            "\n".join(
                [
                    "interface:",
                    f'  display_name: "{name}"',
                    f'  short_description: "Fixture for {name}"',
                    f'  default_prompt: "Use ${name} for this fixture."',
                    "policy:",
                    f"  allow_implicit_invocation: {allow_implicit_invocation}",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def run_validator(
    target: Path,
    *,
    profile: str | None = None,
) -> subprocess.CompletedProcess[str]:
    validator = Path(__file__).with_name("validate-skill-structure.py")
    command = [sys.executable, "-B", str(validator)]
    if profile:
        command.extend(["--profile", profile])
    command.append(str(target))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def assert_contains(text: str, expected: str) -> None:
    if expected not in text:
        raise AssertionError(f"expected output to contain {expected!r}\n{text}")


def assert_not_contains(text: str, unexpected: str) -> None:
    if unexpected in text:
        raise AssertionError(f"did not expect output to contain {unexpected!r}\n{text}")


def test_evals_folder_and_unknown_folder_warning() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        good_skill = root / "good-skill"
        write_skill(
            good_skill,
            "good-skill",
            "Use `evals/trigger-prompts.csv` for trigger examples.",
        )
        (good_skill / "evals").mkdir()
        (good_skill / "evals" / "trigger-prompts.csv").write_text(
            "prompt,should_trigger\n",
            encoding="utf-8",
        )

        warning_skill = root / "warning-skill"
        write_skill(warning_skill, "warning-skill")
        (warning_skill / "experiments").mkdir()

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise AssertionError(output)

        assert_contains(output, "Validated 2 skill(s): 0 failure(s), 1 warning(s)")
        assert_contains(output, "non-canonical folder reported for review: experiments/")
        assert_not_contains(output, "non-canonical folder reported for review: evals/")


def test_missing_evals_reference_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "bad-skill",
            "bad-skill",
            "Use `evals/missing.csv` for trigger examples.",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "referenced local path does not exist: evals/missing.csv",
        )


def test_missing_learning_loop_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "missing-loop",
            "missing-loop",
            include_learning_loop=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "SKILL.md is missing ## Learning Loop")


def test_heading_only_learning_loop_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "empty-loop",
            "empty-loop",
            "## Learning Loop\n",
            include_learning_loop=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Learning Loop is missing required text: "
            "capture durable, reusable, public-safe learnings",
        )


def stateful_workflow_body() -> str:
    return """
## Purpose

Coordinate one stateful workflow step.

## When To Use

- Use for stateful workflow tasks.

## When Not To Use

- Do not use for simple one-shot tasks.

## Inputs

- Input state.

## Required Reads

- Current state.

## Writes

- Updated state.

## Process

- Read, act, record.

## Idempotency

- Reruns converge.

## Failure Handling

- Classify before retrying.

## Must Not

- Do not hide failures.

## Completion Criteria

- State and evidence are updated.

## Output Contract

- Report state, evidence, and next action.
"""


def test_stateful_workflow_profile_passes_complete_sections() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "workflow-skill",
            "workflow-skill",
            stateful_workflow_body(),
        )

        result = run_validator(root, profile="stateful-workflow")
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise AssertionError(output)

        assert_contains(output, "Validated 1 skill(s): 0 failure(s), 0 warning(s)")


def test_stateful_workflow_profile_missing_heading_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "workflow-skill",
            "workflow-skill",
            stateful_workflow_body().replace("## Writes\n\n- Updated state.\n\n", ""),
        )

        basic_result = run_validator(root)
        basic_output = basic_result.stdout + basic_result.stderr
        if basic_result.returncode != 0:
            raise AssertionError(basic_output)

        profile_result = run_validator(root, profile="stateful-workflow")
        profile_output = profile_result.stdout + profile_result.stderr
        if profile_result.returncode == 0:
            raise AssertionError(f"expected profile failure\n{profile_output}")

        assert_contains(
            profile_output,
            "stateful-workflow profile missing required heading: ## Writes",
        )


def test_sdlc_only_name_and_description_contract() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        prefix = "Use only as part of the Agentic SDLC workflow;"

        write_skill(
            root / "sdlc-good",
            "sdlc-good",
            description=f"{prefix} use for a workflow phase.",
            allow_implicit_invocation="false",
        )
        write_skill(
            root / "plain-skill",
            "plain-skill",
            description=f"{prefix} use for a workflow phase.",
        )
        write_skill(
            root / "sdlc-missing-prefix",
            "sdlc-missing-prefix",
            description="Use for a workflow phase.",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "skills with the SDLC-only description prefix must use an sdlc-* name",
        )
        assert_contains(
            output,
            "SDLC-only skills must start the description with: "
            "Use only as part of the Agentic SDLC workflow;",
        )


def test_missing_openai_metadata_policy_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "missing-policy",
            "missing-policy",
            allow_implicit_invocation=None,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "missing agents/openai.yaml metadata with "
            "policy.allow_implicit_invocation",
        )


def test_wrong_openai_metadata_path_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_dir = root / "wrong-path"
        write_skill(
            skill_dir,
            "wrong-path",
            allow_implicit_invocation=None,
        )
        (skill_dir / "agents.openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: true\n",
            encoding="utf-8",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "found agents.openai.yaml; OpenAI metadata must live at "
            "agents/openai.yaml",
        )


def test_invocation_policy_contract_fails_for_wrong_value() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "commit",
            "commit",
            allow_implicit_invocation="true",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "policy.allow_implicit_invocation must be false for commit",
        )


def test_description_declared_explicit_policy_must_be_false() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "manual-release",
            "manual-release",
            description="Use only when the user explicitly asks to create a manual release artifact.",
            allow_implicit_invocation="true",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "policy.allow_implicit_invocation must be false for manual-release",
        )


def test_invocation_policy_section_can_require_explicit_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "guarded-workflow",
            "guarded-workflow",
            "## Invocation Policy\n\nExplicit invocation required.\n",
            allow_implicit_invocation="true",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "policy.allow_implicit_invocation must be false for guarded-workflow",
        )


def test_ordinary_skill_policy_must_be_true() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "ordinary-skill",
            "ordinary-skill",
            allow_implicit_invocation="false",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "policy.allow_implicit_invocation must be true for ordinary-skill",
        )


def test_apply_security_policy_can_be_implicit() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "apply-security",
            "apply-security",
            (
                "## Invocation Policy\n\n"
                "Implicit invocation is allowed. Patch only when the current "
                "task allows edits and the remediation is low risk.\n"
            ),
            description=(
                "Use as a security reviewer, adviser, and safe remediation "
                "helper for design, implementation, review, and validation."
            ),
            allow_implicit_invocation="true",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise AssertionError(output)

        assert_contains(output, "Validated 1 skill(s): 0 failure(s), 0 warning(s)")


def test_guardrail_text_does_not_make_whole_skill_explicit_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "safe-helper",
            "safe-helper",
            (
                "## Guardrails\n\n"
                "- Run destructive actions only when the user explicitly asks.\n"
            ),
            allow_implicit_invocation="true",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise AssertionError(output)

        assert_contains(output, "Validated 1 skill(s): 0 failure(s), 0 warning(s)")


def test_sdlc_invocation_policy_must_be_explicit_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        prefix = "Use only as part of the Agentic SDLC workflow;"
        write_skill(
            root / "sdlc-plan",
            "sdlc-plan",
            description=f"{prefix} use for a workflow phase.",
            allow_implicit_invocation="true",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "policy.allow_implicit_invocation must be false for sdlc-plan",
        )


def main() -> int:
    tests = [
        test_evals_folder_and_unknown_folder_warning,
        test_missing_evals_reference_fails,
        test_missing_learning_loop_fails,
        test_heading_only_learning_loop_fails,
        test_stateful_workflow_profile_passes_complete_sections,
        test_stateful_workflow_profile_missing_heading_fails,
        test_sdlc_only_name_and_description_contract,
        test_missing_openai_metadata_policy_fails,
        test_wrong_openai_metadata_path_fails,
        test_invocation_policy_contract_fails_for_wrong_value,
        test_description_declared_explicit_policy_must_be_false,
        test_invocation_policy_section_can_require_explicit_only,
        test_ordinary_skill_policy_must_be_true,
        test_apply_security_policy_can_be_implicit,
        test_guardrail_text_does_not_make_whole_skill_explicit_only,
        test_sdlc_invocation_policy_must_be_explicit_only,
    ]
    for test in tests:
        test()
    print(f"OK {len(tests)} validator self-test(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
