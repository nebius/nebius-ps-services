#!/usr/bin/env python3
"""Isolated installer, shared runtime and native packaging regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_support_bootstrap = Path(__file__).resolve().parents[1] / "assets/trusted_runtime_bootstrap.py.template"
exec(compile(_support_bootstrap.read_bytes(), str(_support_bootstrap), "exec"))
_load_skill_support("projector", __file__, 'global-context-management/scripts/test-agent-installation.py', source_only=True)  # noqa: F821 — verified bootstrap precedes runtime imports

from agent_runtime import agent_home, native_session_id, normalize_payload, state_edit_only  # noqa: E402 — verified bootstrap precedes runtime imports
from hook_runtime import OWNERS, bind_claude_bash, bound_claude_stop, payload_files, plugin_runtime, project_hook_entries  # noqa: E402 — verified bootstrap precedes runtime imports


ROOT = Path(__file__).resolve().parents[2]
PROMPT_ID = "550e8400-e29b-41d4-a716-446655440000"


class RuntimeTests(unittest.TestCase):
    def test_plugin_runtime_rejects_unsafe_paths_and_payloads(self):
        for agent in ("codex", "claude"):
            for unsafe in ("root", "ancestor", "symlink", "payload", "hardlink"):
                with self.subTest(agent=agent, unsafe=unsafe), tempfile.TemporaryDirectory() as temp:
                    base = Path(temp).resolve()
                    data = base / "data"
                    data.mkdir(mode=0o700)
                    env = {"PLUGIN_DATA": str(data), "CLAUDE_PLUGIN_DATA": str(data),
                           "CODEX_HOME": str(base / "codex"), "CLAUDE_CONFIG_DIR": str(base / "claude")}
                    with patch.dict(os.environ, env):
                        runtime = plugin_runtime(ROOT, agent)
                        if unsafe == "root":
                            runtime.chmod(0o777)
                        elif unsafe == "ancestor":
                            data.chmod(0o777)
                        elif unsafe == "symlink":
                            alias = base / "alias"
                            alias.symlink_to(data, target_is_directory=True)
                            os.environ["PLUGIN_DATA" if agent == "codex" else "CLAUDE_PLUGIN_DATA"] = str(alias)
                        elif unsafe == "payload":
                            (runtime / "commit_intent.py").chmod(0o666)
                        else:
                            os.link(runtime / "commit_intent.py", base / "linked.py")
                        with self.assertRaises(ValueError):
                            plugin_runtime(ROOT, agent)

    def test_continuation_counter_rejects_writable_file(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(Path(temp).resolve())}):
            payload = {"session_id": "session", "turn_id": PROMPT_ID}
            bound_claude_stop(payload, {"decision": "block"})
            counter = next(Path(temp).rglob("stop-count.json"))
            counter.chmod(0o666)
            before = counter.read_bytes()
            with self.assertRaises(ValueError):
                bound_claude_stop(payload, {"decision": "block"})
            self.assertEqual(before, counter.read_bytes())

    def test_standalone_runtime_rejects_unsafe_or_ambient_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            script = base / "skill/scripts/commit_transaction.py"
            script.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "commit/scripts/commit_transaction.py", script)
            marker = base / "executed"
            ambient = base / "agent_runtime.py"
            ambient.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
            for agent in ("codex", "claude"):
                home = base / agent
                hooks = home / "hooks"
                hooks.mkdir(parents=True)
                for name in ("trusted_runtime.py", "hook_runtime.py", "task_state_permissions.py"):
                    shutil.copyfile(ROOT / "global-context-management/scripts" / name, hooks / name)
                module = hooks / "agent_runtime.py"
                env = {**os.environ, "HOME": str(base), "CODEX_HOME": str(base / "codex"),
                       "CLAUDE_CONFIG_DIR": str(base / "claude"), "SKILLS_AGENT": agent, "PYTHONPATH": str(base)}
                env.pop("CODEX_THREAD_ID", None)
                for unsafe in ("missing", "symlink", "writable"):
                    with self.subTest(agent=agent, unsafe=unsafe):
                        if unsafe == "symlink":
                            module.symlink_to(ambient)
                        elif unsafe == "writable":
                            module.write_bytes(ambient.read_bytes())
                            module.chmod(0o666)
                        completed = subprocess.run([sys.executable, "-B", str(script), "--help"], cwd=base,
                                                   env=env, capture_output=True, text=True, timeout=15)
                        self.assertNotEqual(completed.returncode, 0)
                        self.assertTrue(any(message in completed.stderr for message in ("unsafe", "unavailable", "No such file", "incomplete")), completed.stderr)
                        self.assertFalse(marker.exists())
                        module.unlink(missing_ok=True)

    def test_worker_identity_composes_with_auth_rewrite_and_preserves_denial(self):
        payload = {"session_id": "parent", "tool_input": {"command": "original", "timeout": 1000}}
        rewritten = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": "printf auth"}}}
        identities = []
        for worker in ("one", "two"):
            result = bind_claude_bash({**payload, "agent_id": worker}, rewritten)
            data = result["hookSpecificOutput"]["updatedInput"]
            self.assertEqual(data["timeout"], 1000)
            self.assertTrue(data["command"].endswith("printf auth"))
            command = data["command"].removesuffix("printf auth") + 'printf "%s" "$SKILLS_SESSION_ID"'
            identities.append(subprocess.run(["bash", "-c", command], check=True, capture_output=True, text=True).stdout)
        self.assertNotEqual(*identities)
        denied = {"hookSpecificOutput": {"permissionDecision": "deny"}}
        self.assertEqual(bind_claude_bash(payload, denied), denied)

    def test_projection_does_not_adopt_a_custom_command_with_managed_basename(self):
        hooks = {"PreToolUse": [{"hooks": [{"type": "command", "command": "python3 /custom/commit_intent.py"}]}]}
        self.assertEqual(project_hook_entries(hooks, Path("/tmp/home"), "codex"), hooks)
        with self.assertRaises(ValueError):
            project_hook_entries(hooks, Path("/tmp/home"), "claude")

    def test_host_roots_and_native_identity_are_disjoint(self):
        with patch.dict(os.environ, {"CODEX_HOME": "/tmp/codex-home", "CLAUDE_CONFIG_DIR": "/tmp/claude-home",
                                     "CODEX_THREAD_ID": "codex-session", "SKILLS_SESSION_ID": "claude-session"}):
            for agent in ("codex", "claude"):
                with patch.dict(os.environ, {"SKILLS_AGENT": agent}):
                    if agent == "claude":
                        os.environ.pop("CODEX_THREAD_ID", None)
                    self.assertEqual(agent_home(), Path(f"/tmp/{agent}-home"))
                    self.assertEqual(native_session_id(), f"{agent}-session")

    def test_native_prompt_identity_and_invocation(self):
        payload = {"session_id": "session", "prompt_id": PROMPT_ID,
                   "hook_event_name": "UserPromptSubmit", "prompt": "/skills:commit"}
        self.assertEqual(normalize_payload(payload, "claude")["turn_id"], PROMPT_ID)
        self.assertEqual(normalize_payload(payload, "claude")["prompt"], "$commit")
        missing = normalize_payload({"turn_id": "untrusted"}, "claude")
        self.assertNotIn("turn_id", missing)
        self.assertEqual(normalize_payload({"prompt": "explain /skills:commit"}, "claude")["prompt"],
                         "explain /skills:commit")
        self.assertTrue(normalize_payload({**payload, "agent_id": "worker"}, "claude")["is_subagent"])

    def test_claude_state_write_exception_is_narrow(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "current.md"
            payload = {"_skills_host": "claude", "tool_name": "Write",
                       "tool_input": {"file_path": str(state), "content": "state"}}
            self.assertTrue(state_edit_only(payload, state))
            state.write_text("state")
            self.assertFalse(state_edit_only(payload, state))
            payload.update(tool_name="Edit", tool_input={"file_path": str(state), "old_string": "state", "new_string": "new"})
            self.assertTrue(state_edit_only(payload, state))
            payload["tool_input"]["replace_all"] = True
            self.assertFalse(state_edit_only(payload, state))

    def test_plugin_runtime_is_complete_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": temp, "CLAUDE_CONFIG_DIR": str(Path(temp) / "home")}):
            runtime = plugin_runtime(ROOT, "claude")
            self.assertEqual(set(payload_files(ROOT)), {str(p.relative_to(runtime)) for p in runtime.rglob("*") if p.is_file()})
            self.assertEqual(runtime, plugin_runtime(ROOT, "claude"))
            (runtime / "commit_intent.py").write_text("changed")
            with self.assertRaises(ValueError):
                plugin_runtime(ROOT, "claude")

    def test_plugin_rejects_existing_direct_local_hook_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            home.mkdir()
            (home / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{
                "type": "command", "command": 'python3 "${CODEX_HOME:-$HOME/.codex}/hooks/stop_lifecycle_arbiter.py"'
            }]}]}}))
            with patch.dict(os.environ, {"CODEX_HOME": str(home), "PLUGIN_DATA": temp}):
                with self.assertRaises(ValueError):
                    plugin_runtime(ROOT, "codex")
            self.assertFalse((Path(temp) / "hook-runtimes").exists())

    def test_concurrent_plugin_initialization_and_stop_limit(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": temp, "CLAUDE_CONFIG_DIR": str(Path(temp) / "home")}):
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: plugin_runtime(ROOT, "claude"), range(4)))
            self.assertEqual(len(set(paths)), 1)
            payload = {"session_id": "session", "turn_id": PROMPT_ID}
            for _ in range(7):
                self.assertEqual(bound_claude_stop(payload, {"decision": "block", "reason": "pending"})["decision"], "block")
            self.assertIs(bound_claude_stop(payload, {"decision": "block"})["continue"], False)
            self.assertEqual(bound_claude_stop({**payload, "turn_id": "new-native-prompt"}, {"decision": "block"})["decision"], "block")


class CombinedUpgradeTests(unittest.TestCase):
    def run_install(self, base, agent):
        env = {**os.environ, "HOME": str(base / "home"), "CODEX_HOME": str(base / "codex"),
               "CLAUDE_CONFIG_DIR": str(base / "claude"), "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        args = [] if agent == "codex" else ["--agent", "claude"]
        return subprocess.run(["bash", str(ROOT / "install-skills.sh"), *args], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=90)

    def seed_registration(self, base, agent):
        home = base / agent
        home.mkdir()
        hooks = {}
        for owner in OWNERS:
            source = json.loads((ROOT / owner / "assets/hooks.json.template").read_text())
            for event, entries in source["hooks"].items():
                for entry in entries:
                    for handler in entry["hooks"]:
                        script = Path(shlex.split(handler["command"])[-1]).name
                        handler["command"] = "python3 " + shlex.quote(str(home / "hooks" / script))
                    if entry not in hooks.setdefault(event, []):
                        hooks[event].append(entry)
        hooks["PreToolUse"][0]["matcher"] = "OldBash"
        hooks["PreToolUse"][0]["hooks"][0]["statusMessage"] = "Previous hook status"
        unrelated = {"hooks": [{"type": "command", "command": "printf fixture"}]}
        hooks["SessionStart"].append(unrelated)
        value = {"hooks": hooks, "fixturePreference": "preserve", "permissions": {"deny": ["Bash(rm *)"]}}
        settings = home / ("hooks.json" if agent == "codex" else "settings.json")
        settings.write_text(json.dumps(value))
        return home, settings, value, unrelated

    def test_default_upgrades_managed_hooks_and_reruns_without_changes(self):
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent), tempfile.TemporaryDirectory() as temp:
                base = Path(temp).resolve()
                home, settings, original, unrelated = self.seed_registration(base, agent)
                original_bytes = settings.read_bytes()
                first = self.run_install(base, agent)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                installed = json.loads(settings.read_text())
                self.assertEqual(installed["permissions"], original["permissions"])
                self.assertEqual(installed["fixturePreference"], "preserve")
                self.assertIn(unrelated, installed["hooks"]["SessionStart"])
                self.assertNotEqual(installed["hooks"]["PreToolUse"][0]["matcher"], "OldBash")
                for entries in installed["hooks"].values():
                    for entry in entries:
                        if entry != unrelated:
                            self.assertIn("hook_runtime.py", entry["hooks"][0]["command"])
                skills = base / "home/.agents/skills" if agent == "codex" else home / "skills"
                expected_skills = [p for p in ROOT.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
                self.assertEqual(len(list(skills.glob("*/SKILL.md"))), len(expected_skills))
                for skill in expected_skills:
                    self.assertEqual((skills / skill.name / "SKILL.md").read_bytes(), (skill / "SKILL.md").read_bytes())
                for relative, data in payload_files(ROOT).items():
                    self.assertEqual((home / "hooks" / relative).read_bytes(), data)
                self.assertTrue(any(p.read_bytes() == original_bytes for p in home.glob("*.bak.*")))
                snapshot = {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file()}
                second = self.run_install(base, agent)
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertEqual(snapshot, {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file()})

    def test_default_rejects_custom_registration_before_any_installation(self):
        for agent in ("codex", "claude"):
            for custom in ("argument", "foreign-path", "timeout", "mixed", "entry-option"):
                with self.subTest(agent=agent, custom=custom), tempfile.TemporaryDirectory() as temp:
                    base = Path(temp).resolve()
                    home, settings, value, _ = self.seed_registration(base, agent)
                    entry = value["hooks"]["PreToolUse"][0]
                    handler = entry["hooks"][0]
                    if custom == "argument":
                        handler["command"] += " --custom"
                    elif custom == "foreign-path":
                        handler["command"] = "python3 /custom/pre_tool_use_nebius_auth.py"
                    elif custom == "timeout":
                        handler["timeout"] = 99
                    elif custom == "mixed":
                        entry["hooks"].append({"type": "command", "command": "printf custom"})
                    else:
                        entry["customCondition"] = "preserve"
                    settings.write_text(json.dumps(value))
                    original = settings.read_bytes()
                    result = self.run_install(base, agent)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(settings.read_bytes(), original)
                    self.assertFalse((home / "hooks").exists())
                    self.assertFalse((home / "skills").exists())
                    self.assertFalse((base / "home/.agents/skills").exists())
                    self.assertEqual(list(home.glob("*.bak.*")), [])


class InstallationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-install-tests-")
        cls.base = Path(cls.temp.name).resolve()
        cls.env = {**os.environ, "HOME": str(cls.base / "home"), "CODEX_HOME": str(cls.base / "codex"),
                   "CLAUDE_CONFIG_DIR": str(cls.base / "claude"), "NO_COLOR": "1"}
        cls.env.pop("SKILLS_AGENT", None)
        for agent in ("codex", "claude"):
            home = cls.base / agent
            home.mkdir()
            settings = home / ("hooks.json" if agent == "codex" else "settings.json")
            value = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}]},
                     "permissions": {"deny": ["Bash(rm *)"]}, "customPreference": "preserve"}
            settings.write_text(json.dumps(value))
            args = [] if agent == "codex" else ["--agent", "claude"]
            first = cls.install(*args)
            if first.returncode:
                raise AssertionError(first.stdout + first.stderr)
            before = settings.read_bytes()
            second = cls.install(*args)
            if second.returncode or settings.read_bytes() != before:
                raise AssertionError("installation did not converge: " + second.stdout + second.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def install(cls, *args, env=None):
        return subprocess.run(["bash", str(ROOT / "install-skills.sh"), *args], cwd=ROOT,
                              env=env or cls.env, text=True, capture_output=True, timeout=90)

    def test_combined_defaults_include_all_skills_and_hooks(self):
        expected = {p.name for p in ROOT.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()}
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent):
                home = self.base / agent
                skills = (self.base / "home/.agents/skills") if agent == "codex" else home / "skills"
                self.assertEqual({p.name for p in skills.iterdir() if (p / "SKILL.md").is_file()}, expected)
                self.assertTrue((home / "hooks/agent_runtime.py").is_file())
                self.assertTrue((home / "hooks/lib/sdlc_state.py").is_file())
                value = json.loads((home / ("hooks.json" if agent == "codex" else "settings.json")).read_text())
                self.assertEqual(value["customPreference"], "preserve")
                self.assertEqual(len(value["hooks"]["Stop"]), 1)

    def test_operator_delegation_policy_survives_reinstall_on_both_hosts(self):
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent):
                base = self.base / f"operator-policy-{agent}"
                home = base / agent
                policy = home / "hooks/global_context_policy.json"
                policy.parent.mkdir(parents=True)
                content = b'{"auto_read_only_subagents":false,"customOption":"preserve"}\n'
                policy.write_bytes(content)
                policy.chmod(0o600)
                modified = policy.stat().st_mtime_ns
                env = {**self.env, "HOME": str(base / "home"), "CODEX_HOME": str(base / "codex"),
                       "CLAUDE_CONFIG_DIR": str(base / "claude")}
                args = [] if agent == "codex" else ["--agent", "claude"]
                for _ in range(2):
                    result = self.install(*args, env=env)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(policy.read_bytes(), content)
                    self.assertEqual(policy.stat().st_mtime_ns, modified)
                    self.assertEqual(policy.stat().st_mode & 0o777, 0o600)
                self.assertFalse(list(home.glob("**/*bak*")))

    def test_installed_config_claude_checker_runs_from_both_catalogs(self):
        # Prepare native-only fixture surfaces; hooks and registrations above
        # were produced by the real installer, not by the checker's projector.
        home = self.base / "claude"
        (home / "CLAUDE.md").write_bytes((ROOT / "config-claude/assets/CLAUDE.md.template").read_bytes())
        (home / "agents").mkdir(exist_ok=True)
        (home / "task-state").mkdir(mode=0o700, exist_ok=True)
        for name in ("repo-mapper", "test-strategist", "risk-reviewer"):
            (home / f"agents/{name}.md").write_bytes(
                (ROOT / f"config-claude/assets/agents/{name}.md.template").read_bytes())
        settings_before = (home / "settings.json").read_bytes()
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent):
                catalog = self.base / ("home/.agents/skills" if agent == "codex" else "claude/skills")
                script = catalog / "config-claude/scripts/check-local-idempotency.py"
                result = subprocess.run([sys.executable, "-B", str(script), "--help"],
                                        env=self.env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--claude-home", result.stdout)
                result = subprocess.run([sys.executable, "-B", str(script)],
                                        env=self.env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], "STATIC_PASS")
                self.assertEqual((home / "settings.json").read_bytes(), settings_before)

    def test_single_skill_scripts_resolve_installed_runtime(self):
        for agent in ("codex", "claude"):
            for skill, script in (("commit", "commit_transaction.py"), ("worktree", "worktree_manager.py")):
                with self.subTest(agent=agent, skill=skill):
                    base = self.base / f"single-{agent}-{skill}"
                    env = {**self.env, "SKILLS_AGENT": agent, "CODEX_HOME": str(base / "codex"),
                           "CLAUDE_CONFIG_DIR": str(base / "claude")}
                    env.pop("CODEX_THREAD_ID", None)
                    dest = base / "skills"
                    result = self.install("--agent", agent, str(ROOT / skill), str(dest), env=env)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    completed = subprocess.run([sys.executable, "-B", str(dest / skill / "scripts" / script), "--help"],
                                               cwd=base, env=env, capture_output=True, text=True, timeout=15)
                    self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_instruction_only_skill_needs_no_runtime_bundle(self):
        source = self.base / "external-instructions"
        source.mkdir()
        (source / "SKILL.md").write_text("---\nname: external-instructions\ndescription: Explain a fixture.\n---\nExplain it.\n")
        home = self.base / "instruction-home"
        result = self.install("--agent", "claude", str(source), str(home / "skills"),
                              env={**self.env, "CLAUDE_CONFIG_DIR": str(home)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((home / "hooks").exists())

    def test_missing_runtime_dependency_fails_before_installation(self):
        source = self.base / "incomplete-source/commit"
        shutil.copytree(ROOT / "commit", source)
        home = self.base / "incomplete-home"
        result = self.install("--agent", "claude", str(source), str(home / "skills"),
                              env={**self.env, "CLAUDE_CONFIG_DIR": str(home)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime dependencies", result.stderr + result.stdout)
        self.assertFalse(home.exists())

    def test_writable_runtime_home_fails_before_installation(self):
        home = self.base / "writable-home"
        home.mkdir()
        home.chmod(0o777)
        result = self.install("--agent", "claude", str(ROOT / "worktree"), str(home / "skills"),
                              env={**self.env, "CLAUDE_CONFIG_DIR": str(home)})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((home / "skills").exists())
        self.assertFalse((home / "hooks").exists())

    def test_installed_claude_commit_hook_uses_claude_identity_and_state(self):
        project = self.base / "project"
        project.mkdir(exist_ok=True)
        for args in (["init", "-b", "feature"], ["config", "user.name", "Fixture"],
                     ["config", "user.email", "fixture@example.com"], ["commit", "--allow-empty", "-m", "fixture"]):
            subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
        payload = {"hook_event_name": "UserPromptSubmit", "cwd": str(project),
                   "session_id": "claude-install-trial", "prompt_id": PROMPT_ID, "prompt": "/skills:commit"}
        result = subprocess.run([sys.executable, str(self.base / "claude/hooks/hook_runtime.py"),
                                 "--agent", "claude", "--hook", "commit_intent.py"],
                                input=json.dumps(payload), env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertNotEqual(json.loads(result.stdout).get("continue"), False, result.stdout)
        self.assertEqual(len(list((self.base / "claude/commit-transactions").rglob("authorization.json"))), 1)
        self.assertFalse((self.base / "codex/commit-transactions").exists())


    def test_every_claude_hook_entrypoint_runs_from_installed_payloads(self):
        hooks = {
            "session_start_context.py": "SessionStart", "user_prompt_context.py": "UserPromptSubmit",
            "project_specs_lifecycle.py": "SessionStart", "prompt_session_intake.py": "UserPromptSubmit",
            "pre_tool_use_sdlc_policy.py": "PreToolUse", "pre_tool_use_nebius_auth.py": "PreToolUse",
            "remediation_attempt_guard.py": "PreToolUse", "stop_lifecycle_arbiter.py": "Stop",
        }
        for hook, event in hooks.items():
            with self.subTest(hook=hook):
                payload = {"hook_event_name": event, "cwd": str(self.base), "session_id": "hook-smoke",
                           "prompt_id": PROMPT_ID, "prompt": "Explain the fixture", "source": "startup",
                           "tool_name": "Bash", "tool_input": {"command": "printf fixture"},
                           "last_assistant_message": "Fixture explanation complete."}
                completed = subprocess.run([sys.executable, str(self.base / "claude/hooks/hook_runtime.py"),
                                            "--agent", "claude", "--hook", hook], input=json.dumps(payload),
                                           env=self.env, capture_output=True, text=True, timeout=40)
                self.assertEqual(completed.returncode, 0)
                output = json.loads(completed.stdout)
                self.assertNotIn("runtime unavailable", json.dumps(output).lower())
                self.assertNotEqual(output.get("continue"), False, output)

    def test_invalid_claude_settings_fail_before_skill_or_hook_writes(self):
        home = self.base / "malformed"
        home.mkdir()
        (home / "settings.json").write_text("{")
        env = {**self.env, "CLAUDE_CONFIG_DIR": str(home)}
        result = self.install("--agent", "claude", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["settings.json"])

    def test_symlinked_hook_destinations_fail_before_mutation(self):
        for target in ("settings.json", "hooks/lib"):
            with self.subTest(target=target):
                home = self.base / ("symlink-" + target.replace("/", "-"))
                outside = self.base / ("outside-" + target.replace("/", "-"))
                home.mkdir()
                if target.endswith(".json"):
                    outside.write_text('{"hooks":{}}')
                else:
                    outside.mkdir()
                link = home / target
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside)
                result = self.install("--agent", "claude", env={**self.env, "CLAUDE_CONFIG_DIR": str(home)})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((home / "skills").exists())
                self.assertTrue(link.is_symlink())

    def test_selected_source_runtime_is_copied_without_import_execution(self):
        source = self.base / "custom-source"
        support = source / "global-context-management/scripts"
        support.mkdir(parents=True)
        marker = self.base / "untrusted-import"
        for name in ("agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py"):
            (support / name).write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n")
        hooks = source / "commit/assets/hooks"
        hooks.mkdir(parents=True)
        (hooks / "commit_intent.py").write_text("print('{}')\n")
        (hooks.parent / "hooks.json.template").write_bytes((ROOT / "commit/assets/hooks.json.template").read_bytes())
        result = self.install("--install-hooks", str(hooks), "--register-hooks",
                              env={**self.env, "CODEX_HOME": str(self.base / "untrusted-source-home")})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())

    def test_explicit_refresh_rebinds_only_managed_registration(self):
        home = self.base / "refresh"
        home.mkdir()
        source = json.loads((ROOT / "commit/assets/hooks.json.template").read_text())
        for entries in source["hooks"].values():
            for entry in entries:
                for handler in entry["hooks"]:
                    handler["command"] = handler["command"].removeprefix("SKILLS_AGENT=codex ")
        source["hooks"]["SessionStart"] = [{"hooks": [{"type": "command", "command": "true"}]}]
        settings = home / "hooks.json"
        settings.write_text(json.dumps(source))
        args = ("--install-hooks", "commit/assets/hooks", "--register-hooks")
        env = {**self.env, "CODEX_HOME": str(home)}
        self.assertNotEqual(self.install(*args, env=env).returncode, 0)
        result = self.install(*args, "--refresh-hook-registrations", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(settings.read_text())
        self.assertEqual(value["hooks"]["SessionStart"], source["hooks"]["SessionStart"])
        self.assertIn("hook_runtime.py", json.dumps(value["hooks"]["UserPromptSubmit"]))

    def test_claude_replace_preserves_non_hook_settings_and_backup(self):
        home = self.base / "replacement"
        home.mkdir()
        settings = home / "settings.json"
        before = b'{"theme":"dark","permissions":{"deny":["Bash(rm *)"]},"hooks":{}}'
        settings.write_bytes(before)
        result = self.install("--agent", "claude", "--install-hooks", "commit/assets/hooks",
                              "--register-hooks", "--replace-hooks-json",
                              env={**self.env, "CLAUDE_CONFIG_DIR": str(home)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(settings.read_text())["permissions"], {"deny": ["Bash(rm *)"]})
        self.assertEqual(json.loads(settings.read_text())["theme"], "dark")
        self.assertTrue(any(p.read_bytes() == before for p in home.glob("*.bak.*")))
        self.assertFalse((home / "skills").exists())


if __name__ == "__main__":
    unittest.main()
