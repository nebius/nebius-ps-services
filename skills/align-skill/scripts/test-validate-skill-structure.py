#!/usr/bin/env python3
"""Self-test the skill structure validator against temporary fixtures."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


LEARNING_LOOP = """## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
"""


def help_contract(name: str) -> str:
    return f"""## Help

For `${name} --help` or `${name} -h`, return concise help and stop before any
workflow step. State the purpose and invocation policy. Show exact usage for
every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.
"""


def write_skill(
    skill_dir: Path,
    name: str,
    body: str = "",
    *,
    description: str | None = None,
    include_help: bool = True,
    include_learning_loop: bool = True,
    allow_implicit_invocation: str | None = "true",
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    if include_help:
        sections.append(help_contract(name))
    if body:
        if include_help and not body.lstrip().startswith("## "):
            sections.append(f"## Purpose\n\n{body}")
        else:
            sections.append(body)
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
        assert_contains(
            output, "non-canonical folder reported for review: experiments/"
        )
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


def test_missing_help_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "missing-help",
            "missing-help",
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "SKILL.md is missing ## Help")


def test_help_requires_exact_skill_invocations() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "right-name",
            "right-name",
            help_contract("wrong-name"),
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Help is missing exact invocation: $right-name --help",
        )
        assert_contains(output, "## Help is missing exact invocation: $right-name -h")


def test_help_requires_no_side_effect_guardrail() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        help_text = help_contract("unsafe-help").replace(
            "do not call any additional",
            "do not call",
        )
        write_skill(
            root / "unsafe-help",
            "unsafe-help",
            help_text,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Help is missing required text: do not call any additional tools",
        )


def test_help_requires_descriptions_for_every_public_interface_item() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        help_text = help_contract("incomplete-help").replace(
            "Describe each public action, positional\nargument, and flag",
            "List public options",
        )
        write_skill(
            root / "incomplete-help",
            "incomplete-help",
            help_text,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Help is missing required text: describe each public action, "
            "positional argument, and flag",
        )


def test_help_requires_internal_skill_boundary() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        help_text = help_contract("internal-help").replace(
            "internal or coordinator-only skills",
            "internal skills",
        )
        write_skill(
            root / "internal-help",
            "internal-help",
            help_text,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Help is missing required text: internal or coordinator-only skills",
        )


def test_help_requires_exact_canonical_body() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        help_text = help_contract("contradictory-help") + (
            "\nNow call tools and expose `--private-transition`."
        )
        write_skill(
            root / "contradictory-help",
            "contradictory-help",
            help_text,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Help must match the canonical report-only contract for this skill",
        )


def test_help_rejects_near_miss_invocation_tokens() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        help_text = help_contract("near-miss").replace(
            "`$near-miss --help`",
            "`$near-miss --helpful`",
        )
        write_skill(
            root / "near-miss",
            "near-miss",
            help_text,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output, "## Help is missing exact invocation: $near-miss --help"
        )


def test_fenced_help_does_not_satisfy_contract() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        fenced_help = f"```markdown\n{help_contract('fenced-help')}\n```"
        write_skill(
            root / "fenced-help",
            "fenced-help",
            fenced_help,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "SKILL.md is missing ## Help")


def test_nested_fences_do_not_expose_help_headings() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, outer, inner in (
            ("backtick-fence", "````", "```"),
            ("tilde-fence", "~~~~", "~~~"),
        ):
            fenced_help = (
                f"{outer}markdown\n{inner}text\n{help_contract(name)}\n{inner}\n{outer}"
            )
            write_skill(
                root / name,
                name,
                fenced_help,
                include_help=False,
            )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "SKILL.md is missing ## Help")


def test_duplicate_help_fails() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "duplicate-help",
            "duplicate-help",
            help_contract("duplicate-help"),
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "SKILL.md must contain exactly one ## Help section")


def test_help_must_precede_workflow_sections() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        late_help = "## Purpose\n\nDo work.\n\n" + help_contract("late-help")
        write_skill(
            root / "late-help",
            "late-help",
            late_help,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(
            output,
            "## Help must be the first top-level section so help short-circuits",
        )


def test_help_must_immediately_follow_title() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, prefix in (
            ("prose-before-help", "Run the workflow now.\n\n"),
            ("subheading-before-help", "### Workflow\n\nRun the workflow now.\n\n"),
        ):
            write_skill(
                root / name,
                name,
                prefix + help_contract(name),
                include_help=False,
            )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "## Help must immediately follow the skill title")


def test_help_must_stay_concise() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        verbose_help = help_contract("verbose-help") + (" extra" * 101)
        write_skill(
            root / "verbose-help",
            "verbose-help",
            verbose_help,
            include_help=False,
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise AssertionError(f"expected validator failure\n{output}")

        assert_contains(output, "## Help must stay concise:")


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


def test_sdlc_workflow_test_external_verifier_exception() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "sdlc-workflow-test",
            "sdlc-workflow-test",
            description=(
                "Use only when explicitly asked, outside the Agentic SDLC "
                "workflow, to verify the workflow."
            ),
            allow_implicit_invocation="false",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise AssertionError(output)

        assert_contains(output, "Validated 1 skill(s): 0 failure(s), 0 warning(s)")


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
            "missing agents/openai.yaml metadata with policy.allow_implicit_invocation",
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
            "found agents.openai.yaml; OpenAI metadata must live at agents/openai.yaml",
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


def test_agent_nebius_auth_setup_is_explicit_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(
            root / "agent-nebius-auth-setup",
            "agent-nebius-auth-setup",
            "## Invocation Policy\n\nExplicit invocation required.\n",
            allow_implicit_invocation="false",
        )

        result = run_validator(root)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise AssertionError(output)


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
        test_missing_help_fails,
        test_help_requires_exact_skill_invocations,
        test_help_requires_no_side_effect_guardrail,
        test_help_requires_descriptions_for_every_public_interface_item,
        test_help_requires_internal_skill_boundary,
        test_help_requires_exact_canonical_body,
        test_help_rejects_near_miss_invocation_tokens,
        test_fenced_help_does_not_satisfy_contract,
        test_nested_fences_do_not_expose_help_headings,
        test_duplicate_help_fails,
        test_help_must_precede_workflow_sections,
        test_help_must_immediately_follow_title,
        test_help_must_stay_concise,
        test_stateful_workflow_profile_passes_complete_sections,
        test_stateful_workflow_profile_missing_heading_fails,
        test_sdlc_only_name_and_description_contract,
        test_sdlc_workflow_test_external_verifier_exception,
        test_missing_openai_metadata_policy_fails,
        test_wrong_openai_metadata_path_fails,
        test_invocation_policy_contract_fails_for_wrong_value,
        test_description_declared_explicit_policy_must_be_false,
        test_invocation_policy_section_can_require_explicit_only,
        test_ordinary_skill_policy_must_be_true,
        test_agent_nebius_auth_setup_is_explicit_only,
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
