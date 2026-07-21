#!/usr/bin/env python3
"""Fixture tests for the agent-nebius-auth setup script."""

from __future__ import annotations

import hashlib
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


SCRIPT = Path(__file__).resolve().parent / "agent-nebius-auth-setup.sh"
TENANT = "tenant-test"
PROJECT = "project-test"
PROJECT_NAME = "Project Test"
HUMAN_PROFILE = "human-admin"
AGENT_PROFILE = f"codex-agent-{PROJECT}"
SERVICE_ACCOUNT_ID = "serviceaccount-test"
GROUP_ID = "group-test"
GROUP_NAME = (
    f"codex-agent-project-test-{hashlib.sha256(PROJECT.encode()).hexdigest()[:20]}"
)


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


if args[:1] == ["iam"] and any(
    os.environ.get(name)
    for name in (
        "TOKEN",
        "NEBIUS_IAM_TOKEN",
        "NEBIUS_AUTH_CREDENTIALS_FILE",
        "NEBIUS_PROJECT_ID",
    )
):
    state["inherited_auth_attempts"] += 1
    save()
    print("inherited auth environment reached Nebius CLI", file=sys.stderr)
    raise SystemExit(66)


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
    if state.get("profile_list_error"):
        print("profile configuration not found", file=sys.stderr)
        raise SystemExit(2)
    if state.get("profile_list_malformed"):
        print("{unsupported-profile-output")
        raise SystemExit(0)
    for profile in state["profiles"]:
        suffix = " [active]" if profile == state["active"] else ""
        print(f"{profile}{suffix}")
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
    credential_blocks_agent = state["credential_broken"] and profile.startswith(
        "codex-agent-"
    )
    if (
        profile in state["profiles"]
        and profile not in state["broken_profiles"]
        and not credential_blocks_agent
    ):
        print("fake-token")
        raise SystemExit(0)
    raise SystemExit(2)

if args[:3] == ["iam", "service-account", "get-by-name"]:
    if state.get("service_account_lookup_error"):
        print(
            state.get("service_account_lookup_error_message", "temporary lookup failure"),
            file=sys.stderr,
        )
        raise SystemExit(2)
    if current_profile().startswith("codex-agent-") and state[
        "project_access_broken"
    ]:
        raise SystemExit(13)
    if not state["service_account_exists"]:
        print("not found", file=sys.stderr)
        raise SystemExit(13)
    print_json({"metadata": {"id": state["service_account_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "service-account", "get"]:
    require_human()
    service_account_id = option_value("--id")
    record = state["service_account_records"][service_account_id]
    print_json(
        {
            "metadata": {
                "id": service_account_id,
                "name": record["name"],
                "parent_id": record["parent_id"],
            }
        }
    )
    raise SystemExit(0)

if args[:3] == ["iam", "project", "get"]:
    project_id = option_value("--id") or (args[3] if len(args) > 3 else "")
    project_name = state["project_names"].get(project_id, project_id)
    if not project_id or project_id not in state["project_ids"]:
        raise SystemExit(4)
    state["project_get_calls"] += 1
    save()
    print_json(
        {
            "metadata": {
                "id": state["project_response_ids"].get(project_id, project_id),
                "name": project_name,
                "parent_id": state["project_parent_ids"][project_id],
            }
        }
    )
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
    state["service_account_exists"] = True
    state["service_account_create_calls"] += 1
    save()
    print_json({"metadata": {"id": state["service_account_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "group", "get-by-name"]:
    require_human()
    if state.get("group_lookup_error"):
        print("temporary group lookup failure", file=sys.stderr)
        raise SystemExit(2)
    state["group_get_by_name_calls"] += 1
    parent_id = option_value("--parent-id")
    group_name = option_value("--name")
    state["group_get_parent_ids"].append(parent_id)
    save()
    group_id = state["group_records"].get(f"{parent_id}:{group_name}")
    if not group_id:
        raise SystemExit(13)
    print_json({"metadata": {"id": group_id}})
    raise SystemExit(0)

if args[:3] == ["iam", "group", "create"]:
    require_human()
    parent_id = option_value("--parent-id")
    group_name = option_value("--name")
    state["group_create_parent_ids"].append(parent_id)
    state["group_records"][f"{parent_id}:{group_name}"] = state["group_id"]
    save()
    print_json({"metadata": {"id": state["group_id"]}})
    raise SystemExit(0)

if args[:3] == ["iam", "access-permit", "list"]:
    require_human()
    if state.get("access_permit_lookup_error"):
        print("temporary permit lookup failure", file=sys.stderr)
        raise SystemExit(2)
    print_json(
        {
            "items": [
                {"spec": {"resource_id": project_id, "role": role}}
                for project_id in state["project_ids"]
                for role in state["access_permit_roles"]
            ]
        }
    )
    raise SystemExit(0)

if args[:3] == ["iam", "access-permit", "create"]:
    require_human()
    role = option_value("--role")
    state["access_permit_create_parent_ids"].append(option_value("--parent-id"))
    state["access_permit_create_resource_ids"].append(option_value("--resource-id"))
    state["access_permit_create_roles"].append(role)
    if role not in state["access_permit_roles"]:
        state["access_permit_roles"].append(role)
    save()
    raise SystemExit(0)

if args[:3] == ["iam", "group-membership", "list-members"]:
    require_human()
    if state.get("membership_lookup_error"):
        print("temporary membership lookup failure", file=sys.stderr)
        raise SystemExit(2)
    state["membership_list_calls"] += 1
    save()
    page_token = option_value("--page-token")
    if state.get("membership_on_second_page") and not page_token:
        print_json({"memberships": [], "next_page_token": "page-2"})
        raise SystemExit(0)
    if state.get("membership_on_second_page") and page_token != "page-2":
        print("unexpected page token", file=sys.stderr)
        raise SystemExit(2)
    memberships = (
        [{"spec": {"member_id": state["service_account_id"]}}]
        if state.get("membership_exists", True)
        else []
    )
    print_json({"memberships": memberships})
    raise SystemExit(0)

if args[:3] == ["iam", "group-membership", "create"]:
    require_human()
    state["membership_create_calls"] += 1
    state["membership_exists"] = True
    save()
    if state.get("membership_create_conflict_converges"):
        print("AlreadyExists", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)

if args[:3] == ["iam", "auth-public-key", "generate"]:
    require_human()
    state["auth_public_key_generate_calls"] += 1
    if state.get("generated_credentials_broken_count", 0) > 0:
        state["credential_broken"] = True
        state["generated_credentials_broken_count"] -= 1
    else:
        state["credential_broken"] = False
    save()
    output = option_value("--output")
    generated_subject = (
        "serviceaccount-wrong"
        if state.get("generated_credential_invalid")
        else state["service_account_id"]
    )
    Path(output).write_text(
        json.dumps(
            {
                "subject-credentials": {
                    "type": "JWT",
                    "alg": "RS256",
                    "kid": f"publickey-{state['auth_public_key_generate_calls']}",
                    "iss": state["service_account_id"],
                    "sub": generated_subject,
                }
            }
        ),
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
        access_permit_roles: list[str] | None = None,
        profiles: list[str] | None = None,
        broken_profiles: list[str] | None = None,
        service_account_exists: bool = True,
    ) -> None:
        value = {
            "active": active,
            "access_permit_create_roles": [],
            "access_permit_create_parent_ids": [],
            "access_permit_create_resource_ids": [],
            "access_permit_roles": (
                ["admin"] if access_permit_roles is None else access_permit_roles
            ),
            "agent_iam_attempts": 0,
            "auth_public_key_generate_calls": 0,
            "broken_profiles": broken_profiles or [],
            "credential_broken": False,
            "generated_credentials_broken_count": 0,
            "generated_credential_invalid": False,
            "service_account_lookup_error": False,
            "group_lookup_error": False,
            "access_permit_lookup_error": False,
            "membership_lookup_error": False,
            "membership_create_calls": 0,
            "membership_create_conflict_converges": False,
            "membership_exists": True,
            "membership_list_calls": 0,
            "membership_on_second_page": False,
            "profile_list_error": False,
            "profile_list_malformed": False,
            "group_get_by_name_calls": 0,
            "group_get_parent_ids": [],
            "group_create_parent_ids": [],
            "group_records": {f"{PROJECT}:{GROUP_NAME}": GROUP_ID},
            "inherited_auth_attempts": 0,
            "profile_create_calls": 0,
            "profile_update_calls": 0,
            "project_access_broken": False,
            "project_get_calls": 0,
            "project_get_by_name_calls": 0,
            "profiles": profiles or [HUMAN_PROFILE],
            "project_ids": [PROJECT],
            "project_names": {PROJECT: PROJECT_NAME},
            "project_parent_ids": {PROJECT: TENANT},
            "project_response_ids": {},
            "service_account_id": SERVICE_ACCOUNT_ID,
            "service_account_create_calls": 0,
            "service_account_exists": service_account_exists,
            "service_account_name": "codex-agent-sa",
            "service_account_parent_id": PROJECT,
            "service_account_records": {
                SERVICE_ACCOUNT_ID: {
                    "name": "codex-agent-sa",
                    "parent_id": PROJECT,
                }
            },
            "group_id": GROUP_ID,
        }
        self.state_path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_credential(self, project: str = PROJECT) -> None:
        (self.home / ".nebius").mkdir(exist_ok=True)
        (self.home / ".nebius").chmod(0o700)
        service_account_id = (
            SERVICE_ACCOUNT_ID if project == PROJECT else f"serviceaccount-{project}"
        )
        state = self.read_state()
        state["service_account_records"][service_account_id] = {
            "name": "codex-agent-sa",
            "parent_id": project,
        }
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        credential = self.home / ".nebius" / f"codex-agent-authkey.{project}.json"
        credential.write_text(
            json.dumps(
                {
                    "subject-credentials": {
                        "type": "JWT",
                        "alg": "RS256",
                        "iss": service_account_id,
                        "sub": service_account_id,
                    }
                }
            ),
            encoding="utf-8",
        )

    def setup_command(
        self,
        project: str | None = PROJECT,
        *,
        tenant: str | None = TENANT,
        confirm_digest: str | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> list[str]:
        command = [
            "bash",
            str(SCRIPT),
            "ensure",
        ]
        if project is not None:
            command.extend(["--project-id", project])
        if tenant is not None:
            command.extend(["--tenant-id", tenant])
        command.extend(extra_args)
        if confirm_digest is not None:
            command.extend(["--confirm", confirm_digest])
        return command

    def plan_digest(self, result: subprocess.CompletedProcess[str]) -> str:
        prefix = "Plan digest: "
        return next(
            line.split(prefix, 1)[1]
            for line in result.stderr.splitlines()
            if prefix in line
        )

    def run_setup(
        self,
        project: str | None = PROJECT,
        *,
        tenant: str | None = TENANT,
        confirm: bool = True,
        env: dict[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        selected_env = env or self.env
        confirm_digest: str | None = None
        if confirm:
            dry_run = self.run_dry_run(
                project or PROJECT,
                tenant=tenant,
                env=selected_env,
                extra_args=extra_args,
            )
            if dry_run.returncode != 0:
                return dry_run
            confirm_digest = self.plan_digest(dry_run)
        return subprocess.run(
            self.setup_command(
                project,
                tenant=tenant,
                confirm_digest=confirm_digest,
                extra_args=extra_args,
            ),
            cwd=str(SCRIPT.parent.parent),
            env=selected_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_dry_run(
        self,
        project: str = PROJECT,
        *,
        tenant: str | None = TENANT,
        env: dict[str, str] | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                *self.setup_command(project, tenant=tenant, extra_args=extra_args),
                "--dry-run",
            ],
            cwd=str(SCRIPT.parent.parent),
            env=env or self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def replacement_command(
        self, *, confirm_digest: str | None = None
    ) -> list[str]:
        command = [
            "bash",
            str(SCRIPT),
            "replace-credential",
            "--project-id",
            PROJECT,
            "--tenant-id",
            TENANT,
        ]
        if confirm_digest is not None:
            command.extend(["--confirm", confirm_digest])
        return command

    def run_replacement(
        self, *, confirm: bool = True
    ) -> subprocess.CompletedProcess[str]:
        dry_run = subprocess.run(
            [*self.replacement_command(), "--dry-run"],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        if not confirm or dry_run.returncode != 0:
            return dry_run
        return subprocess.run(
            self.replacement_command(confirm_digest=self.plan_digest(dry_run)),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def repair_lease_command(
        self,
        *,
        confirm_digest: str | None = None,
        ttl_seconds: int = 43200,
    ) -> list[str]:
        command = [
            "bash",
            str(SCRIPT),
            "repair-lease",
            "--project-id",
            PROJECT,
            "--tenant-id",
            TENANT,
            "--ttl-seconds",
            str(ttl_seconds),
        ]
        if confirm_digest is not None:
            command.extend(["--confirm", confirm_digest])
        return command

    def run_repair_lease(
        self, *, ttl_seconds: int = 43200, confirm: bool = True
    ) -> subprocess.CompletedProcess[str]:
        dry_run = subprocess.run(
            [*self.repair_lease_command(ttl_seconds=ttl_seconds), "--dry-run"],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        if not confirm or dry_run.returncode != 0:
            return dry_run
        return subprocess.run(
            self.repair_lease_command(
                confirm_digest=self.plan_digest(dry_run),
                ttl_seconds=ttl_seconds,
            ),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def issued_lease_path(
        self, result: subprocess.CompletedProcess[str]
    ) -> Path:
        prefix = "Repair lease created: "
        return Path(
            next(
                line.split(prefix, 1)[1]
                for line in result.stderr.splitlines()
                if prefix in line
            )
        )

    def run_local_repair(self, lease_file: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "repair-local",
                "--lease-file",
                str(lease_file),
            ],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_verify(self, project: str = PROJECT) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "verify",
                "--project-id",
                project,
            ],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def start_setup(
        self,
        project: str | None = PROJECT,
        *,
        tenant: str | None = TENANT,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        selected_env = env or self.env
        dry_run = self.run_dry_run(project or PROJECT, tenant=tenant, env=selected_env)
        if dry_run.returncode != 0:
            raise AssertionError(dry_run.stderr)
        return subprocess.Popen(
            self.setup_command(
                project,
                tenant=tenant,
                confirm_digest=self.plan_digest(dry_run),
            ),
            cwd=str(SCRIPT.parent.parent),
            env=selected_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_setup_succeeds(
        self,
        project: str | None = PROJECT,
        *,
        tenant: str | None = TENANT,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = self.run_setup(project, tenant=tenant, env=env)
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
        self.assertEqual(after_second["project_get_calls"], 4)

        self.assertFalse((self.codex_home / "config.toml").exists())
        self.assertFalse(
            (self.home / ".nebius" / "codex-agent-default-project-id").exists()
        )

    def test_verify_uses_only_agent_runtime_and_makes_no_changes(self) -> None:
        self.write_state(
            active=AGENT_PROFILE,
            profiles=[AGENT_PROFILE],
        )
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        before = self.read_state()

        result = self.run_verify()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Agent runtime auth is ready", result.stderr)
        self.assertIn("Human/admin IAM reconciliation was not checked", result.stderr)
        after = self.read_state()
        self.assertEqual(after["active"], AGENT_PROFILE)
        self.assertEqual(after["profile_create_calls"], 0)
        self.assertEqual(after["profile_update_calls"], 0)
        self.assertEqual(after["service_account_create_calls"], 0)
        self.assertEqual(after["access_permit_create_roles"], [])
        self.assertEqual(after["agent_iam_attempts"], before["agent_iam_attempts"])

    def test_verify_fails_closed_without_repairing_local_state(self) -> None:
        self.write_state(profiles=[AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o644)

        result = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must have mode 0600", result.stderr)
        self.assertEqual(credential.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.read_state()["profile_update_calls"], 0)

    def test_verify_distinguishes_token_and_project_access_failures(self) -> None:
        for broken_profile, broken_access, expected in (
            (True, False, "cannot mint a token non-interactively"),
            (False, True, "lacks basic project access"),
        ):
            with self.subTest(expected=expected):
                self.write_state(
                    profiles=[AGENT_PROFILE],
                    broken_profiles=[AGENT_PROFILE] if broken_profile else [],
                )
                state = self.read_state()
                state["project_access_broken"] = broken_access
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )
                self.write_credential()
                credential = (
                    self.home
                    / ".nebius"
                    / f"codex-agent-authkey.{PROJECT}.json"
                )
                credential.chmod(0o600)

                result = self.run_verify()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_default_role_adds_admin_permit_to_editor_only_group(self) -> None:
        self.write_state(access_permit_roles=["editor"])
        self.write_credential()

        self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(state["access_permit_create_roles"], ["admin"])
        self.assertEqual(state["access_permit_roles"], ["editor", "admin"])

        self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(state["access_permit_create_roles"], ["admin"])
        self.assertEqual(state["access_permit_roles"], ["editor", "admin"])

    def test_explicit_role_override_remains_available(self) -> None:
        self.write_state(access_permit_roles=[])
        self.write_credential()

        result = self.run_setup(extra_args=("--role", "editor"))
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.read_state()

        self.assertEqual(state["access_permit_create_roles"], ["editor"])
        self.assertEqual(state["access_permit_roles"], ["editor"])

    def test_help_reports_admin_as_default_role(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("project role 'admin'", result.stderr)

    def test_unsupported_install_hook_flag_fails_fast(self) -> None:
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
        self.assertFalse(
            (self.home / ".nebius" / "codex-agent-default-project-id").exists()
        )

    def test_project_metadata_derives_tenant_and_name(self) -> None:
        self.write_state()
        self.write_credential()

        result = self.assert_setup_succeeds(tenant=None)
        state = self.read_state()

        self.assertEqual(state["project_get_calls"], 2)
        self.assertIn(AGENT_PROFILE, state["profiles"])
        self.assertIn(f"tenant ID: {TENANT}", result.stderr)
        self.assertIn(f"project name: {PROJECT_NAME}", result.stderr)

    def test_project_name_selector_is_rejected(self) -> None:
        self.write_state()
        self.write_credential()

        result = subprocess.run(
            [*self.setup_command(), "--project-name", PROJECT_NAME],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown argument: --project-name", result.stderr)

    def test_tenant_mismatch_fails_before_any_mutation(self) -> None:
        self.write_state()
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o644)

        result = self.run_setup(tenant="tenant-wrong")
        state = self.read_state()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not asserted tenant 'tenant-wrong'", result.stderr)
        self.assertEqual(state["profile_create_calls"], 0)
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o644)

    def test_unknown_project_fails_before_any_mutation(self) -> None:
        self.write_state()

        result = self.run_setup(project="project-missing", tenant=None)
        state = self.read_state()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to resolve authoritative metadata", result.stderr)
        self.assertEqual(state["profile_create_calls"], 0)
        self.assertEqual(state["access_permit_create_roles"], [])
        self.assertFalse((self.home / ".nebius").exists())

    def test_incomplete_or_mismatched_project_metadata_fails_before_mutation(self) -> None:
        cases = (
            ("name", "", "missing metadata.name"),
            ("parent", "", "missing metadata.parent_id"),
            ("id", "project-other", "does not match requested project"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                self.write_state()
                state = self.read_state()
                if field == "name":
                    state["project_names"][PROJECT] = value
                elif field == "parent":
                    state["project_parent_ids"][PROJECT] = value
                else:
                    state["project_response_ids"][PROJECT] = value
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )

                result = self.run_setup()
                state = self.read_state()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertEqual(state["service_account_create_calls"], 0)
                self.assertEqual(state["auth_public_key_generate_calls"], 0)
                self.assertEqual(state["profile_create_calls"], 0)
                self.assertFalse((self.home / ".nebius").exists())

    def test_dry_run_uses_real_metadata_and_performs_no_mutation(self) -> None:
        self.write_state(access_permit_roles=[], service_account_exists=False)
        result = self.run_dry_run(tenant=None)
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["project_get_calls"], 1)
        self.assertEqual(state["profile_create_calls"], 0)
        self.assertEqual(state["access_permit_create_roles"], [])
        self.assertFalse((self.home / ".nebius").exists())
        self.assertNotIn("dry-run-project", result.stderr)
        self.assertIn(f"project name: {PROJECT_NAME}", result.stderr)
        self.assertIn("No filesystem, profile, credential, or IAM mutations", result.stderr)
        self.assertIn("create service account 'codex-agent-sa'", result.stderr)
        self.assertIn("generate credential", result.stderr)
        self.assertIn("add role 'admin'", result.stderr)
        self.assertIn("create owned local Nebius directory", result.stderr)

    def test_group_and_access_permit_are_project_scoped(self) -> None:
        self.write_state(
            access_permit_roles=[],
            profiles=[HUMAN_PROFILE, AGENT_PROFILE],
        )
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["group_records"] = {}
        state["membership_exists"] = False
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["group_create_parent_ids"], [PROJECT])
        self.assertTrue(state["group_get_parent_ids"])
        self.assertEqual(set(state["group_get_parent_ids"]), {PROJECT})
        self.assertEqual(state["access_permit_create_parent_ids"], [GROUP_ID])
        self.assertEqual(state["access_permit_create_resource_ids"], [PROJECT])
        self.assertNotIn(TENANT, state["group_create_parent_ids"])
        self.assertNotIn(TENANT, state["access_permit_create_resource_ids"])

    def test_tenant_scoped_group_is_not_discovered_or_reused(self) -> None:
        self.write_state(access_permit_roles=[])
        state = self.read_state()
        state["group_records"] = {f"{TENANT}:{GROUP_NAME}": "group-tenant-legacy"}
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_dry_run()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"create group '{GROUP_NAME}' under project '{PROJECT}'",
            result.stderr,
        )
        self.assertEqual(set(state["group_get_parent_ids"]), {PROJECT})
        self.assertNotIn("group-tenant-legacy", result.stderr)

    def test_read_failures_are_not_treated_as_absent_resources(self) -> None:
        cases = (
            ("service_account_lookup_error", False, "Service-account lookup failed"),
            ("group_lookup_error", True, "Group lookup failed"),
            ("access_permit_lookup_error", True, "Access-permit lookup failed"),
            ("membership_lookup_error", True, "Group-membership lookup failed"),
        )
        for field, with_credential, expected in cases:
            with self.subTest(field=field):
                self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
                if with_credential:
                    self.write_credential()
                    credential = (
                        self.home
                        / ".nebius"
                        / f"codex-agent-authkey.{PROJECT}.json"
                    )
                    credential.chmod(0o600)
                state = self.read_state()
                state[field] = True
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )

                result = self.run_dry_run()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                state = self.read_state()
                self.assertEqual(state["service_account_create_calls"], 0)
                self.assertEqual(state["access_permit_create_roles"], [])

    def test_not_found_text_with_non_not_found_status_fails_closed(self) -> None:
        self.write_state(service_account_exists=False)
        state = self.read_state()
        state["service_account_lookup_error"] = True
        state["service_account_lookup_error_message"] = "profile not found"
        self.state_path.write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )

        result = self.run_dry_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Service-account lookup failed", result.stderr)
        self.assertEqual(self.read_state()["service_account_create_calls"], 0)

    def test_profile_lookup_failures_are_not_treated_as_absence(self) -> None:
        for field, expected in (
            ("profile_list_error", "CLI profile lookup failed"),
            ("profile_list_malformed", "unsupported output"),
        ):
            with self.subTest(field=field):
                self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
                self.write_credential()
                credential = (
                    self.home
                    / ".nebius"
                    / f"codex-agent-authkey.{PROJECT}.json"
                )
                credential.chmod(0o600)
                state = self.read_state()
                state[field] = True
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )

                result = self.run_dry_run()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                state = self.read_state()
                self.assertEqual(state["profile_create_calls"], 0)
                self.assertEqual(state["profile_update_calls"], 0)

    def test_membership_lookup_follows_pagination(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        state = self.read_state()
        state["membership_on_second_page"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_dry_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["membership_list_calls"], 2)
        self.assertNotIn(
            "add service account 'codex-agent-sa' to group", result.stderr
        )

    def test_invalid_generated_credential_never_replaces_canonical_path(self) -> None:
        self.write_state(service_account_exists=True)
        state = self.read_state()
        state["generated_credential_invalid"] = True
        self.state_path.write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Generated credential identity is invalid", result.stderr)
        self.assertFalse(credential.exists())
        self.assertEqual(list(self.home.glob(".nebius/*.tmp.*")), [])
        self.assertEqual(self.read_state()["profile_create_calls"], 0)

    def test_invalid_replacement_preserves_existing_credential(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        original = credential.read_bytes()
        state = self.read_state()
        state["credential_broken"] = True
        state["generated_credential_invalid"] = True
        self.state_path.write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )

        setup_result = self.run_setup()
        result = self.run_replacement()

        self.assertIn("credential-replacement-required", setup_result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Replacement credential identity is invalid", result.stderr)
        self.assertEqual(credential.read_bytes(), original)
        self.assertEqual(list(credential.parent.glob("*.bak.*")), [])
        self.assertEqual(list(credential.parent.glob("*.tmp.*")), [])

    def test_dry_run_keeps_an_already_working_profile_unchanged(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)

        result = self.run_dry_run()
        current_actions = result.stderr.split("Currently required actions:", 1)[1].split(
            "Plan digest:", 1
        )[0]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("none (read-only authentication and access verification only)", result.stderr)
        self.assertNotIn("update CLI profile", current_actions)

    def test_live_run_requires_explicit_confirm(self) -> None:
        self.write_state()

        result = self.run_setup(confirm=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Review the plan with --dry-run", result.stderr)
        self.assertEqual(self.read_state()["project_get_calls"], 0)

    def test_confirmed_plan_digest_rejects_target_metadata_drift(self) -> None:
        self.write_state(access_permit_roles=[])
        dry_run = self.run_dry_run()
        digest = self.plan_digest(dry_run)
        state = self.read_state()
        state["project_names"][PROJECT] = "Renamed Project"
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = subprocess.run(
            self.setup_command(confirm_digest=digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the confirmed dry-run digest", result.stderr)
        self.assertEqual(self.read_state()["profile_create_calls"], 0)

    def test_confirmed_plan_digest_rejects_role_or_account_name_drift(self) -> None:
        for extra_args in (
            ("--role", "editor"),
            ("--service-account-name", "different-agent-sa"),
        ):
            with self.subTest(extra_args=extra_args):
                self.write_state(access_permit_roles=[])
                dry_run = self.run_dry_run()
                digest = self.plan_digest(dry_run)

                result = subprocess.run(
                    self.setup_command(
                        confirm_digest=digest,
                        extra_args=extra_args,
                    ),
                    cwd=str(SCRIPT.parent.parent),
                    env=self.env,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "does not match the confirmed dry-run digest", result.stderr
                )
                state = self.read_state()
                self.assertEqual(state["service_account_create_calls"], 0)
                self.assertEqual(state["auth_public_key_generate_calls"], 0)
                self.assertEqual(state["profile_create_calls"], 0)

    def test_missing_credential_creates_service_account_after_validation(self) -> None:
        self.write_state(service_account_exists=False)

        result = self.assert_setup_succeeds()
        state = self.read_state()

        self.assertIn(f"project ID: {PROJECT}", result.stderr)
        self.assertEqual(state["project_get_calls"], 2)
        self.assertEqual(state["service_account_create_calls"], 1)
        self.assertEqual(state["auth_public_key_generate_calls"], 1)
        self.assertTrue(
            (self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json").is_file()
        )

    def test_confirmed_bootstrap_replaces_one_unusable_generated_credential(
        self,
    ) -> None:
        self.write_state(service_account_exists=False)
        state = self.read_state()
        state["generated_credentials_broken_count"] = 1
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        setup_result = self.run_setup()
        result = self.run_replacement()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        backups = list(credential.parent.glob(f"{credential.name}.bak.*"))

        self.assertIn("credential-replacement-required", setup_result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("one bounded credential replacement", result.stderr)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 2)
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    def test_partial_progress_requires_fresh_state_bound_digest(self) -> None:
        self.write_state(access_permit_roles=[], service_account_exists=False)
        initial_plan = self.run_dry_run()
        digest = self.plan_digest(initial_plan)

        state = self.read_state()
        state["service_account_exists"] = True
        state["access_permit_roles"] = ["admin"]
        state["profiles"] = [HUMAN_PROFILE, AGENT_PROFILE]
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["credential_broken"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        continued_plan = self.run_dry_run()
        stale_result = subprocess.run(
            self.setup_command(confirm_digest=digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        fresh_digest = self.plan_digest(continued_plan)
        result = subprocess.run(
            self.setup_command(confirm_digest=fresh_digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(fresh_digest, digest)
        self.assertNotEqual(stale_result.returncode, 0)
        self.assertIn("does not match the confirmed dry-run digest", stale_result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential-replacement-required", result.stderr)
        self.assertEqual(self.read_state()["service_account_create_calls"], 0)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 0)

    def test_plan_exposes_same_name_identity_recreation_for_skill_rejection(
        self,
    ) -> None:
        self.write_state()
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        original_plan = self.run_dry_run()

        recreated_sa_id = "serviceaccount-recreated"
        recreated_group_id = "group-recreated"
        state = self.read_state()
        state["service_account_id"] = recreated_sa_id
        state["group_id"] = recreated_group_id
        state["group_records"] = {f"{PROJECT}:{GROUP_NAME}": recreated_group_id}
        state["service_account_records"][recreated_sa_id] = {
            "name": "codex-agent-sa",
            "parent_id": PROJECT,
        }
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        credential.write_text(
            json.dumps(
                {
                    "subject-credentials": {
                        "type": "JWT",
                        "alg": "RS256",
                        "iss": recreated_sa_id,
                        "sub": recreated_sa_id,
                    }
                }
            ),
            encoding="utf-8",
        )
        credential.chmod(0o600)
        recreated_plan = self.run_dry_run()

        self.assertEqual(original_plan.returncode, 0, original_plan.stderr)
        self.assertEqual(recreated_plan.returncode, 0, recreated_plan.stderr)
        self.assertIn(
            f"observed service-account ID: {SERVICE_ACCOUNT_ID}",
            original_plan.stderr,
        )
        self.assertIn(f"observed group ID: {GROUP_ID}", original_plan.stderr)
        self.assertIn(
            f"observed service-account ID: {recreated_sa_id}",
            recreated_plan.stderr,
        )
        self.assertIn(
            f"observed group ID: {recreated_group_id}", recreated_plan.stderr
        )
        self.assertIn("observed credential SHA-256:", recreated_plan.stderr)
        self.assertNotEqual(
            self.plan_digest(original_plan), self.plan_digest(recreated_plan)
        )

    def test_completed_bootstrap_rejects_replay_of_initial_digest(self) -> None:
        self.write_state(service_account_exists=False)
        initial_plan = self.run_dry_run()
        digest = self.plan_digest(initial_plan)

        completed = subprocess.run(
            self.setup_command(confirm_digest=digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        calls_after_completion = self.read_state()["auth_public_key_generate_calls"]
        replay = subprocess.run(
            self.setup_command(confirm_digest=digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotEqual(replay.returncode, 0)
        self.assertIn("does not match the confirmed dry-run digest", replay.stderr)
        self.assertEqual(
            self.read_state()["auth_public_key_generate_calls"],
            calls_after_completion,
        )

    def test_failed_bounded_replacement_rejects_same_digest_retry(self) -> None:
        self.write_state(service_account_exists=False)
        state = self.read_state()
        state["generated_credentials_broken_count"] = 2
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        initial_plan = self.run_dry_run()
        digest = self.plan_digest(initial_plan)

        setup_failed = subprocess.run(
            self.setup_command(confirm_digest=digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertIn("credential-replacement-required", setup_failed.stderr)
        replacement_plan = self.run_replacement(confirm=False)
        replacement_digest = self.plan_digest(replacement_plan)
        failed = subprocess.run(
            self.replacement_command(confirm_digest=replacement_digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        calls_after_failure = self.read_state()["auth_public_key_generate_calls"]
        replay = subprocess.run(
            self.replacement_command(confirm_digest=replacement_digest),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("one bounded credential replacement", failed.stderr)
        self.assertEqual(calls_after_failure, 2)
        self.assertNotEqual(replay.returncode, 0)
        self.assertIn("does not match the confirmed dry-run digest", replay.stderr)
        self.assertEqual(
            self.read_state()["auth_public_key_generate_calls"],
            calls_after_failure,
        )

    def test_membership_create_conflict_converges_after_readback(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["membership_exists"] = False
        state["membership_create_conflict_converges"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("converged after a create conflict", result.stderr)
        self.assertEqual(self.read_state()["membership_create_calls"], 1)

    def test_existing_credential_identity_must_match_name_and_project(self) -> None:
        for field, value, expected in (
            ("name", "different-sa", "not 'codex-agent-sa'"),
            ("parent_id", "project-other", "not 'project-test'"),
        ):
            with self.subTest(field=field):
                self.write_state()
                self.write_credential()
                state = self.read_state()
                state["service_account_records"][SERVICE_ACCOUNT_ID][field] = value
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )
                credential = (
                    self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
                )
                credential.chmod(0o644)

                result = self.run_dry_run()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o644)
                self.assertEqual(self.read_state()["profile_create_calls"], 0)

    def test_symlinked_credential_fails_dry_run_before_mutation(self) -> None:
        self.write_state()
        target = self.root / "credential-target.json"
        target.write_text(
            json.dumps(
                {
                    "subject-credentials": {
                        "type": "JWT",
                        "alg": "RS256",
                        "iss": SERVICE_ACCOUNT_ID,
                        "sub": SERVICE_ACCOUNT_ID,
                    }
                }
            ),
            encoding="utf-8",
        )
        credential_dir = self.home / ".nebius"
        credential_dir.mkdir()
        (credential_dir / f"codex-agent-authkey.{PROJECT}.json").symlink_to(target)

        result = self.run_dry_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular non-symlink file", result.stderr)
        self.assertEqual(self.read_state()["profile_create_calls"], 0)

    def test_confirmation_envelope_discloses_conditional_credential_replacement(
        self,
    ) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()

        result = self.run_dry_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Authorized convergence actions", result.stderr)
        self.assertIn("back it up and replace it once", result.stderr)

    def test_removed_repair_flag_fails_fast(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()

        result = self.run_setup(confirm=False, extra_args=("--repair",))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown argument: --repair", result.stderr)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 0)

    def test_confirmed_broken_matching_credential_is_backed_up_and_replaced(
        self,
    ) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        original = credential.read_bytes()
        state = self.read_state()
        state["credential_broken"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        setup_result = self.run_setup()
        result = self.run_replacement()

        self.assertIn("credential-replacement-required", setup_result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(credential.parent.glob(f"{credential.name}.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 1)

    def test_repair_lease_requires_existing_working_auth_and_confirmation(
        self,
    ) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])

        missing = self.run_repair_lease(confirm=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("requires an existing confirmed credential", missing.stderr)

        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        insecure = self.run_repair_lease(confirm=False)
        self.assertNotEqual(insecure.returncode, 0)
        self.assertIn("requires the canonical credential at mode 0600", insecure.stderr)
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)
        unconfirmed = subprocess.run(
            self.repair_lease_command(),
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(unconfirmed.returncode, 0)
        self.assertIn("Review the plan with --dry-run", unconfirmed.stderr)
        self.assertFalse(
            (self.home / ".nebius" / "codex-agent-repair-leases").exists()
        )

    def test_confirmed_repair_lease_is_bound_and_private(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)

        result = self.run_repair_lease()

        self.assertEqual(result.returncode, 0, result.stderr)
        lease = self.issued_lease_path(result)
        value = json.loads(lease.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(lease.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(lease.stat().st_mode), 0o600)
        self.assertEqual(value["project_id"], PROJECT)
        self.assertEqual(value["service_account_id"], SERVICE_ACCOUNT_ID)
        self.assertEqual(
            value["allowed_actions"],
            ["chmod-credential-0600", "rebuild-profile"],
        )
        self.assertEqual(
            value["expires_at_epoch"] - value["issued_at_epoch"], 43200
        )

    def test_repair_lease_requires_working_project_access(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)
        state = self.read_state()
        state["project_access_broken"] = True
        self.state_path.write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )

        result = self.run_repair_lease(confirm=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks basic project access", result.stderr)
        self.assertFalse(
            (self.home / ".nebius" / "codex-agent-repair-leases").exists()
        )

    def test_valid_repair_lease_repairs_only_mode_and_matching_profile(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)
        issued = self.run_repair_lease()
        self.assertEqual(issued.returncode, 0, issued.stderr)
        lease = self.issued_lease_path(issued)
        credential.chmod(0o644)
        state = self.read_state()
        state["broken_profiles"] = [AGENT_PROFILE]
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_local_repair(lease)
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertEqual(state["active"], HUMAN_PROFILE)
        self.assertEqual(state["profile_update_calls"], 1)
        self.assertEqual(state["auth_public_key_generate_calls"], 0)
        self.assertEqual(state["access_permit_create_roles"], [])

    def test_repair_local_rejects_altered_expired_and_overlong_leases(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)

        overlong = self.run_repair_lease(
            ttl_seconds=86401, confirm=False
        )
        self.assertNotEqual(overlong.returncode, 0)
        self.assertIn("must be between 1 and 86400", overlong.stderr)

        issued = self.run_repair_lease()
        self.assertEqual(issued.returncode, 0, issued.stderr)
        lease = self.issued_lease_path(issued)
        value = json.loads(lease.read_text(encoding="utf-8"))
        value["project_id"] = "project-other"
        lease.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        lease.chmod(0o600)
        altered = self.run_local_repair(lease)
        self.assertNotEqual(altered.returncode, 0)
        self.assertIn("integrity digest does not match", altered.stderr)

        value["project_id"] = PROJECT
        value["issued_at_epoch"] = int(time.time()) - 100
        value["expires_at_epoch"] = int(time.time()) - 1
        integrity_value = dict(value)
        integrity_value.pop("integrity_sha256")
        canonical = json.dumps(
            integrity_value, sort_keys=True, separators=(",", ":")
        ).encode()
        value["integrity_sha256"] = hashlib.sha256(canonical).hexdigest()
        lease.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        lease.chmod(0o600)
        expired = self.run_local_repair(lease)
        self.assertNotEqual(expired.returncode, 0)
        self.assertIn("lease is not currently valid", expired.stderr)

    def test_repair_local_rejects_bound_credential_content_drift(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)
        issued = self.run_repair_lease()
        self.assertEqual(issued.returncode, 0, issued.stderr)
        lease = self.issued_lease_path(issued)
        state_before = self.read_state()
        credential.write_text(
            json.dumps(
                {
                    "service_account_id": SERVICE_ACCOUNT_ID,
                    "unexpected": "drift",
                }
            ),
            encoding="utf-8",
        )
        credential.chmod(0o600)

        result = self.run_local_repair(lease)
        state_after = self.read_state()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bound credential content has changed", result.stderr)
        self.assertEqual(
            state_after["profile_update_calls"], state_before["profile_update_calls"]
        )
        self.assertEqual(
            state_after["auth_public_key_generate_calls"],
            state_before["auth_public_key_generate_calls"],
        )

    def test_repair_local_rejects_invalid_schema_types_and_oversized_lease(
        self,
    ) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)
        issued = self.run_repair_lease()
        self.assertEqual(issued.returncode, 0, issued.stderr)
        lease = self.issued_lease_path(issued)
        original = json.loads(lease.read_text(encoding="utf-8"))

        def write_mutation(field: str, replacement: object) -> None:
            value = dict(original)
            value[field] = replacement
            value.pop("integrity_sha256")
            canonical = json.dumps(
                value, sort_keys=True, separators=(",", ":")
            ).encode()
            value["integrity_sha256"] = hashlib.sha256(canonical).hexdigest()
            lease.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            lease.chmod(0o600)

        write_mutation("schema_version", True)
        boolean_schema = self.run_local_repair(lease)

        self.assertNotEqual(boolean_schema.returncode, 0)
        self.assertIn("schema version or purpose", boolean_schema.stderr)

        write_mutation("credential_file", {})
        object_path = self.run_local_repair(lease)

        self.assertNotEqual(object_path.returncode, 0)
        self.assertIn("credential path is not canonical", object_path.stderr)

        write_mutation("issued_at_epoch", True)

        boolean_timestamp = self.run_local_repair(lease)

        self.assertNotEqual(boolean_timestamp.returncode, 0)
        self.assertIn("timestamps must be integers", boolean_timestamp.stderr)

        lease.write_bytes(b"x" * (64 * 1024 + 1))
        lease.chmod(0o600)
        oversized = self.run_local_repair(lease)

        self.assertNotEqual(oversized.returncode, 0)
        self.assertIn("65536-byte safety limit", oversized.stderr)

    def test_colliding_project_name_slugs_have_distinct_groups(self) -> None:
        project_one = "project-one"
        project_two = "project-two"
        self.write_state()
        state = self.read_state()
        state["project_ids"] = [project_one, project_two]
        state["project_names"] = {project_one: "A/B", project_two: "A B"}
        state["project_parent_ids"] = {project_one: TENANT, project_two: TENANT}
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        first = self.run_dry_run(project_one)
        second = self.run_dry_run(project_two)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_group = next(
            line.split("group: ", 1)[1]
            for line in first.stderr.splitlines()
            if "group: " in line
        )
        second_group = next(
            line.split("group: ", 1)[1]
            for line in second.stderr.splitlines()
            if "group: " in line
        )
        self.assertNotEqual(first_group, second_group)

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
        self.assertEqual(state["group_get_by_name_calls"], 7)
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_inherited_auth_environment_is_sanitized(self) -> None:
        self.write_state()
        self.write_credential()
        env = self.env.copy()
        env.update(
            {
                "TOKEN": "stale-token",
                "NEBIUS_IAM_TOKEN": "stale-token",
                "NEBIUS_AUTH_CREDENTIALS_FILE": "/tmp/stale.json",
                "NEBIUS_PROJECT_ID": "project-wrong",
            }
        )

        self.assert_setup_succeeds(env=env)

        self.assertEqual(self.read_state()["inherited_auth_attempts"], 0)

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
        self.assertEqual(state["agent_iam_attempts"], 0)

    def test_active_agent_profile_is_not_treated_as_human_session(self) -> None:
        self.write_state(
            active=AGENT_PROFILE,
            profiles=[HUMAN_PROFILE, AGENT_PROFILE],
        )
        self.write_credential()

        result = self.run_setup()
        state = self.read_state()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked-admin-auth", result.stderr)
        self.assertIn("non-agent administrative profile", result.stderr)
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
        state["project_parent_ids"] = {project_one: TENANT, project_two: TENANT}
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
        self.assertEqual(state["agent_iam_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
