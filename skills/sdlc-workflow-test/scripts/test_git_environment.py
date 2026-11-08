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
import run_with_git_environment as native  # noqa: E402
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

    def retain_large_lifecycle(self):
        active = self.root / "three-tier-live/active.json"
        for index in range(110):
            lifecycle.record_validation(
                self.root, f"retained check {index}", "PASS",
                "Independent observation retained for diagnosis. " * 10,
            )
        self.assertGreater(active.stat().st_size, 65536)
        self.assertLess(active.stat().st_size, 1024 * 1024)
        return active

    def test_retained_lifecycle_admits_git_workers_and_exact_cleanup(self):
        active = self.retain_large_lifecycle()
        retained = lifecycle.read_json(active)["validations"]
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        with mock.patch.dict(os.environ, self.env):
            derived = runtime_environment(
                "codex", home=Path(self.state["private_root"]) / "codex-home",
                fresh_session=True, cwd=self.project,
            )
        self.assertEqual(derived[PROFILE_ENV], str(self.profile))
        lifecycle.finish(self.root, "FAIL", False)
        with mock.patch.object(lifecycle, "discover_owned_resources", return_value={
            kind: [] for kind in lifecycle.RESOURCE_KINDS
        }):
            outcome, archived = lifecycle.destroy(self.root)
        self.assertEqual(outcome, "DESTROYED")
        self.assertEqual(archived["validations"], retained)
        self.assertFalse(active.exists())
        self.assertFalse(Path(self.state["run_root"]).exists())

    def test_lifecycle_reader_limit_matches_owner_at_one_mib(self):
        active = self.root / "three-tier-live/active.json"
        payload = active.read_bytes()
        active.write_bytes(payload + b" " * (1024 * 1024 - len(payload)))
        self.assertEqual(lifecycle.read_json(active)["verification_id"], self.state["verification_id"])
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.state["git"]["baseline_sha"])
        with active.open("ab") as target:
            target.write(b" ")
        with self.assertRaisesRegex(lifecycle.LifecycleError, "1 MiB"):
            lifecycle.read_json(active)
        with self.assertRaisesRegex(GitEnvironmentError, "1048576-byte limit"):
            environment(self.project, env=self.env)

    def test_profile_retains_small_file_limit(self):
        payload = self.profile.read_bytes()
        self.profile.write_bytes(payload + b" " * (65536 - len(payload)))
        environment(self.project, env=self.env)
        with self.profile.open("ab") as target:
            target.write(b" ")
        with self.assertRaisesRegex(GitEnvironmentError, "65536-byte limit"):
            environment(self.project, env=self.env)

    def test_large_lifecycle_still_rejects_stale_generation(self):
        active = self.retain_large_lifecycle()
        value = lifecycle.read_json(active)
        value["verification_id"] = "f" * 32
        lifecycle.private_json(active, value)
        with self.assertRaisesRegex(GitEnvironmentError, "generation is no longer active"):
            environment(self.project, env=self.env)

    def test_lifecycle_integrity_failure_is_not_misreported_as_json(self):
        active = self.root / "three-tier-live/active.json"
        active.chmod(0o644)
        with self.assertRaisesRegex(GitEnvironmentError, "unsafe profile or repository file"):
            environment(self.project, env=self.env)

    def test_malformed_lifecycle_reports_json_failure(self):
        active = self.root / "three-tier-live/active.json"
        active.write_bytes(b"{malformed")
        with self.assertRaisesRegex(GitEnvironmentError, "invalid profile JSON"):
            environment(self.project, env=self.env)

    def test_unsealed_profile_is_not_admitted_to_normal_commands(self):
        value = json.loads(self.profile.read_bytes())
        value["origin_receipt_sha256"] = None
        self.profile.write_text(json.dumps(value))
        with self.assertRaisesRegex(GitEnvironmentError, "not sealed"):
            environment(self.project, env=self.env)

    def test_editor_history_hint_preserves_checkout_worker_and_origin_identity(self):
        worker = Path(self.state["private_root"]) / "worker"
        self.git("worktree", "add", "-b", "worker", str(worker))
        config = self.project / ".git/config"
        baseline = self.git("rev-parse", "HEAD").stdout
        origin_refs = self.git("show-ref", cwd=Path(self.state["run_root"]) / "origin.git").stdout
        original_profile = self.profile.read_bytes()
        config.write_bytes(config.read_bytes() + (
            b'[branch "main"]\n vscode-merge-base = origin/main\n'
            b' vscode-merge-base = origin/main\n'
            b'[branch "worker"]\n vscode-merge-base = origin/main\n'
        ))
        retained = config.read_bytes()
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, baseline)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        with mock.patch.dict(os.environ, self.env):
            derived = runtime_environment(
                "codex", home=Path(self.state["private_root"]) / "codex-home",
                fresh_session=True, cwd=worker,
            )
        self.assertEqual(derived[PROFILE_ENV], str(self.profile))
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=worker).stdout, baseline)
        self.assertEqual(self.git("show-ref", cwd=Path(self.state["run_root"]) / "origin.git").stdout, origin_refs)
        self.assertEqual(config.read_bytes(), retained)
        self.assertEqual(self.profile.read_bytes(), original_profile)

    def test_editor_history_hint_does_not_admit_other_values_or_configuration(self):
        config = self.project / ".git/config"
        original = config.read_bytes()
        for addition in (
            '[branch "main"]\n vscode-merge-base = main\n',
            '[branch "main"]\n vscode-merge-base = foreign/main\n',
            '[branch "main"]\n vscode-merge-base = origin/other\n',
            '[branch "main"]\n vscode-merge-base = " origin/main"\n',
            '[branch "main"]\n vscode-merge-base = "origin/main\\nextra"\n',
            '[branch "main"]\n vscode-merge-base = --help\n',
            '[branch "main"]\n vscode-merge-base = origin/main\n vscode-merge-base = origin/other\n',
            '[branch "bad name"]\n vscode-merge-base = origin/main\n',
            '[branch "main"]\n vscode-other = origin/main\n',
            '[branch "main"]\n mergeOptions = --no-verify\n',
        ):
            with self.subTest(addition=addition):
                config.write_bytes(original + addition.encode())
                with self.assertRaises(GitEnvironmentError):
                    environment(self.project, env=self.env)
        config.write_bytes(original)

    def test_editor_history_hint_rejects_bare_origin_and_unsealed_profiles(self):
        config = self.project / ".git/config"
        origin_config = Path(self.state["run_root"]) / "origin.git/config"
        original_origin = origin_config.read_bytes()
        hint = b'[branch "main"]\n vscode-merge-base = origin/main\n'
        origin_config.write_bytes(original_origin + hint)
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env)
        origin_config.write_bytes(original_origin)
        config.write_bytes(config.read_bytes() + hint)
        profile = json.loads(self.profile.read_text())
        profile["origin_receipt_sha256"] = None
        self.profile.write_text(json.dumps(profile))
        with self.assertRaises(GitEnvironmentError):
            environment(self.project, env=self.env, bootstrap=True)

    def test_editor_history_hint_rechecks_receipt_before_using_default(self):
        import git_environment

        config = self.project / ".git/config"
        config.write_bytes(config.read_bytes() + b'[branch "main"]\n vscode-merge-base = origin/main\n')
        receipt = Path(self.state["run_root"]) / ".owned-git-origin.json"
        original_read = git_environment._read
        receipt_reads = 0

        def changing_read(path, *args, **kwargs):
            nonlocal receipt_reads
            data = original_read(path, *args, **kwargs)
            if path == receipt:
                receipt_reads += 1
                if receipt_reads >= 3:
                    return data + b" "
            return data

        with mock.patch.object(git_environment, "_read", side_effect=changing_read):
            with self.assertRaisesRegex(GitEnvironmentError, "sealed origin receipt changed"):
                environment(self.project, env=self.env)

    def test_editor_history_hint_allows_exact_cleanup_with_failed_evidence_retained(self):
        config = self.project / ".git/config"
        config.write_bytes(config.read_bytes() + b'[branch "main"]\n vscode-merge-base = origin/main\n')
        lifecycle.record_validation(self.root, "native launch", "FAIL", "Retain the original rejected launch.")
        lifecycle.finish(self.root, "FAIL", False)
        with mock.patch.object(lifecycle, "discover_owned_resources", return_value={
            kind: [] for kind in lifecycle.RESOURCE_KINDS
        }):
            outcome, archived = lifecycle.destroy(self.root)
        self.assertEqual(outcome, "DESTROYED")
        self.assertEqual(archived["result"], "FAIL")
        self.assertEqual(archived["validations"][0]["status"], "FAIL")
        self.assertFalse(Path(self.state["run_root"]).exists())

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
                with self.assertRaises(module.TransactionError if hasattr(module, "TransactionError") else module.IntentError):
                    module._git_environment(ROOT)
            with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null"}):
                with self.assertRaises(module.TransactionError if hasattr(module, "TransactionError") else module.IntentError):
                    module._git_environment(ROOT)

    def test_native_launcher_validates_and_passes_profile_to_fresh_process(self):
        self.retain_large_lifecycle()
        native_home = Path(self.state["private_root"]) / "codex-home"
        config = native_home / "config.toml"
        config.write_text('project_root_markers = [".git"]\nallow_login_shell = false\n[features]\nshell_snapshot = false\n'
                          '[shell_environment_policy]\ninherit = "all"\nignore_default_excludes = false\n')
        config.chmod(0o600)
        synthetic = {"GITHUB_TOKEN": "synthetic-do-not-inherit", "UNRELATED_ACCESS": "synthetic-other",
                     "BASH_ENV": str(self.base / "untrusted-shell-init"),
                     "CODEX_HOME": str(native_home), "CODEX_THREAD_ID": "parent-identity"}
        probe = self.base / 'codex'
        probe.write_text(f'#!{sys.executable}\n' +
                         "import sys\nexec(sys.argv[2])\n")
        probe.chmod(0o700)
        # This test uses a synthetic native executable. Model that host's
        # installation discovery while retaining the actual launcher/exec path.
        launch = (
            "import sys;from pathlib import Path;from unittest.mock import patch; "
            "sys.path.insert(0,sys.argv.pop(1)); import run_with_git_environment as native; "
            "import three_tier_lifecycle; "
            "catalog=Path(native.__file__).resolve().parents[2]; "
            "context=patch('agent_runtime.installed_skills_dir',return_value=catalog); context.start(); "
            "raise SystemExit(native.main(sys.argv[1:]))"
        )
        result = subprocess.run([
            sys.executable, "-B", "-c", launch, str(Path(__file__).parent),
            "--profile", str(self.profile), "--cwd", str(self.project), "--", str(probe),
            "--no-daemon", "import os,subprocess; assert not any(k in os.environ for k in "
            "['GITHUB_TOKEN','UNRELATED_ACCESS','CODEX_THREAD_ID']); "
            "assert os.environ['SKILLS_GIT_PROFILE']; "
            "from pathlib import Path; p=Path(os.environ['CODEX_HOME'])/'private-mode-probe'; "
            "p.write_text('synthetic'); assert p.stat().st_mode & 0o777 == 0o600; "
            "assert os.environ['ZDOTDIR'] == os.environ['CODEX_HOME']; "
            "assert os.environ['BASH_ENV'] == '/dev/null'; "
            "subprocess.run(['git','status','--porcelain'],check=True); "
            f"import sys; sys.path.insert(0, {str(ROOT / 'global-context-management/scripts')!r}); "
            "from agent_runtime import runtime_environment; "
            "worker=runtime_environment('codex',fresh_session=True); "
            "assert not any(k in worker for k in ['GITHUB_TOKEN','UNRELATED_ACCESS','CODEX_THREAD_ID']); "
            "assert worker['SKILLS_GIT_PROFILE'] == os.environ['SKILLS_GIT_PROFILE']; "
            "subprocess.run([sys.executable,'-c',\"import os; assert 'GITHUB_TOKEN' not in os.environ\"],"
            "env=worker,check=True)",
        ], capture_output=True, text=True, timeout=20, env={**os.environ, **synthetic})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_launcher_rejects_runtime_overrides_and_shared_host(self):
        native.validate_native_command(['codex', '--no-daemon', 'exec', '--approve-for-me', '-'], 'codex')
        for args in (['codex', 'exec'], ['codex', '--', '--no-daemon'], ['bash', '-c', 'codex'],
                     ['codex', '--no-daemon', '-c', 'allow_login_shell=true'],
                     ['codex', '--no-daemon', '--config=features.shell_snapshot=true'],
                     ['codex', '--no-daemon', '--profile', 'other'],
                     ['codex', '--no-daemon', '--enable', 'shell_snapshot'],
                     ['codex', '--no-daemon', '--cd=/unrelated'],
                     ['codex', '--no-daemon', '--remote=unix:///unrelated']):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'NATIVE_COMMAND_INVALID'):
                native.validate_native_command(args, 'codex')

    def test_native_environment_rejects_unsafe_home_config_and_git_override(self):
        home = Path(self.state["private_root"]) / "codex-home"
        config = home / "config.toml"
        valid = ('project_root_markers = [".git"]\nallow_login_shell = false\n[features]\nshell_snapshot = false\n'
                 '[shell_environment_policy]\ninherit = "all"\nignore_default_excludes = false\n')
        config.write_text(valid)
        config.chmod(0o600)
        with mock.patch.dict(os.environ, {**self.env, "CODEX_HOME": str(home)}, clear=True):
            for bad in ("", valid.replace('allow_login_shell = false', 'allow_login_shell = true'),
                        valid.replace('shell_snapshot = false', 'shell_snapshot = true'),
                        valid.replace('project_root_markers = [".git"]\n', ''),
                        valid.replace('[".git"]', '[]'),
                        valid.replace('[".git"]', '[".hg"]'),
                        valid + 'set = { EXTRA = "synthetic" }\n'):
                config.write_text(bad)
                with self.subTest(config=bad), self.assertRaisesRegex(ValueError, "shell isolation"):
                    native.native_environment(self.project, self.profile)
            config.write_text(valid)
            config.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "shell isolation"):
                native.native_environment(self.project, self.profile)
            config.chmod(0o600)
            for bad in ('profile = "unreviewed"\n' + valid,
                        valid + '[profiles.unreviewed]\nallow_login_shell=true\n'):
                config.write_text(bad)
                with self.assertRaisesRegex(ValueError, "shell isolation"):
                    native.native_environment(self.project, self.profile)
            config.write_text(valid)
            override = self.project / '.codex/config.toml'
            override.parent.mkdir(exist_ok=True)
            override.write_text('allow_login_shell=true\n')
            with self.assertRaisesRegex(ValueError, "project configuration"):
                native.native_environment(self.project, self.profile)
            override.unlink()
            (home / '.zshenv').symlink_to(self.base / 'missing')
            with self.assertRaisesRegex(ValueError, "startup files"):
                native.native_environment(self.project, self.profile)
            (home / '.zshenv').unlink()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.base)}):
                with self.assertRaisesRegex(ValueError, "exact private"):
                    native.native_environment(self.project, self.profile)
            with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "0"}):
                with self.assertRaises(GitEnvironmentError):
                    native.native_environment(self.project, self.profile)

    def test_native_project_config_stops_at_each_owned_checkout_root(self):
        home = Path(self.state["private_root"]) / "codex-home"
        config = home / "config.toml"
        config.write_text('project_root_markers = [".git"]\nallow_login_shell = false\n'
                          '[features]\nshell_snapshot = false\n'
                          '[shell_environment_policy]\ninherit = "all"\nignore_default_excludes = false\n')
        config.chmod(0o600)
        ancestor = self.base / ".codex/config.toml"
        ancestor.parent.mkdir()
        ancestor.write_text('allow_login_shell = true\n')
        linked = Path(self.state["private_root"]) / "linked-checkout"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        with mock.patch.dict(os.environ, {**self.env, "CODEX_HOME": str(home)}, clear=True):
            for checkout in (self.project, linked):
                with self.subTest(checkout=checkout.name):
                    child = checkout / "nested"
                    child.mkdir()
                    self.assertEqual(native.native_environment(child, self.profile)["CODEX_HOME"], str(home))
                    for directory in (checkout, child):
                        override = directory / ".codex/config.toml"
                        override.parent.mkdir()
                        override.write_text('allow_login_shell = true\n')
                        with self.assertRaisesRegex(ValueError, "project configuration"):
                            native.native_environment(child, self.profile)
                        override.unlink()
                        override.symlink_to(self.base / "missing-config")
                        with self.assertRaisesRegex(ValueError, "project configuration"):
                            native.native_environment(child, self.profile)
                        override.unlink()

    def test_native_launcher_rejects_bare_origin_as_checkout(self):
        subprocess.run(["git", "init", "--initial-branch=main", str(self.base)],
                       env=self.env, capture_output=True, check=True, timeout=15)
        home = Path(self.state["private_root"]) / "codex-home"
        config = home / "config.toml"
        config.write_text('project_root_markers = [".git"]\nallow_login_shell = false\n'
                          '[features]\nshell_snapshot = false\n'
                          '[shell_environment_policy]\ninherit = "all"\nignore_default_excludes = false\n')
        config.chmod(0o600)
        with mock.patch.dict(os.environ, {**self.env, "CODEX_HOME": str(home)}, clear=True):
            with self.assertRaisesRegex(ValueError, "checkout root"):
                native.native_environment(Path(self.state["run_root"]) / "origin.git", self.profile)

    def test_native_shells_do_not_restore_ambient_startup_credentials(self):
        home = Path(self.state["private_root"]) / "codex-home"
        user_home = self.base / 'ordinary-home'
        user_home.mkdir()
        (user_home / '.zshenv').write_text('export GITHUB_TOKEN=synthetic-from-startup\n')
        bash_init = user_home / 'bash-init'
        bash_init.write_text('export GITHUB_TOKEN=synthetic-from-startup\n')
        with mock.patch.dict(os.environ, {**self.env, 'SKILLS_AGENT': 'claude',
                             'CLAUDE_CONFIG_DIR': str(home.with_name('claude-home')),
                             'HOME': str(user_home), 'BASH_ENV': str(bash_init)}, clear=True):
            home.with_name('claude-home').mkdir(mode=0o700)
            env = native.native_environment(self.project, self.profile)
        for shell in ('/bin/bash', '/bin/zsh'):
            if not Path(shell).is_file():
                continue
            result = subprocess.run([shell, '-c', 'test -z "${GITHUB_TOKEN+x}"'],
                                    env=env, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0)

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
