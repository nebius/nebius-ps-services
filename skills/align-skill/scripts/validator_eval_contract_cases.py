"""Focused eval-contract cases for the skill-structure validator self-test."""

from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def require_success(result: subprocess.CompletedProcess[str]) -> str:
    output = combined_output(result)
    if result.returncode != 0:
        raise AssertionError(output)
    return output


def require_failure(result: subprocess.CompletedProcess[str]) -> str:
    output = combined_output(result)
    if result.returncode == 0:
        raise AssertionError(f"expected validator failure\n{output}")
    return output


def run_eval_contract_cases(
    *,
    write_skill: Callable[..., None],
    write_trigger_evals: Callable[..., None],
    run_validator: Callable[..., subprocess.CompletedProcess[str]],
    stateful_workflow_body: Callable[[], str],
    assert_contains: Callable[[str, str], None],
    assert_not_contains: Callable[[str, str], None],
) -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_skill(root / "missing-evals", "missing-evals")
        require_success(run_validator(root))
        output = require_failure(run_validator(root, require_evals=True))
        assert_contains(output, "missing required evals/trigger-prompts.csv")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_dir = root / "valid-evals"
        write_skill(skill_dir, "valid-evals")
        write_trigger_evals(skill_dir)
        output = require_success(run_validator(root, require_evals=True))
        assert_contains(output, "Validated 1 skill(s): 0 failure(s), 0 warning(s)")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        too_few_true = root / "too-few-true"
        write_skill(too_few_true, "too-few-true")
        write_trigger_evals(too_few_true, true_count=2)
        too_few_false = root / "too-few-false"
        write_skill(too_few_false, "too-few-false")
        write_trigger_evals(too_few_false, false_count=2)
        output = require_failure(run_validator(root, require_evals=True))
        assert_contains(output, "needs at least 3 true cases; found 2")
        assert_contains(output, "needs at least 3 false cases; found 2")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_dir = root / "malformed-evals"
        write_skill(skill_dir, "malformed-evals")
        evals_dir = skill_dir / "evals"
        evals_dir.mkdir()
        (evals_dir / "trigger-prompts.csv").write_text(
            "prompt,should_trigger\n",
            encoding="utf-8",
        )
        output = require_failure(run_validator(root))
        assert_contains(output, "must use exact header: id,should_trigger,prompt")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_dir = root / "bad-rows"
        write_skill(skill_dir, "bad-rows")
        evals_dir = skill_dir / "evals"
        evals_dir.mkdir()
        (evals_dir / "trigger-prompts.csv").write_text(
            "\n".join(
                [
                    "id,should_trigger,prompt",
                    ",true,Blank id",
                    "confidential-case-123,true,PRIVATE_TRIGGER_CONTENT_8f92",
                    "confidential-case-123,true,Another prompt",
                    "bad-label,True,Wrong boolean case",
                    "blank-prompt,false,",
                    "negative-1,false,PRIVATE_TRIGGER_CONTENT_8f92",
                    "negative-2,false,Unique negative",
                    "too,many,columns,here",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        output = require_failure(run_validator(root))
        for expected in (
            "row 2 has a blank id",
            "rows 3 and 4 have duplicate ids",
            "rows 3 and 7 have duplicate prompts",
            "should_trigger must be lowercase true or false",
            "row 6 has a blank prompt",
            "row 9 must contain exactly 3 columns",
        ):
            assert_contains(output, expected)
        for private_value in (
            "confidential-case-123",
            "PRIVATE_TRIGGER_CONTENT_8f92",
        ):
            assert_not_contains(output, private_value)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        external_owner = root / "external-owner"
        external_owner.mkdir()
        write_trigger_evals(external_owner)

        file_link_skill = root / "external-file-link"
        write_skill(file_link_skill, "external-file-link")
        (file_link_skill / "evals").mkdir()
        (file_link_skill / "evals" / "trigger-prompts.csv").symlink_to(
            external_owner / "evals" / "trigger-prompts.csv"
        )
        output = require_failure(
            run_validator(file_link_skill, require_evals=True)
        )
        assert_contains(output, "symlinks are not allowed")

        directory_link_skill = root / "external-directory-link"
        write_skill(directory_link_skill, "external-directory-link")
        (directory_link_skill / "evals").symlink_to(
            external_owner / "evals",
            target_is_directory=True,
        )
        output = require_failure(
            run_validator(directory_link_skill, require_evals=True)
        )
        assert_contains(output, "symlinks are not allowed")

    for name, payload in (
        (
            "unterminated-quote",
            b'id,should_trigger,prompt\npositive-1,true,"unterminated\n',
        ),
        (
            "trailing-quote-data",
            b'id,should_trigger,prompt\npositive-1,true,"prompt"junk\n',
        ),
        (
            "invalid-utf8",
            b"id,should_trigger,prompt\npositive-1,true,\xff\n",
        ),
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / name
            write_skill(skill_dir, name)
            evals_dir = skill_dir / "evals"
            evals_dir.mkdir()
            (evals_dir / "trigger-prompts.csv").write_bytes(payload)
            output = require_failure(run_validator(root))
            assert_contains(output, "cannot read evals/trigger-prompts.csv")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        markdown_only = root / "markdown-only"
        write_skill(markdown_only, "markdown-only")
        markdown_evals = markdown_only / "evals"
        markdown_evals.mkdir()
        (markdown_evals / "trigger-prompts.md").write_text(
            "# Trigger Prompts\n",
            encoding="utf-8",
        )
        dual = root / "dual-authority"
        write_skill(dual, "dual-authority")
        write_trigger_evals(dual)
        (dual / "evals" / "trigger-prompts.md").write_text(
            "# Trigger Prompts\n",
            encoding="utf-8",
        )
        output = require_failure(run_validator(root, require_evals=True))
        assert_contains(
            output,
            "evals/trigger-prompts.md does not satisfy the canonical CSV contract",
        )
        assert_contains(output, "strict eval validation rejects dual trigger authorities")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, target_lines in (("exact-budget", 500), ("over-budget", 501)):
            skill_dir = root / name
            write_skill(skill_dir, name)
            skill_md = skill_dir / "SKILL.md"
            lines = skill_md.read_text(encoding="utf-8").splitlines()
            lines.extend(
                f"<!-- padding line {index} -->"
                for index in range(len(lines) + 1, target_lines + 1)
            )
            skill_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = require_success(run_validator(root))
        assert_not_contains(output, "SKILL.md has 500 lines")
        assert_contains(output, "SKILL.md has 501 lines; review progressive disclosure")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_dir = root / "stateful-with-evals"
        write_skill(skill_dir, "stateful-with-evals", stateful_workflow_body())
        write_trigger_evals(skill_dir)
        output = require_success(
            run_validator(
                root,
                profile="stateful-workflow",
                require_evals=True,
            )
        )
        assert_contains(output, "Validated 1 skill(s): 0 failure(s), 0 warning(s)")
