from __future__ import annotations

import hashlib
import json
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
        *,
        package_import_name: str = "",
        release_commit: str = "",
        tag_object: str = "",
    ) -> subprocess.CompletedProcess[str]:
        args = [
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
        ]
        if package_import_name:
            args.extend(("--package-import-name", package_import_name))
        if release_commit:
            args.extend(("--release-commit", release_commit))
        if tag_object:
            args.extend(("--tag-object", tag_object))
        if mode == "push":
            urls = [
                self.git_output(
                    self.work, "remote", "get-url", "--all", "origin"
                ).splitlines(),
                self.git_output(
                    self.work, "remote", "get-url", "--push", "--all", "origin"
                ).splitlines(),
            ]
            args.extend(
                (
                    "--origin-digest",
                    hashlib.sha256(json.dumps(urls).encode()).hexdigest(),
                )
            )
        return run_command(*args, cwd=self.root, check=False)

    def write_tag_derived_package(self, *, version_without_tag: str) -> str:
        package_import_name = "demo_package"
        package_dir = self.work / "src" / package_import_name
        package_dir.mkdir(parents=True)
        (package_dir / "__init__.py").write_text(
            """from __future__ import annotations

import subprocess


result = subprocess.run(
    ["git", "describe", "--tags", "--exact-match", "--match", "demo-v*"],
    check=False,
    capture_output=True,
    text=True,
)
if result.returncode == 0:
    __version__ = result.stdout.strip().removeprefix("demo-v")
else:
    __version__ = VERSION_WITHOUT_TAG
""".replace("VERSION_WITHOUT_TAG", repr(version_without_tag)),
            encoding="utf-8",
        )
        return package_import_name

    def write_fixed_version_package(self, *, version: str) -> str:
        package_import_name = "demo_package"
        package_dir = self.work / "src" / package_import_name
        package_dir.mkdir(parents=True)
        (package_dir / "__init__.py").write_text(
            f"__version__ = {version!r}\n",
            encoding="utf-8",
        )
        return package_import_name

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
    def for_each_helper(self, assertion):
        for variant in ("canonical", "template"):
            with self.subTest(helper=variant):
                fixture = GitFixture()
                try:
                    helper = (
                        CANONICAL_HELPER
                        if variant == "canonical"
                        else fixture.render_template()
                    )
                    assertion(fixture, helper)
                finally:
                    fixture.close()

    def test_dirty_feature_prep_only_changes_content(self):
        def check(f, helper):
            f.git(f.work, "switch", "-c", "feature/current")
            (f.work / "untracked.txt").write_text("user work")
            (f.work / "staged.txt").write_text("staged work")
            f.git(f.work, "add", "staged.txt")
            head = f.git_output(f.work, "rev-parse", "HEAD")
            index = f.git_output(f.work, "write-tree")
            result = f.run_helper(helper, "prep", "1.2.3")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(f.git_output(f.work, "rev-parse", "HEAD"), head)
            self.assertEqual(f.git_output(f.work, "write-tree"), index)
            self.assertEqual(
                f.git_output(f.work, "branch", "--show-current"), "feature/current"
            )
            self.assertEqual((f.work / "untracked.txt").read_text(), "user work")
            f.assert_no_local_or_remote_ref(self, "refs/heads/release/demo-v1.2.3")
            f.assert_initial_change_released(self, "demo-v1.2.3")
            first = (f.work / "CHANGELOG.md").read_bytes()
            self.assertEqual(f.run_helper(helper, "prep", "1.2.3").returncode, 0)
            self.assertEqual((f.work / "CHANGELOG.md").read_bytes(), first)

        self.for_each_helper(check)

    def test_default_and_detached_prep_refused(self):
        def check(f, helper):
            before = (f.work / "CHANGELOG.md").read_bytes()
            for detached in (False, True):
                if detached:
                    f.git(f.work, "switch", "--detach")
                self.assertNotEqual(f.run_helper(helper, "prep", "1.2.3").returncode, 0)
                self.assertEqual((f.work / "CHANGELOG.md").read_bytes(), before)

        self.for_each_helper(check)

    def test_empty_payload_and_remote_tag_prevent_prep(self):
        def check(f, helper):
            f.git(f.work, "switch", "-c", "feature/current")
            f.write_changelog(f.work)
            before = (f.work / "CHANGELOG.md").read_bytes()
            self.assertNotEqual(f.run_helper(helper, "prep", "1.2.3").returncode, 0)
            self.assertEqual((f.work / "CHANGELOG.md").read_bytes(), before)
            f.write_changelog(f.work, unreleased="- New work")
            f.git(f.seed, "tag", "demo-v1.2.3")
            f.git(f.seed, "push", "origin", "refs/tags/demo-v1.2.3")
            self.assertNotEqual(f.run_helper(helper, "prep", "1.2.3").returncode, 0)

        self.for_each_helper(check)

    def ready(self, f, *, mismatch=False):
        f.write_changelog(f.work, release_tag="demo-v1.2.3")
        package = (
            f.write_fixed_version_package(version="0.0.0")
            if mismatch
            else f.write_tag_derived_package(version_without_tag="1.2.3.dev0")
        )
        f.git(f.work, "add", "-A")
        f.git(f.work, "commit", "-m", "Release content")
        f.git(f.work, "push", "origin", "HEAD:refs/heads/main")
        sha = f.git_output(f.work, "rev-parse", "HEAD")
        f.git(f.work, "switch", "--detach", sha)
        return sha, package

    def test_exact_merged_commit_tag_and_idempotent_push(self):
        def check(f, helper):
            sha, package = self.ready(f)
            f.git(f.seed, "pull", "--ff-only", "origin", "main")
            (f.seed / "later.txt").write_text("not part of release")
            f.git(f.seed, "add", "later.txt")
            f.git(f.seed, "commit", "-m", "Advance default")
            f.git(f.seed, "push", "origin", "main")
            result = f.run_helper(
                helper, "tag", "1.2.3", package_import_name=package, release_commit=sha
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            obj = f.git_output(f.work, "rev-parse", "refs/tags/demo-v1.2.3")
            self.assertEqual(f.git_output(f.work, "cat-file", "-t", obj), "tag")
            self.assertNotEqual(
                f.git(
                    f.root,
                    "--git-dir",
                    str(f.origin),
                    "show-ref",
                    "--verify",
                    "refs/tags/demo-v1.2.3",
                    check=False,
                ).returncode,
                0,
            )
            for _ in range(2):
                result = f.run_helper(
                    helper, "push", "1.2.3", release_commit=sha, tag_object=obj
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                f.bare_git_output("rev-parse", "refs/tags/demo-v1.2.3^{}"), sha
            )

        self.for_each_helper(check)

    def test_runtime_mismatch_removes_only_created_tag(self):
        def check(f, helper):
            sha, package = self.ready(f, mismatch=True)
            f.git(f.work, "tag", "unrelated")
            result = f.run_helper(
                helper, "tag", "1.2.3", package_import_name=package, release_commit=sha
            )
            self.assertNotEqual(result.returncode, 0)
            f.assert_no_local_or_remote_ref(self, "refs/tags/demo-v1.2.3")
            self.assertEqual(
                f.git(f.work, "show-ref", "--verify", "refs/tags/unrelated").returncode,
                0,
            )

        self.for_each_helper(check)

    def test_feature_only_commit_and_wrong_head_cannot_tag(self):
        def check(f, helper):
            sha, _ = self.ready(f)
            f.git(f.work, "switch", "-c", "feature/unmerged")
            (f.work / "extra.txt").write_text("unmerged")
            f.git(f.work, "add", "extra.txt")
            f.git(f.work, "commit", "-m", "Unmerged work")
            for commit in (sha, f.git_output(f.work, "rev-parse", "HEAD")):
                result = f.run_helper(helper, "tag", "1.2.3", release_commit=commit)
                self.assertNotEqual(result.returncode, 0)
            f.assert_no_local_or_remote_ref(self, "refs/tags/demo-v1.2.3")

        self.for_each_helper(check)

    def test_push_does_not_follow_unrelated_annotated_tags(self):
        def check(f, helper):
            sha, package = self.ready(f)
            f.git(f.work, "tag", "-a", "unrelated-v9.0.0", "-m", "Unrelated")
            f.git(f.work, "config", "push.followTags", "true")
            self.assertEqual(
                f.run_helper(
                    helper,
                    "tag",
                    "1.2.3",
                    release_commit=sha,
                    package_import_name=package,
                ).returncode,
                0,
            )
            obj = f.git_output(f.work, "rev-parse", "refs/tags/demo-v1.2.3")
            result = f.run_helper(
                helper, "push", "1.2.3", release_commit=sha, tag_object=obj
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(
                f.git(
                    f.root,
                    "--git-dir",
                    str(f.origin),
                    "show-ref",
                    "--verify",
                    "refs/tags/unrelated-v9.0.0",
                    check=False,
                ).returncode,
                0,
            )

        self.for_each_helper(check)

    def test_wrong_tag_object_cannot_push(self):
        def check(f, helper):
            sha, package = self.ready(f)
            self.assertEqual(
                f.run_helper(
                    helper,
                    "tag",
                    "1.2.3",
                    release_commit=sha,
                    package_import_name=package,
                ).returncode,
                0,
            )
            self.assertNotEqual(
                f.run_helper(
                    helper, "push", "1.2.3", release_commit=sha, tag_object=sha
                ).returncode,
                0,
            )

        self.for_each_helper(check)

    def test_multiple_push_destinations_fail_before_tag_creation(self):
        def check(f, helper):
            sha, package = self.ready(f)
            f.git(f.work, "config", "--add", "remote.origin.pushurl", str(f.origin))
            f.git(
                f.work,
                "config",
                "--add",
                "remote.origin.pushurl",
                str(f.root / "unrelated.git"),
            )
            result = f.run_helper(
                helper, "tag", "1.2.3", release_commit=sha, package_import_name=package
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one", result.stderr)
            f.assert_no_local_or_remote_ref(self, "refs/tags/demo-v1.2.3")

        self.for_each_helper(check)

    def test_missing_flag_value_and_retired_mode_fail(self):
        with GitFixtureContext() as f:
            for helper in (CANONICAL_HELPER, f.render_template()):
                self.assertNotEqual(
                    run_command(
                        "bash", str(helper), "--tag", cwd=f.root, check=False
                    ).returncode,
                    0,
                )
                self.assertNotEqual(
                    f.run_helper(helper, "publish", "1.2.3").returncode, 0
                )


class GitFixtureContext(GitFixture):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


if __name__ == "__main__":
    unittest.main()
