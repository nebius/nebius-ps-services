"""Real-Git isolation, tampering and cross-owner admission regressions."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "worktree/scripts"))
import three_tier_lifecycle as lifecycle  # noqa: E402
from git_environment import environment, GitEnvironmentError, PROFILE_ENV  # noqa: E402
from agent_runtime import runtime_environment  # noqa: E402
import workflow_execution as execution  # noqa: E402


class GitEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sdlc-profile-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "verification"
        with mock.patch.object(lifecycle, "require_command", side_effect=["29", "5", "git 2.56"]), mock.patch.object(
            lifecycle, "detect_browser", return_value=("chrome", "Chrome")
        ):
            self.state = lifecycle.prepare(self.root)
        self.project = Path(self.state["project_root"])
        self.profile = lifecycle.owned_git_origin.profile_path(self.project)
        self.env = environment(self.project, profile=self.profile)

    def git(self, *arguments, cwd=None, check=True):
        target = cwd or self.project
        return subprocess.run(["git", "-C", str(target), *arguments],
                              env=environment(target, env=self.env), capture_output=True,
                              text=True, timeout=15, check=check)

    def test_profile_has_fixed_configuration_without_changing_caller(self):
        before = dict(os.environ)
        self.assertEqual(self.git("config", "--global", "--get", "user.email").stdout.strip(),
                         "sdlc-verification@example.invalid")
        self.assertEqual(self.git("config", "--get", "core.attributesFile").stdout.strip(),
                         str(self.profile.parent / "attributes"))
        self.assertEqual(dict(os.environ), before)

    def test_profile_tampering_and_missing_files_fail_closed(self):
        for name, data in (("config", b"[filter \"bad\"]\n clean = false\n"),
                           ("attributes", b"* text eol=crlf\n")):
            path = self.profile.parent / name
            original = path.read_bytes()
            path.write_bytes(data)
            with self.assertRaises(GitEnvironmentError):
                environment(self.project, env=self.env)
            path.write_bytes(original)
        self.profile.unlink()
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)

    def test_profile_permissions_links_and_foreign_pointer_are_rejected(self):
        config = self.profile.parent / "config"
        config.chmod(0o644)
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)
        config.chmod(0o600)
        alias = self.profile.parent / "alias"
        os.link(config, alias)
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)
        alias.unlink()
        data = config.read_bytes()
        config.unlink()
        alias.write_bytes(data)
        alias.chmod(0o600)
        config.symlink_to(alias)
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)
        with self.assertRaises(GitEnvironmentError):
            environment(self.base, env=self.env)

    def test_stale_generation_and_changed_origin_receipt_are_rejected(self):
        active = self.root / "three-tier-live/active.json"
        original = active.read_bytes()
        value = json.loads(original)
        value["verification_id"] = "f" * 32
        active.write_text(json.dumps(value))
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)
        active.write_bytes(original)
        receipt = Path(self.state["run_root"]) / ".owned-git-origin.json"
        receipt.write_bytes(receipt.read_bytes() + b" ")
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)

    def test_unsealed_profile_is_not_admitted_to_normal_commands(self):
        value = json.loads(self.profile.read_bytes())
        value["origin_receipt_sha256"] = None
        self.profile.write_text(json.dumps(value))
        with self.assertRaisesRegex(GitEnvironmentError, "not sealed"):
            environment(self.project, env=self.env)

    def test_local_executable_and_inactive_include_configuration_is_rejected(self):
        config = self.project / ".git/config"
        original = config.read_bytes()
        for addition in (
            '[filter "bad"]\n clean = false\n',
            '[includeIf "onbranch:never-created"]\n path = /unrelated/config\n',
            '[diff "bad"]\n textconv = false\n',
            '[gpg]\n program = false\n',
            '[core]\n attributesFile = /unrelated/attributes\n',
        ):
            config.write_bytes(original + addition.encode())
            with self.assertRaisesRegex(GitEnvironmentError, "repository-local"):
                environment(self.project, env=self.env)
        config.write_bytes(original)

    def test_caller_overrides_are_not_masked_by_a_valid_profile(self):
        for key, value in (("GIT_CONFIG_GLOBAL", "/dev/null"), ("GIT_CONFIG_COUNT", "0"),
                           ("GIT_DIR", str(self.project / ".git")),
                           ("GIT_INDEX_FILE", str(self.base / "index")),
                           ("GIT_SSH_COMMAND", "false"), ("GIT_ATTR_SOURCE", "HEAD")):
            with self.assertRaises(GitEnvironmentError):
                environment(self.project, env={**self.env, key: value})

    def test_workflow_worker_scope_and_runtime_propagation(self):
        worker = Path(self.state["private_root"]) / "worker"
        with mock.patch.dict(os.environ, self.env):
            execution.git(self.project, ["worktree", "add", "-b", "worker", str(worker)], "create worker")
            derived = runtime_environment("codex", home=Path(self.state["private_root"]) / "codex-home",
                                          fresh_session=True, cwd=worker)
            self.assertEqual(derived[PROFILE_ENV], str(self.profile))
            self.assertEqual(execution.git(worker, ["rev-parse", "HEAD"], "read worker"),
                             self.state["git"]["baseline_sha"])
            with self.assertRaises(GitEnvironmentError):
                runtime_environment("codex", fresh_session=True, cwd=ROOT)

    def test_commit_owner_and_hook_admit_only_the_owned_scope(self):
        for name, relative in (("profile_commit_transaction", "commit/scripts/commit_transaction.py"),
                               ("profile_commit_intent", "commit/assets/hooks/commit_intent.py")):
            spec = importlib.util.spec_from_file_location(name, ROOT / relative)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with mock.patch.dict(os.environ, self.env):
                self.assertEqual(module._git_environment(self.project)[PROFILE_ENV], str(self.profile))
                with self.assertRaises((module.TransactionError if hasattr(module, "TransactionError") else module.IntentError)):
                    module._git_environment(ROOT)
            with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null"}):
                with self.assertRaises((module.TransactionError if hasattr(module, "TransactionError") else module.IntentError)):
                    module._git_environment(ROOT)

    def test_native_launcher_validates_and_passes_profile_to_fresh_process(self):
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("run_with_git_environment.py")),
            "--profile", str(self.profile), "--cwd", str(self.project), "--", sys.executable,
            "-c", "import os,subprocess; assert os.environ['SKILLS_GIT_PROFILE']; subprocess.run(['git','status','--porcelain'],check=True)",
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_origin_push_guard_remains_effective(self):
        (self.project / "change.txt").write_text("change\n")
        self.git("add", "-A")
        self.git("commit", "-m", "local change")
        result = self.git("push", "origin", "main", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=Path(self.state["run_root"]) / "origin.git").stdout.strip(),
                         self.state["git"]["baseline_sha"])

    def test_promotion_owner_revalidates_profile_before_git(self):
        import git_promotion as promotion
        with mock.patch.dict(os.environ, {PROFILE_ENV: str(self.profile)}):
            observed = promotion.observe_implementation_source(self.project)
            self.assertEqual(observed["promotion_initial_head"], self.state["git"]["baseline_sha"])
            (self.profile.parent / "attributes").write_text("* invalid-profile\n")
            with self.assertRaises(promotion.GitPromotionError):
                promotion.ensure_promotion_branch(self.project, lifecycle_id="test", task_slug="test")

    def test_sdlc_hook_git_reads_reject_tampered_profile(self):
        path = ROOT / "sdlc-start/assets/hooks/lib/sdlc_state.py"
        spec = importlib.util.spec_from_file_location("profile_sdlc_state", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(module.run_git(self.project, ["rev-parse", "HEAD"]), self.state["git"]["baseline_sha"])
            (self.profile.parent / "attributes").write_text("* invalid-profile\n")
            for action in (lambda: module.run_git(self.project, ["rev-parse", "HEAD"]),
                           lambda: module.staged_diff(self.project)):
                with self.subTest(action=action), self.assertRaises(GitEnvironmentError):
                    action()
            hook = subprocess.run(
                [sys.executable, str(ROOT / "sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py")],
                input=json.dumps({"hook_event_name": "PreToolUse", "cwd": str(self.project), "tool_name": "Bash",
                                  "tool_input": {"command": "git status"}}),
                env={**os.environ, "CODEX_HOME": str(Path(self.state["private_root"]) / "codex-home")},
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertEqual(json.loads(hook.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_nested_foreign_bare_repository_is_not_owned_scope(self):
        worker = Path(self.state["private_root"]) / "worker"
        self.git("worktree", "add", "-b", "worker", str(worker))
        for parent in (self.project, worker):
            nested = parent / "nested.git"
            self.git("init", "--bare", "--template=", str(nested))
            with self.assertRaises(GitEnvironmentError):
                environment(nested, env=self.env)

    def test_disabled_origin_guard_blocks_project_worker_and_origin_commands(self):
        origin = Path(self.state["run_root"]) / "origin.git"
        worker = Path(self.state["private_root"]) / "worker"
        self.git("worktree", "add", "-b", "worker", str(worker))
        hook = origin / "hooks/pre-receive"
        for mutation in (lambda: hook.chmod(0o644), hook.unlink):
            mutation()
            for cwd in (self.project, worker, origin):
                with self.assertRaises(GitEnvironmentError):
                    environment(cwd, env=self.env)
            if hook.exists():
                hook.chmod(0o755)

    def test_removed_origin_blocks_sealed_profile_admission(self):
        origin = Path(self.state["run_root"]) / "origin.git"
        origin.rename(origin.with_name("removed-origin"))
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)

    def test_common_directory_redirects_and_borrowed_storage_are_rejected(self):
        foreign = self.base / "foreign.git"
        self.git("init", "--bare", "--template=", str(foreign))
        primary = self.project / ".git"
        origin = Path(self.state["run_root"]) / "origin.git"
        for metadata, relative in ((primary, "commondir"), (origin, "commondir"),
                                   (primary, "objects/info/alternates")):
            path = metadata / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(foreign) + "\n")
            try:
                with self.subTest(relative=relative, metadata=metadata.name), self.assertRaises(GitEnvironmentError):
                    environment(self.project, env=self.env)
            finally:
                path.unlink()

    def test_nested_common_directory_without_objects_is_not_owned_scope(self):
        foreign = self.base / "foreign.git"
        self.git("init", "--bare", "--template=", str(foreign))
        nested = self.project / "nested.git"
        nested.mkdir()
        (nested / "HEAD").write_bytes((foreign / "HEAD").read_bytes())
        (nested / "commondir").write_text(str(foreign) + "\n")
        with self.assertRaises(GitEnvironmentError):
            environment(nested, env=self.env)

    def test_global_attributes_do_not_enter_owned_checkout(self):
        home, xdg = self.base / "home", self.base / "xdg"
        home.mkdir()
        (xdg / "git").mkdir(parents=True)
        (xdg / "git/attributes").write_text("* ambient-marker=leaked\n")
        env = environment(self.project, env={**self.env, "HOME": str(home), "XDG_CONFIG_HOME": str(xdg)})
        result = subprocess.run(["git", "-C", str(self.project), "check-attr", "ambient-marker", "--", "example.txt"],
                                env=env, capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(result.stdout.strip(), "example.txt: ambient-marker: unspecified")

    def test_tampering_blocks_cleanup_before_resource_operations(self):
        (self.profile.parent / "attributes").write_text("* text\n")
        with mock.patch.object(lifecycle, "discover_owned_resources") as discover:
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.destroy(self.root)
            discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
