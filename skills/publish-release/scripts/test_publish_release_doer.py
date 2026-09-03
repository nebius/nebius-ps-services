#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
CANONICAL_HELPER = SKILL_DIR / "scripts" / "publish-release-doer.sh"
TEMPLATE_HELPER = SKILL_DIR / "assets" / "publish-release.sh.template"
TAG_PREFIX = "demo"


def run_command(
    *args: str,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
            "NO_COLOR": "1",
        }
    )
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class GitFixture:
    def __init__(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.origin = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.work = self.root / "work"
        self._initialize()

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def git(
        self,
        cwd: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return run_command("git", *args, cwd=cwd, check=check)

    def git_output(self, cwd: Path, *args: str) -> str:
        return self.git(cwd, *args).stdout.strip()

    def bare_git_output(self, *args: str) -> str:
        return self.git_output(self.root, "--git-dir", str(self.origin), *args)

    def _configure_identity(self, checkout: Path) -> None:
        self.git(checkout, "config", "user.name", "Release Test")
        self.git(checkout, "config", "user.email", "release-test@example.invalid")

    def _initialize(self) -> None:
        self.git(self.root, "init", "--bare", str(self.origin))
        self.git(
            self.root,
            "--git-dir",
            str(self.origin),
            "symbolic-ref",
            "HEAD",
            "refs/heads/main",
        )
        self.seed.mkdir()
        self.git(self.seed, "init", "--initial-branch=main")
        self._configure_identity(self.seed)
        self.write_changelog(self.seed, unreleased="- Initial change.")
        self.git(self.seed, "add", "CHANGELOG.md")
        self.git(self.seed, "commit", "-m", "Initial changelog")
        self.git(self.seed, "remote", "add", "origin", str(self.origin))
        self.git(self.seed, "push", "-u", "origin", "main")
        self.git(self.root, "clone", str(self.origin), str(self.work))
        self._configure_identity(self.work)

    @staticmethod
    def write_changelog(
        checkout: Path,
        *,
        unreleased: str = "",
        release_tag: str | None = None,
    ) -> None:
        text = "# Changelog\n\n## [Unreleased]\n\n"
        if unreleased:
            text += f"{unreleased}\n"
        if release_tag:
            text += f"\n## [{release_tag}] - 2026-09-03\n\n- Released change.\n"
        (checkout / "CHANGELOG.md").write_text(text, encoding="utf-8")

    def render_template(self) -> Path:
        rendered = TEMPLATE_HELPER.read_text(encoding="utf-8")
        replacements = {
            "__ASSET_GLOB__": "dist/*.whl",
            "__MAIN_BRANCH__": "main",
            "__PACKAGE_IMPORT_NAME__": "",
            "__PROJECT_TAG_PREFIX__": TAG_PREFIX,
        }
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)
        helper = self.root / "publish-release.sh"
        helper.write_text(rendered, encoding="utf-8")
        helper.chmod(0o755)
        return helper

    def run_helper(
        self,
        helper: Path,
        mode: str,
        version: str,
    ) -> subprocess.CompletedProcess[str]:
        return run_command(
            "bash",
            str(helper),
            "--mode",
            mode,
            "--tag",
            version,
            "--tag-prefix",
            TAG_PREFIX,
            "--project-dir",
            str(self.work),
            "--main-branch",
            "main",
            "--changelog",
            "CHANGELOG.md",
            cwd=self.root,
            check=False,
        )

    def assert_no_local_or_remote_ref(self, test: unittest.TestCase, ref: str) -> None:
        local = self.git(
            self.work,
            "show-ref",
            "--verify",
            "--quiet",
            ref,
            check=False,
        )
        remote = self.git(
            self.root,
            "--git-dir",
            str(self.origin),
            "show-ref",
            "--verify",
            "--quiet",
            ref,
            check=False,
        )
        test.assertNotEqual(local.returncode, 0)
        test.assertNotEqual(remote.returncode, 0)

    def assert_initial_change_released(
        self,
        test: unittest.TestCase,
        tag: str,
    ) -> None:
        changelog = (self.work / "CHANGELOG.md").read_text(encoding="utf-8")
        release_heading = f"## [{tag}]"
        test.assertIn(release_heading, changelog)
        unreleased, released = changelog.split(release_heading, maxsplit=1)
        test.assertNotIn("- Initial change.", unreleased)
        test.assertIn("- Initial change.", released)


class PublishReleaseDoerTests(unittest.TestCase):
    def helper_variants(self, fixture: GitFixture) -> tuple[tuple[str, Path], ...]:
        return (
            ("canonical", CANONICAL_HELPER),
            ("rendered-template", fixture.render_template()),
        )

    def for_each_helper(self, assertion) -> None:  # type: ignore[no-untyped-def]
        for variant in ("canonical", "rendered-template"):
            with self.subTest(helper=variant):
                fixture = GitFixture()
                try:
                    helper = dict(self.helper_variants(fixture))[variant]
                    assertion(fixture, helper)
                finally:
                    fixture.close()

    def test_main_prep_creates_release_branch(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            original_main = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            result = fixture.run_helper(helper, "prep", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            release_branch = f"release/{TAG_PREFIX}-v1.2.3"
            self.assertEqual(
                fixture.git_output(fixture.work, "branch", "--show-current"),
                release_branch,
            )
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD^"),
                original_main,
            )
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "main"),
                original_main,
            )
            self.assertEqual(
                fixture.bare_git_output("rev-parse", f"refs/heads/{release_branch}"),
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
            )
            self.assertEqual(
                fixture.git_output(
                    fixture.work,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ),
                "CHANGELOG.md",
            )
            fixture.assert_initial_change_released(
                self,
                f"{TAG_PREFIX}-v1.2.3",
            )

        self.for_each_helper(assertion)

    def test_feature_prep_reuses_current_branch(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "feature/release"
            fixture.git(fixture.work, "switch", "-c", branch)
            (fixture.work / "feature.txt").write_text("feature\n", encoding="utf-8")
            fixture.git(fixture.work, "add", "feature.txt")
            fixture.git(fixture.work, "commit", "-m", "Feature change")
            result = fixture.run_helper(helper, "prep", "2.0.0")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture.git_output(fixture.work, "branch", "--show-current"),
                branch,
            )
            self.assertEqual(
                fixture.bare_git_output("rev-parse", f"refs/heads/{branch}"),
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
            )
            fixture.assert_no_local_or_remote_ref(
                self,
                f"refs/heads/release/{TAG_PREFIX}-v2.0.0",
            )
            self.assertEqual(
                fixture.git_output(
                    fixture.work,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ),
                "CHANGELOG.md",
            )
            fixture.assert_initial_change_released(
                self,
                f"{TAG_PREFIX}-v2.0.0",
            )

        self.for_each_helper(assertion)

    def test_pushed_feature_prep_fast_forwards_current_branch(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "feature/pushed-release"
            fixture.git(fixture.work, "switch", "-c", branch)
            (fixture.work / "feature.txt").write_text("feature\n", encoding="utf-8")
            fixture.git(fixture.work, "add", "feature.txt")
            fixture.git(fixture.work, "commit", "-m", "Feature change")
            fixture.git(fixture.work, "push", "-u", "origin", branch)
            (fixture.work / "follow-up.txt").write_text("follow-up\n", encoding="utf-8")
            fixture.git(fixture.work, "add", "follow-up.txt")
            fixture.git(fixture.work, "commit", "-m", "Feature follow-up")

            result = fixture.run_helper(helper, "prep", "2.0.1")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture.bare_git_output("rev-parse", f"refs/heads/{branch}"),
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
            )
            fixture.assert_initial_change_released(
                self,
                f"{TAG_PREFIX}-v2.0.1",
            )

        self.for_each_helper(assertion)

    def test_ref_like_feature_name_pushes_an_exact_remote_head(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "refs/tags/release-prep"
            fixture.git(fixture.work, "switch", "-c", branch)

            result = fixture.run_helper(helper, "prep", "2.0.2")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture.bare_git_output("rev-parse", f"refs/heads/{branch}"),
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
            )
            remote_tag = fixture.git(
                fixture.root,
                "--git-dir",
                str(fixture.origin),
                "show-ref",
                "--verify",
                "--quiet",
                "refs/tags/release-prep",
                check=False,
            )
            self.assertNotEqual(remote_tag.returncode, 0)

        self.for_each_helper(assertion)

    def test_empty_unreleased_prep_fails_before_branch_or_commit(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            fixture.write_changelog(fixture.work)
            fixture.git(fixture.work, "add", "CHANGELOG.md")
            fixture.git(fixture.work, "commit", "-m", "Empty unreleased notes")
            fixture.git(fixture.work, "push", "origin", "main")
            before_head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            before_changelog = (fixture.work / "CHANGELOG.md").read_bytes()

            result = fixture.run_helper(helper, "prep", "2.0.3")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No changelog content is available", result.stderr)
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
                before_head,
            )
            self.assertEqual(
                fixture.git_output(fixture.work, "branch", "--show-current"),
                "main",
            )
            self.assertEqual(
                (fixture.work / "CHANGELOG.md").read_bytes(),
                before_changelog,
            )
            fixture.assert_no_local_or_remote_ref(
                self,
                f"refs/heads/release/{TAG_PREFIX}-v2.0.3",
            )

        self.for_each_helper(assertion)

    def test_dirty_feature_prep_fails_before_mutation(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "feature/dirty"
            fixture.git(fixture.work, "switch", "-c", branch)
            before_head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            before_changelog = (fixture.work / "CHANGELOG.md").read_bytes()
            (fixture.work / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            result = fixture.run_helper(helper, "prep", "2.1.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Working tree is not clean", result.stderr)
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
                before_head,
            )
            self.assertEqual(
                (fixture.work / "CHANGELOG.md").read_bytes(),
                before_changelog,
            )
            fixture.assert_no_local_or_remote_ref(
                self,
                f"refs/heads/release/{TAG_PREFIX}-v2.1.0",
            )

        self.for_each_helper(assertion)

    def test_detached_prep_fails_before_mutation(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            fixture.git(fixture.work, "switch", "--detach")
            before_head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            before_changelog = (fixture.work / "CHANGELOG.md").read_bytes()
            result = fixture.run_helper(helper, "prep", "2.2.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("detached HEAD", result.stderr)
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
                before_head,
            )
            self.assertEqual(
                (fixture.work / "CHANGELOG.md").read_bytes(),
                before_changelog,
            )

        self.for_each_helper(assertion)

    def test_stale_feature_prep_fails_before_mutation(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "feature/stale"
            fixture.git(fixture.work, "switch", "-c", branch)
            (fixture.seed / "main-change.txt").write_text("new main\n", encoding="utf-8")
            fixture.git(fixture.seed, "add", "main-change.txt")
            fixture.git(fixture.seed, "commit", "-m", "Advance main")
            fixture.git(fixture.seed, "push", "origin", "main")
            before_head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            before_changelog = (fixture.work / "CHANGELOG.md").read_bytes()
            result = fixture.run_helper(helper, "prep", "2.3.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not contain the latest origin/main", result.stderr)
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
                before_head,
            )
            self.assertEqual(
                (fixture.work / "CHANGELOG.md").read_bytes(),
                before_changelog,
            )

        self.for_each_helper(assertion)

    def test_diverged_remote_feature_fails_before_mutation(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "feature/diverged"
            fixture.git(fixture.work, "switch", "-c", branch)
            (fixture.work / "shared.txt").write_text("base\n", encoding="utf-8")
            fixture.git(fixture.work, "add", "shared.txt")
            fixture.git(fixture.work, "commit", "-m", "Feature base")
            fixture.git(fixture.work, "push", "-u", "origin", branch)

            peer = fixture.root / "peer"
            fixture.git(fixture.root, "clone", str(fixture.origin), str(peer))
            fixture._configure_identity(peer)
            fixture.git(peer, "switch", branch)
            (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
            fixture.git(peer, "add", "remote.txt")
            fixture.git(peer, "commit", "-m", "Remote feature change")
            fixture.git(peer, "push", "origin", branch)

            (fixture.work / "local.txt").write_text("local\n", encoding="utf-8")
            fixture.git(fixture.work, "add", "local.txt")
            fixture.git(fixture.work, "commit", "-m", "Local feature change")
            before_head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            before_changelog = (fixture.work / "CHANGELOG.md").read_bytes()
            result = fixture.run_helper(helper, "prep", "2.4.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("would not fast-forward", result.stderr)
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
                before_head,
            )
            self.assertEqual(
                (fixture.work / "CHANGELOG.md").read_bytes(),
                before_changelog,
            )

        self.for_each_helper(assertion)

    def test_feature_publish_is_rejected_without_a_tag(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            branch = "feature/publish"
            fixture.git(fixture.work, "switch", "-c", branch)
            fixture.write_changelog(
                fixture.work,
                release_tag=f"{TAG_PREFIX}-v3.0.0",
            )
            fixture.git(fixture.work, "add", "CHANGELOG.md")
            fixture.git(fixture.work, "commit", "-m", "Prepare release")
            result = fixture.run_helper(helper, "publish", "3.0.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must run from main", result.stderr)
            fixture.assert_no_local_or_remote_ref(
                self,
                f"refs/tags/{TAG_PREFIX}-v3.0.0",
            )

        self.for_each_helper(assertion)

    def test_clean_synced_main_publish_pushes_annotated_tag(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            tag = f"{TAG_PREFIX}-v3.1.0"
            fixture.write_changelog(fixture.work, release_tag=tag)
            fixture.git(fixture.work, "add", "CHANGELOG.md")
            fixture.git(fixture.work, "commit", "-m", "Prepare release")
            fixture.git(fixture.work, "push", "origin", "main")
            expected_commit = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            result = fixture.run_helper(helper, "publish", "3.1.0")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                fixture.git_output(
                    fixture.work,
                    "cat-file",
                    "-t",
                    f"refs/tags/{tag}",
                ),
                "tag",
            )
            self.assertEqual(
                fixture.bare_git_output("rev-parse", f"refs/tags/{tag}^{{}}"),
                expected_commit,
            )

        self.for_each_helper(assertion)

    def test_diverged_main_publish_fails_without_a_tag(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            tag = f"{TAG_PREFIX}-v3.2.0"
            fixture.write_changelog(fixture.work, release_tag=tag)
            fixture.git(fixture.work, "add", "CHANGELOG.md")
            fixture.git(fixture.work, "commit", "-m", "Local release only")
            result = fixture.run_helper(helper, "publish", "3.2.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is not at origin/main", result.stderr)
            fixture.assert_no_local_or_remote_ref(self, f"refs/tags/{tag}")

        self.for_each_helper(assertion)

    def test_publish_refreshes_main_without_configured_fetch_refspec(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            tag = f"{TAG_PREFIX}-v3.2.1"
            fixture.write_changelog(fixture.work, release_tag=tag)
            fixture.git(fixture.work, "add", "CHANGELOG.md")
            fixture.git(fixture.work, "commit", "-m", "Prepare release")
            fixture.git(fixture.work, "push", "origin", "main")

            fixture.git(fixture.seed, "pull", "--ff-only", "origin", "main")
            (fixture.seed / "remote-main.txt").write_text(
                "remote main advanced\n",
                encoding="utf-8",
            )
            fixture.git(fixture.seed, "add", "remote-main.txt")
            fixture.git(fixture.seed, "commit", "-m", "Advance remote main")
            fixture.git(fixture.seed, "push", "origin", "main")
            fixture.git(
                fixture.work,
                "config",
                "--unset-all",
                "remote.origin.fetch",
            )

            result = fixture.run_helper(helper, "publish", "3.2.1")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is not at origin/main", result.stderr)
            fixture.assert_no_local_or_remote_ref(self, f"refs/tags/{tag}")

        self.for_each_helper(assertion)

    def test_duplicate_tag_blocks_prep_before_mutation(self) -> None:
        def assertion(fixture: GitFixture, helper: Path) -> None:
            tag = f"{TAG_PREFIX}-v3.3.0"
            fixture.git(fixture.work, "tag", "-a", tag, "-m", f"Release {tag}")
            fixture.git(fixture.work, "push", "origin", f"refs/tags/{tag}")
            before_head = fixture.git_output(fixture.work, "rev-parse", "HEAD")
            before_changelog = (fixture.work / "CHANGELOG.md").read_bytes()
            result = fixture.run_helper(helper, "prep", "3.3.0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("tag already exists", result.stderr.lower())
            self.assertEqual(
                fixture.git_output(fixture.work, "rev-parse", "HEAD"),
                before_head,
            )
            self.assertEqual(
                (fixture.work / "CHANGELOG.md").read_bytes(),
                before_changelog,
            )

        self.for_each_helper(assertion)


if __name__ == "__main__":
    unittest.main()
