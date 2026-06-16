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
    include_learning_loop: bool = True,
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    sections = [body]
    if include_learning_loop:
        sections.append(LEARNING_LOOP)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: Test fixture for {name}.",
                "---",
                "",
                f"# {name}",
                "",
                "\n\n".join(section for section in sections if section),
            ]
        ),
        encoding="utf-8",
    )


def run_validator(target: Path) -> subprocess.CompletedProcess[str]:
    validator = Path(__file__).with_name("validate-skill-structure.py")
    return subprocess.run(
        [sys.executable, "-B", str(validator), str(target)],
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


def main() -> int:
    tests = [
        test_evals_folder_and_unknown_folder_warning,
        test_missing_evals_reference_fails,
        test_missing_learning_loop_fails,
        test_heading_only_learning_loop_fails,
    ]
    for test in tests:
        test()
    print(f"OK {len(tests)} validator self-test(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
