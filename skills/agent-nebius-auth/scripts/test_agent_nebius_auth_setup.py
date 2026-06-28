#!/usr/bin/env python3
"""Fixture tests for the agent-nebius-auth setup script."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest


SCRIPT = Path(__file__).resolve().parent / "agent-nebius-auth.sh"
TENANT = "tenant-test"
PROJECT = "project-test"
PROJECT_NAME = "Project Test"
HUMAN_PROFILE = "human-admin"
AGENT_PROFILE = f"codex-agent-{PROJECT}"
SERVICE_ACCOUNT_ID = "serviceaccount-test"
GROUP_ID = "group-test"


FAKE_NEBIUS = r"""#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


state_path = Path(os.environ["FAKE_NEBIUS_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]


def save() -> None:
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def option_value(name: str) -> str:
    if name not in args:
        return ""
    index = args.index(name)
    if index + 1 >= len(args):
        return ""
    return args[index + 1]


def current_profile() -> str:
    profile = option_value("--profile") or option_value("-p")
    return profile or os.environ.get("NEBIUS_PROFILE") or state["active"]


def require_human() -> None:
    if current_profile().startswith("codex-agent-"):
        state["agent_iam_attempts"] += 1
        save()
        print("agent profile cannot manage IAM", file=sys.stderr)
        raise SystemExit(13)


def print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True))


if args[:2] == ["profile", "active"]:
    print(state["active"])
    raise SystemExit(0)

if args[:2] == ["profile", "current"]:
    print(os.environ.get("NEBIUS_PROFILE") or state["active"])
    raise SystemExit(0)

if args[:2] == ["profile", "activate"]:
    state["active"] = args[2]
    save()
    raise SystemExit(0)

if args[:2] == ["profile", "list"]:
    print_json({"profiles": [{"name": profile} for profile in state["profiles"]]})
    raise SystemExit(0)

if args[:2] in (["profile", "create"], ["profile", "update"]):
    profile = args[2]
    if profile not in state["profiles"]:
        state["profiles"].append(profile)
    state["active"] = profile
    if args[1] == "create":
        state["profile_create_calls"] += 1
    else:
        state["profile_update_calls"] += 1
        state["broken_profiles"] = [
            item for item in state["broken_profiles"] if item != profile
        ]
    save()
    sleep_seconds = float(os.environ.get("FAKE_NEBIUS_CREATE_SLEEP_SECONDS", "0"))
    if sleep_seconds:
        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            pass
    raise SystemExit(0)

if args[:2] == ["iam", "get-access-token"]:
    profile = current_profile()
    if profile in state["profiles"] and profile not in state["broken_profiles"]:
        print("fake-token")
        raise SystemExit(0)
    raise SystemExit(2)

if args[:3] == ["iam", "service-account", "get-by-name"]:
    print_json({"metadata": {"id": state["service_account_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "project", "get"]:
    project_id = option_value("--id") or (args[3] if len(args) > 3 else "")
    project_name = state["project_names"].get(project_id, project_id)
    if not project_id or project_id not in state["project_ids"]:
        raise SystemExit(4)
    state["project_get_calls"] += 1
    save()
    print_json({"metadata": {"id": project_id, "name": project_name}})
    raise SystemExit(0)

if args[:3] == ["iam", "project", "get-by-name"]:
    require_human()
    project_name = option_value("--name")
    for project_id, candidate_name in state["project_names"].items():
        if candidate_name == project_name:
            state["project_get_by_name_calls"] += 1
            save()
            print_json({"metadata": {"id": project_id, "name": candidate_name}})
            raise SystemExit(0)
    raise SystemExit(4)

if args[:3] == ["iam", "service-account", "create"]:
    require_human()
    print_json({"metadata": {"id": state["service_account_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "group", "get-by-name"]:
    require_human()
    state["group_get_by_name_calls"] += 1
    save()
    print_json({"metadata": {"id": state["group_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "group", "create"]:
    require_human()
    print_json({"metadata": {"id": state["group_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "access-permit", "list"]:
    require_human()
    print_json(
        {
            "items": [
                {"spec": {"resource_id": project_id, "role": "editor"}}
                for project_id in state["project_ids"]
            ]
        }
    )
    raise SystemExit(0)

if args[:3] == ["iam", "access-permit", "create"]:
    require_human()
    raise SystemExit(0)

if args[:3] == ["iam", "group-membership", "list-members"]:
    require_human()
    print_json(
        {
            "items": [
                {
                    "spec": {
                        "member_id": state["service_account_id"],
                    }
                }
            ]
        }
    )
    raise SystemExit(0)

if args[:3] == ["iam", "group-membership", "create"]:
    require_human()
    raise SystemExit(0)

if args[:3] == ["iam", "auth-public-key", "generate"]:
    require_human()
    output = option_value("--output")
    Path(output).write_text(
        json.dumps({"service_account_id": state["service_account_id"]}),
        encoding="utf-8",
    )
    raise SystemExit(0)

print(f"unhandled fake nebius command: {' '.join(args)}", file=sys.stderr)
raise SystemExit(64)
"""


class AgentNebiusAuthSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.codex_home = self.root / "codex"
        self.bin_dir = self.root / "bin"
        self.state_path = self.root / "state.json"
        self.home.mkdir()
        (self.home / ".nebius").mkdir()
        self.codex_home.mkdir()
        self.bin_dir.mkdir()

        fake_nebius = self.bin_dir / "nebius"
        fake_nebius.write_text(FAKE_NEBIUS, encoding="utf-8")
        fake_nebius.chmod(fake_nebius.stat().st_mode | stat.S_IXUSR)

        self.env = os.environ.copy()
        self.env.update(
            {
                "CODEX_HOME": str(self.codex_home),
                "FAKE_NEBIUS_STATE": str(self.state_path),
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env['PATH']}",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_state(
        self,
        *,
        active: str = HUMAN_PROFILE,
        profiles: list[str] | None = None,
        broken_profiles: list[str] | None = None,
    ) -> None:
        value = {
            "active": active,
            "agent_iam_attempts": 0,
            "broken_profiles": broken_profiles or [],
            "group_get_by_name_calls": 0,
            "profile_create_calls": 0,
            "profile_update_calls": 0,
            "project_get_calls": 0,
            "project_get_by_name_calls": 0,
            "profiles": profiles or [HUMAN_PROFILE],
            "project_ids": [PROJECT],
            "project_names": {PROJECT: PROJECT_NAME},
            "service_account_id": SERVICE_ACCOUNT_ID,
            "group_id": GROUP_ID,
        }
        self.state_path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_credential(self, project: str = PROJECT) -> None:
        credential = self.home / ".nebius" / f"codex-agent-authkey.{project}.json"
        credential.write_text(
            json.dumps({"service_account_id": SERVICE_ACCOUNT_ID}),
            encoding="utf-8",
        )

    def default_project_file(self) -> Path:
        return self.home / ".nebius" / "codex-agent-default-project-id"

    def setup_command(
        self,
        project: str | None = PROJECT,
        *,
        project_name: str | None = None,
    ) -> list[str]:
        command = [
            "bash",
            str(SCRIPT),
            "ensure",
            "--tenant-id",
            TENANT,
        ]
        if project is not None:
            command.extend(["--project-id", project])
        if project_name is not None:
            command.extend(["--project-name", project_name])
        return command

    def run_setup(
        self,
        project: str | None = PROJECT,
        *,
        project_name: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.setup_command(project, project_name=project_name),
            cwd=str(SCRIPT.parent.parent),
            env=env or self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def start_setup(
        self,
        project: str | None = PROJECT,
        *,
        project_name: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            self.setup_command(project, project_name=project_name),
            cwd=str(SCRIPT.parent.parent),
            env=env or self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_setup_succeeds(
        self,
        project: str | None = PROJECT,
        *,
        project_name: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = self.run_setup(project, project_name=project_name, env=env)
        self.assertEqual(
            result.returncode,
            0,
            textwrap.dedent(
                f"""\
                stdout:
                {result.stdout}
                stderr:
                {result.stderr}
                """
            ),
        )
        return result

    def test_profile_create_preserves_active_human_profile_across_reruns(self) -> None:
        self.write_state()
        self.write_credential()

        self.assert_setup_succeeds()
        after_first = self.read_state()

        self.assertEqual(after_first["active"], HUMAN_PROFILE)
        self.assertIn(AGENT_PROFILE, after_first["profiles"])
        self.assertEqual(after_first["profile_create_calls"], 1)
        self.assertEqual(after_first["agent_iam_attempts"], 0)

        self.assert_setup_succeeds()
        after_second = self.read_state()

        self.assertEqual(after_second["active"], HUMAN_PROFILE)
        self.assertEqual(after_second["profile_create_calls"], 1)
        self.assertEqual(after_second["profile_update_calls"], 0)
        self.assertEqual(after_second["agent_iam_attempts"], 0)
        self.assertEqual(after_second["project_get_calls"], 2)

        self.assertFalse((self.codex_home / "config.toml").exists())
        self.assertEqual(
            self.default_project_file().read_text(encoding="utf-8").strip(),
            PROJECT,
        )

    def test_legacy_install_hook_flag_fails_fast(self) -> None:
        self.write_state()
        self.write_credential()

        result = subprocess.run(
            [
                *self.setup_command(),
                "--install-hook",
            ],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--install-hook is no longer supported", result.stderr)
        self.assertIn("install-skills.sh --install-hooks", result.stderr)
        self.assertFalse((self.codex_home / "config.toml").exists())
        self.assertFalse(self.default_project_file().exists())

    def test_project_name_selector_resolves_project_id(self) -> None:
        self.write_state()
        self.write_credential()

        self.assert_setup_succeeds(project=None, project_name=PROJECT_NAME)
        state = self.read_state()

        self.assertEqual(state["project_get_by_name_calls"], 1)
        self.assertIn(AGENT_PROFILE, state["profiles"])
        self.assertEqual(
            self.default_project_file().read_text(encoding="utf-8").strip(),
            PROJECT,
        )

    def test_project_id_and_project_name_together_fail_fast(self) -> None:
        self.write_state()
        self.write_credential()

        result = self.run_setup(PROJECT, project_name=PROJECT_NAME)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Pass only one of --project-id or --project-name", result.stderr)
        self.assertFalse(self.default_project_file().exists())

    def test_profile_update_preserves_active_human_profile(self) -> None:
        self.write_state(
            profiles=[HUMAN_PROFILE, AGENT_PROFILE],
            broken_profiles=[AGENT_PROFILE],
        )
        self.write_credential()

        self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(state["active"], HUMAN_PROFILE)
        self.assertEqual(state["profile_create_calls"], 0)
        self.assertEqual(state["profile_update_calls"], 1)
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_effective_human_profile_may_come_from_nebius_profile_env(self) -> None:
        self.write_state(
            active=AGENT_PROFILE,
            profiles=[HUMAN_PROFILE, AGENT_PROFILE],
        )
        self.write_credential()
        env = self.env.copy()
        env["NEBIUS_PROFILE"] = HUMAN_PROFILE

        self.assert_setup_succeeds(env=env)
        state = self.read_state()

        self.assertEqual(state["active"], AGENT_PROFILE)
        self.assertEqual(state["group_get_by_name_calls"], 1)
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_nebius_profile_env_restores_when_no_profile_is_active(self) -> None:
        self.write_state(
            active="",
            profiles=[HUMAN_PROFILE],
        )
        self.write_credential()
        env = self.env.copy()
        env["NEBIUS_PROFILE"] = HUMAN_PROFILE

        self.assert_setup_succeeds(env=env)
        state = self.read_state()

        self.assertEqual(state["active"], HUMAN_PROFILE)
        self.assertIn(AGENT_PROFILE, state["profiles"])
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_active_agent_profile_is_not_treated_as_human_session(self) -> None:
        self.write_state(
            active=AGENT_PROFILE,
            profiles=[HUMAN_PROFILE, AGENT_PROFILE],
        )
        self.write_credential()

        self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(state["active"], AGENT_PROFILE)
        self.assertEqual(state["group_get_by_name_calls"], 0)
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_concurrent_setup_serializes_active_profile_mutation(self) -> None:
        project_one = "project-one"
        project_two = "project-two"
        profile_one = f"codex-agent-{project_one}"
        profile_two = f"codex-agent-{project_two}"
        self.write_state()
        state = self.read_state()
        state["project_ids"] = [project_one, project_two]
        state["project_names"] = {project_one: "Project One", project_two: "Project Two"}
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        self.write_credential(project_one)
        self.write_credential(project_two)
        env = self.env.copy()
        env["FAKE_NEBIUS_CREATE_SLEEP_SECONDS"] = "0.2"

        first = self.start_setup(project_one, env=env)
        time.sleep(0.05)
        second = self.start_setup(project_two, env=env)
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)

        self.assertEqual(
            first.returncode,
            0,
            f"stdout:\n{first_stdout}\nstderr:\n{first_stderr}",
        )
        self.assertEqual(
            second.returncode,
            0,
            f"stdout:\n{second_stdout}\nstderr:\n{second_stderr}",
        )
        state = self.read_state()

        self.assertEqual(state["active"], HUMAN_PROFILE)
        self.assertIn(profile_one, state["profiles"])
        self.assertIn(profile_two, state["profiles"])
        self.assertEqual(state["profile_create_calls"], 2)
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_interrupted_profile_create_restores_active_profile(self) -> None:
        self.write_state()
        self.write_credential()
        env = self.env.copy()
        env["FAKE_NEBIUS_CREATE_SLEEP_SECONDS"] = "5"

        process = self.start_setup(env=env)
        time.sleep(0.5)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)

        self.assertEqual(
            process.returncode,
            130,
            f"stdout:\n{stdout}\nstderr:\n{stderr}",
        )
        state = self.read_state()

        self.assertEqual(state["active"], HUMAN_PROFILE)
        self.assertIn(AGENT_PROFILE, state["profiles"])
        self.assertEqual(state["agent_iam_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
