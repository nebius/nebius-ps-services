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
STALE_SERVICE_ACCOUNT_ID = "serviceaccount-deleted"
GROUP_ID = "group-test"
GROUP_NAME = f"codex-agent-{hashlib.sha256(PROJECT.encode()).hexdigest()[:20]}"


FAKE_NEBIUS = r"""#!/usr/bin/env python3
from __future__ import annotations

import hashlib
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
    if state.get("profile_write_error"):
        print("profile write failed", file=sys.stderr)
        raise SystemExit(2)
    if profile not in state["profiles"]:
        state["profiles"].append(profile)
    credential_path = option_value("--service-account-file")
    credential_value = json.loads(Path(credential_path).read_text(encoding="utf-8"))
    state["profile_service_account_ids"][profile] = credential_value[
        "subject-credentials"
    ]["iss"]
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
    if profile.startswith("codex-agent-"):
        state["agent_token_calls"] += 1
    else:
        state["human_token_calls"] += 1
    save()
    credential_blocks_agent = state["credential_broken"] and profile.startswith(
        "codex-agent-"
    )
    if state.get("token_transient_error") and profile.startswith("codex-agent-"):
        print("UNAVAILABLE: temporary token service failure", file=sys.stderr)
        raise SystemExit(2)
    if (
        profile in state["profiles"]
        and profile not in state["broken_profiles"]
        and not credential_blocks_agent
    ):
        print("fake-token")
        raise SystemExit(0)
    if credential_blocks_agent:
        print("UNAUTHENTICATED: invalid credential", file=sys.stderr)
    raise SystemExit(2)

if args[:2] == ["iam", "whoami"]:
    profile = current_profile()
    if profile.startswith("codex-agent-"):
        state["agent_whoami_calls"] += 1
        save()
    service_account_id = state["profile_service_account_ids"].get(profile, "")
    if not service_account_id:
        print("profile identity unavailable", file=sys.stderr)
        raise SystemExit(2)
    print_json(
        {
            "service_account_profile": {
                "info": {"metadata": {"id": service_account_id}}
            }
        }
    )
    raise SystemExit(0)

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
    parent_id = option_value("--parent-id")
    account_name = option_value("--name")
    matching_ids = [
        service_account_id
        for service_account_id, record in state["service_account_records"].items()
        if record["parent_id"] == parent_id and record["name"] == account_name
    ]
    if len(matching_ids) != 1:
        print("not found or ambiguous", file=sys.stderr)
        raise SystemExit(13)
    print_json({"metadata": {"id": matching_ids[0]}})
    raise SystemExit(0)

if args[:3] == ["iam", "service-account", "get"]:
    require_human()
    service_account_id = option_value("--id")
    if state.get("service_account_get_error"):
        print(
            state.get(
                "service_account_get_error_message",
                "rpc error: code = Unavailable desc = temporary lookup failure",
            ),
            file=sys.stderr,
        )
        raise SystemExit(state.get("service_account_get_error_status", 2))
    if service_account_id not in state["service_account_records"]:
        print(
            "rpc error: code = NotFound desc = entity not found\n"
            "Resource not found ResourceNotFound: service iam",
            file=sys.stderr,
        )
        raise SystemExit(1)
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
    state["service_account_records"][state["service_account_id"]] = {
        "name": state["service_account_name"],
        "parent_id": state["service_account_parent_id"],
    }
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
    group_id = (
        state["group_id"]
        if group_name == state["group_name"]
        else f"group-{hashlib.sha256(group_name.encode()).hexdigest()[:12]}"
    )
    state["group_records"][f"{parent_id}:{group_name}"] = group_id
    save()
    print_json({"metadata": {"id": group_id}})
    raise SystemExit(0)

if args[:3] == ["iam", "access-permit", "list"]:
    require_human()
    if state.get("access_permit_lookup_error"):
        print("temporary permit lookup failure", file=sys.stderr)
        raise SystemExit(2)
    if state.get("access_permit_unsupported_object"):
        print_json({"unexpected": []})
        raise SystemExit(0)
    parent_id = option_value("--parent-id")
    state["access_permit_list_calls"] += 1
    save()
    if parent_id == state["group_id"]:
        items = [
            {"spec": {"resource_id": state["project_id"], "role": role}}
            for role in state["access_permit_roles"]
        ]
        items.extend(
            {"spec": {"resource_id": state["tenant_id"], "role": role}}
            for role in state["tenant_access_permit_roles"]
        )
    else:
        items = [
            {"spec": permit}
            for permit in state["access_permit_records"].get(parent_id, [])
        ]
    if not items and state.get("access_permit_empty_object"):
        print_json({})
        raise SystemExit(0)
    print_json({"items": items})
    raise SystemExit(0)

if args[:3] == ["iam", "access-permit", "create"]:
    require_human()
    role = option_value("--role")
    parent_id = option_value("--parent-id")
    resource_id = option_value("--resource-id")
    state["access_permit_create_parent_ids"].append(parent_id)
    state["access_permit_create_resource_ids"].append(resource_id)
    state["access_permit_create_roles"].append(role)
    if parent_id == state["group_id"]:
        if resource_id == state["tenant_id"]:
            if role not in state["tenant_access_permit_roles"]:
                state["tenant_access_permit_roles"].append(role)
        elif role not in state["access_permit_roles"]:
            state["access_permit_roles"].append(role)
    else:
        permit = {"resource_id": resource_id, "role": role}
        state["access_permit_records"].setdefault(parent_id, [])
        if permit not in state["access_permit_records"][parent_id]:
            state["access_permit_records"][parent_id].append(permit)
    save()
    raise SystemExit(0)

if args[:3] == ["iam", "group-membership", "list-members"]:
    require_human()
    if state.get("membership_lookup_error"):
        print("temporary membership lookup failure", file=sys.stderr)
        raise SystemExit(2)
    if state.get("membership_unsupported_object"):
        print_json({"unexpected": []})
        raise SystemExit(0)
    state["membership_list_calls"] += 1
    save()
    group_id = option_value("--parent-id")
    page_token = option_value("--page-token")
    if state.get("membership_on_second_page") and not page_token:
        first_page_memberships = (
            [
                {"spec": {"member_id": member_id}}
                for member_id in state["membership_extra_ids"]
            ]
            if state.get("membership_extra_on_first_page")
            else []
        )
        print_json(
            {
                "memberships": first_page_memberships,
                "next_page_token": "page-2",
            }
        )
        raise SystemExit(0)
    if state.get("membership_on_second_page") and page_token != "page-2":
        print("unexpected page token", file=sys.stderr)
        raise SystemExit(2)
    memberships = [
        {"spec": {"member_id": member_id}}
        for member_id in state["membership_records"].get(group_id, [])
    ]
    if (
        group_id not in state["membership_records"]
        and group_id in state["membership_group_ids"]
    ):
        memberships.append({"spec": {"member_id": state["service_account_id"]}})
    if not state.get("membership_extra_on_first_page"):
        memberships.extend(
            {"spec": {"member_id": member_id}}
            for member_id in state["membership_extra_ids"]
        )
    if not memberships and state.get("membership_empty_object"):
        print_json({})
        raise SystemExit(0)
    print_json({"memberships": memberships})
    raise SystemExit(0)

if args[:3] == ["iam", "group-membership", "create"]:
    require_human()
    group_id = option_value("--parent-id")
    state["membership_create_calls"] += 1
    state["membership_create_parent_ids"].append(group_id)
    state["membership_records"][group_id] = [option_value("--member-id")]
    if group_id not in state["membership_group_ids"]:
        state["membership_group_ids"].append(group_id)
    save()
    if state.get("membership_create_conflict_converges"):
        print("AlreadyExists", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(0)

if args[:3] == ["iam", "auth-public-key", "generate"]:
    require_human()
    state["auth_public_key_generate_calls"] += 1
    if state.get("auth_public_key_generate_error"):
        save()
        print("authorized-key generation failed", file=sys.stderr)
        raise SystemExit(2)
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

if args[:3] == ["quotas", "quota-allowance", "list"]:
    state["quota_list_calls"] += 1
    save()
    if (
        state.get("tenant_quota_access_broken")
        or not any(
            group_id in state["membership_group_ids"]
            and (
                (
                    group_id == state["group_id"]
                    and "viewer" in state["tenant_access_permit_roles"]
                )
                or any(
                    permit["resource_id"] == state["tenant_id"]
                    and permit["role"] == "viewer"
                    for permit in state["access_permit_records"].get(group_id, [])
                )
            )
            for group_id in state["membership_group_ids"]
        )
    ):
        print("tenant quota access denied", file=sys.stderr)
        raise SystemExit(13)
    print_json({"items": []})
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
            "access_permit_list_calls": 0,
            "access_permit_records": {},
            "tenant_access_permit_roles": ["viewer"],
            "agent_iam_attempts": 0,
            "agent_token_calls": 0,
            "agent_whoami_calls": 0,
            "human_token_calls": 0,
            "auth_public_key_generate_calls": 0,
            "auth_public_key_generate_error": False,
            "broken_profiles": broken_profiles or [],
            "credential_broken": False,
            "generated_credentials_broken_count": 0,
            "generated_credential_invalid": False,
            "service_account_lookup_error": False,
            "service_account_get_error": False,
            "service_account_get_error_message": "",
            "service_account_get_error_status": 2,
            "group_lookup_error": False,
            "access_permit_lookup_error": False,
            "access_permit_empty_object": False,
            "access_permit_unsupported_object": False,
            "membership_lookup_error": False,
            "membership_empty_object": False,
            "membership_unsupported_object": False,
            "membership_create_calls": 0,
            "membership_create_parent_ids": [],
            "membership_create_conflict_converges": False,
            "membership_group_ids": [GROUP_ID] if service_account_exists else [],
            "membership_records": {},
            "membership_extra_ids": [],
            "membership_extra_on_first_page": False,
            "membership_list_calls": 0,
            "membership_on_second_page": False,
            "profile_list_error": False,
            "profile_list_malformed": False,
            "profile_write_error": False,
            "profile_service_account_ids": {
                AGENT_PROFILE: SERVICE_ACCOUNT_ID,
            },
            "token_transient_error": False,
            "group_get_by_name_calls": 0,
            "group_get_parent_ids": [],
            "group_create_parent_ids": [],
            "group_records": {
                f"{TENANT}:{GROUP_NAME}": GROUP_ID,
            },
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
            "quota_list_calls": 0,
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
            "group_name": GROUP_NAME,
            "project_id": PROJECT,
            "tenant_quota_access_broken": False,
            "tenant_id": TENANT,
        }
        self.state_path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_credential(
        self,
        project: str = PROJECT,
        *,
        service_account_id: str | None = None,
        register_service_account: bool = True,
    ) -> None:
        (self.home / ".nebius").mkdir(exist_ok=True)
        (self.home / ".nebius").chmod(0o700)
        selected_service_account_id = service_account_id or (
            SERVICE_ACCOUNT_ID if project == PROJECT else f"serviceaccount-{project}"
        )
        state = self.read_state()
        if register_service_account:
            state["service_account_records"][selected_service_account_id] = {
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
                        "iss": selected_service_account_id,
                        "sub": selected_service_account_id,
                    }
                }
            ),
            encoding="utf-8",
        )

    def write_deleted_service_account_credential(
        self,
        *,
        replacement_service_account_exists: bool = False,
    ) -> Path:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential(service_account_id=STALE_SERVICE_ACCOUNT_ID)
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["service_account_records"].pop(STALE_SERVICE_ACCOUNT_ID)
        state["service_account_id"] = SERVICE_ACCOUNT_ID
        state["service_account_exists"] = replacement_service_account_exists
        if replacement_service_account_exists:
            state["service_account_records"][SERVICE_ACCOUNT_ID] = {
                "name": "codex-agent-sa",
                "parent_id": PROJECT,
            }
        else:
            state["service_account_records"].pop(SERVICE_ACCOUNT_ID, None)
        state["profile_service_account_ids"][AGENT_PROFILE] = (
            STALE_SERVICE_ACCOUNT_ID
        )
        state["membership_group_ids"] = []
        state["membership_records"] = {}
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        return credential

    def setup_command(
        self,
        project: str | None = PROJECT,
        *,
        tenant: str | None = TENANT,
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
        return command

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
        return subprocess.run(
            self.setup_command(
                project,
                tenant=tenant,
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

    def repair_lease_command(
        self,
        *,
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
        return command

    def run_repair_lease(
        self, *, ttl_seconds: int = 43200, confirm: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                *self.repair_lease_command(ttl_seconds=ttl_seconds),
                *([] if confirm else ["--dry-run"]),
            ],
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
        return subprocess.Popen(
            self.setup_command(
                project,
                tenant=tenant,
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
        self.assertEqual(after["quota_list_calls"], 1)
        self.assertEqual(after["agent_iam_attempts"], before["agent_iam_attempts"])

    def test_verify_rejects_noncanonical_service_account_identity(self) -> None:
        foreign_service_account_id = "serviceaccount-foreign"
        self.write_state(active=AGENT_PROFILE, profiles=[AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential_value = json.loads(credential.read_text(encoding="utf-8"))
        credential_value["subject-credentials"]["iss"] = foreign_service_account_id
        credential_value["subject-credentials"]["sub"] = foreign_service_account_id
        credential.write_text(json.dumps(credential_value), encoding="utf-8")
        credential.chmod(0o600)
        state = self.read_state()
        state["profile_service_account_ids"][AGENT_PROFILE] = foreign_service_account_id
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not resolve the canonical service account", result.stderr)

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
            (False, True, "does not resolve the canonical service account"),
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

    def test_verify_requires_authoritative_tenant_quota_read_access(self) -> None:
        self.write_state(profiles=[AGENT_PROFILE])
        state = self.read_state()
        state["tenant_quota_access_broken"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)

        result = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks authoritative tenant quota-allowance read access", result.stderr)
        self.assertEqual(self.read_state()["quota_list_calls"], 1)

    def test_verify_rejects_profile_credential_identity_mismatch(self) -> None:
        self.write_state(profiles=[AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["profile_service_account_ids"][AGENT_PROFILE] = (
            "serviceaccount-different"
        )
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the canonical credential", result.stderr)
        self.assertEqual(self.read_state()["profile_update_calls"], 0)

    def test_managed_group_rejects_existing_extra_project_role(self) -> None:
        self.write_state(access_permit_roles=["editor"])
        self.write_credential()

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain only project 'admin' and tenant 'viewer'", result.stderr)
        self.assertEqual(self.read_state()["access_permit_create_roles"], [])

    def test_role_override_is_rejected(self) -> None:
        self.write_state(access_permit_roles=[])
        self.write_credential()

        result = self.run_setup(extra_args=("--role", "editor"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown argument: --role", result.stderr)
        self.assertEqual(self.read_state()["access_permit_create_roles"], [])

    def test_confirmation_option_is_rejected(self) -> None:
        self.write_state()

        result = self.run_setup(extra_args=("--confirm", "obsolete"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown argument: --confirm", result.stderr)
        self.assertEqual(self.read_state()["project_get_calls"], 0)

    def test_managed_group_rejects_duplicate_permits(self) -> None:
        self.write_state(access_permit_roles=["admin", "admin"])
        self.write_credential()

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without duplicates; refusing mutation", result.stderr)
        self.assertEqual(self.read_state()["membership_create_calls"], 0)

    def test_managed_group_rejects_extra_or_duplicate_members(self) -> None:
        for extras in (["serviceaccount-extra"], [SERVICE_ACCOUNT_ID]):
            with self.subTest(extras=extras):
                self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
                self.write_credential()
                credential = (
                    self.home
                    / ".nebius"
                    / f"codex-agent-authkey.{PROJECT}.json"
                )
                credential.chmod(0o600)
                state = self.read_state()
                state["membership_extra_ids"] = extras
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )

                result = self.run_setup()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must contain only one membership", result.stderr)
                self.assertEqual(self.read_state()["membership_create_calls"], 0)

    def test_managed_group_rejects_extra_member_on_another_page(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["membership_on_second_page"] = True
        state["membership_extra_on_first_page"] = True
        state["membership_extra_ids"] = ["serviceaccount-extra"]
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain only one membership", result.stderr)
        self.assertEqual(self.read_state()["membership_list_calls"], 2)

    def test_setup_adds_fixed_tenant_viewer_permit_for_quota_reads(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["tenant_access_permit_roles"] = []
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(state["access_permit_create_roles"], ["viewer"])
        self.assertEqual(state["access_permit_create_parent_ids"], [GROUP_ID])
        self.assertEqual(state["access_permit_create_resource_ids"], [TENANT])
        self.assertEqual(state["tenant_access_permit_roles"], ["viewer"])
        self.assertGreaterEqual(state["quota_list_calls"], 1)

    def test_empty_list_objects_converge_permits_and_memberships(self) -> None:
        self.write_state(
            access_permit_roles=[], profiles=[HUMAN_PROFILE, AGENT_PROFILE]
        )
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["access_permit_empty_object"] = True
        state["membership_empty_object"] = True
        state["membership_group_ids"] = []
        state["tenant_access_permit_roles"] = []
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["access_permit_create_roles"], ["admin", "viewer"])
        self.assertEqual(state["access_permit_roles"], ["admin"])
        self.assertEqual(state["tenant_access_permit_roles"], ["viewer"])
        self.assertEqual(
            state["membership_create_parent_ids"],
            [GROUP_ID],
        )

    def test_nonempty_unsupported_list_objects_fail_closed(self) -> None:
        for field, expected in (
            (
                "access_permit_unsupported_object",
                "Access-permit lookup returned malformed JSON",
            ),
            (
                "membership_unsupported_object",
                "Group-membership lookup returned malformed JSON",
            ),
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
                self.assertEqual(self.read_state()["access_permit_create_roles"], [])

    def test_empty_group_member_id_fails_closed(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["membership_records"] = {GROUP_ID: [""]}
        state["membership_group_ids"] = []
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty or malformed member ID", result.stderr)
        self.assertEqual(self.read_state()["membership_create_calls"], 0)

    def test_managed_group_rejects_any_broader_existing_permit(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["tenant_access_permit_roles"] = ["viewer", "admin"]
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_dry_run()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain only project 'admin' and tenant 'viewer'", result.stderr)
        self.assertEqual(self.read_state()["membership_create_calls"], 0)

    def test_live_setup_fails_closed_on_tenant_permit_drift(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["tenant_access_permit_roles"] = ["viewer", "editor"]
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain only project 'admin' and tenant 'viewer'", result.stderr)
        self.assertEqual(self.read_state()["membership_create_calls"], 0)

    def test_help_reports_fixed_roles_and_no_confirmation(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            cwd=str(SCRIPT.parent.parent),
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("exactly project 'admin'", result.stderr)
        self.assertIn("plus tenant 'viewer'", result.stderr)
        self.assertNotIn("--confirm", result.stderr)
        self.assertNotIn("--role", result.stderr)
        self.assertNotIn("--service-account-name", result.stderr)

    def test_service_account_name_override_is_rejected(self) -> None:
        result = self.run_setup(
            extra_args=("--service-account-name", "codex-agent-other")
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown argument: --service-account-name", result.stderr)

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

        self.assert_setup_succeeds(tenant=None)
        state = self.read_state()

        self.assertEqual(state["project_get_calls"], 2)
        self.assertIn(AGENT_PROFILE, state["profiles"])
        self.assertEqual(set(state["group_get_parent_ids"]), {TENANT})

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

    def test_one_tenant_group_has_exact_project_and_tenant_scopes(self) -> None:
        self.write_state(
            access_permit_roles=[],
            profiles=[HUMAN_PROFILE, AGENT_PROFILE],
        )
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["group_records"] = {}
        state["tenant_access_permit_roles"] = []
        state["membership_group_ids"] = []
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["group_create_parent_ids"], [TENANT])
        self.assertTrue(state["group_get_parent_ids"])
        self.assertEqual(set(state["group_get_parent_ids"]), {TENANT})
        self.assertEqual(
            state["access_permit_create_parent_ids"],
            [GROUP_ID, GROUP_ID],
        )
        self.assertEqual(state["access_permit_create_resource_ids"], [PROJECT, TENANT])
        self.assertEqual(state["access_permit_create_roles"], ["admin", "viewer"])
        self.assertEqual(
            state["membership_create_parent_ids"],
            [GROUP_ID],
        )

        repeated = self.run_setup()
        repeated_state = self.read_state()

        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(
            repeated_state["membership_create_parent_ids"],
            [GROUP_ID],
        )

    def test_same_name_project_parented_group_is_not_reused(self) -> None:
        self.write_state(access_permit_roles=[])
        state = self.read_state()
        state["group_records"] = {f"{PROJECT}:{GROUP_NAME}": "group-project-legacy"}
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_dry_run()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"create group '{GROUP_NAME}' under tenant '{TENANT}'",
            result.stderr,
        )
        self.assertEqual(set(state["group_get_parent_ids"]), {TENANT})
        self.assertNotIn("group-project-legacy", result.stderr)

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

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Replacement credential identity is invalid", result.stderr)
        self.assertEqual(credential.read_bytes(), original)
        self.assertEqual(list(credential.parent.glob("*.bak.*")), [])
        self.assertEqual(list(credential.parent.glob("*.tmp.*")), [])

    def test_profile_write_failure_never_replaces_credential(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        original = credential.read_bytes()
        state = self.read_state()
        state["profile_write_error"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no additional credential replacement was attempted", result.stderr)
        self.assertEqual(credential.read_bytes(), original)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 0)
        self.assertEqual(list(credential.parent.glob("*.bak.*")), [])

    def test_transient_token_failure_never_replaces_credential(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        original = credential.read_bytes()
        state = self.read_state()
        state["token_transient_error"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without a classified credential-authentication error", result.stderr)
        self.assertEqual(credential.read_bytes(), original)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 0)
        self.assertEqual(list(credential.parent.glob("*.bak.*")), [])

    def test_dry_run_keeps_an_already_working_profile_unchanged(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)

        result = self.run_dry_run()
        current_actions = result.stderr.split("Currently required actions:", 1)[1].split(
            "No global default-project selector", 1
        )[0]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("none (read-only authentication and access verification only)", result.stderr)
        self.assertNotIn("update CLI profile", current_actions)

    def test_dry_run_reports_wrong_identity_profile_rebind(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["profile_service_account_ids"][AGENT_PROFILE] = "serviceaccount-other"
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_dry_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"update CLI profile '{AGENT_PROFILE}'", result.stderr)
        self.assertIn("back up and replace the canonical credential once", result.stderr)
        self.assertEqual(self.read_state()["profile_update_calls"], 0)

        live = self.run_setup()
        live_state = self.read_state()

        self.assertEqual(live.returncode, 0, live.stderr)
        self.assertEqual(live_state["profile_update_calls"], 1)
        self.assertEqual(live_state["auth_public_key_generate_calls"], 0)
        self.assertEqual(
            live_state["profile_service_account_ids"][AGENT_PROFILE],
            SERVICE_ACCOUNT_ID,
        )

    def test_dry_run_discloses_conditional_broken_credential_replacement(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["credential_broken"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_dry_run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"update CLI profile '{AGENT_PROFILE}'", result.stderr)
        self.assertIn("back up and replace the canonical credential once", result.stderr)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 0)

    def test_explicit_ensure_runs_without_confirmation(self) -> None:
        self.write_state()

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["project_get_calls"], 2)

    def test_missing_credential_creates_service_account_after_validation(self) -> None:
        self.write_state(service_account_exists=False)

        self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(state["project_get_calls"], 2)
        self.assertEqual(state["service_account_create_calls"], 1)
        self.assertEqual(state["auth_public_key_generate_calls"], 1)
        self.assertTrue(
            (self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json").is_file()
        )

    def test_missing_credential_rebinds_existing_working_profile(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])

        dry_run = self.run_dry_run()

        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertIn(f"generate credential '{self.home}/.nebius/", dry_run.stderr)
        self.assertIn(f"update CLI profile '{AGENT_PROFILE}'", dry_run.stderr)
        self.assertEqual(self.read_state()["profile_update_calls"], 0)

        live = self.run_setup()
        state = self.read_state()

        self.assertEqual(live.returncode, 0, live.stderr)
        self.assertEqual(state["auth_public_key_generate_calls"], 1)
        self.assertEqual(state["profile_update_calls"], 1)
        self.assertEqual(
            state["profile_service_account_ids"][AGENT_PROFILE],
            SERVICE_ACCOUNT_ID,
        )

    def test_deleted_service_account_credential_bootstraps_with_human_profile(
        self,
    ) -> None:
        credential = self.write_deleted_service_account_credential()
        original = credential.read_bytes()

        first = self.assert_setup_succeeds()
        first_state = self.read_state()
        backups = list(credential.parent.glob(f"{credential.name}.bak.*"))
        credential_value = json.loads(credential.read_text(encoding="utf-8"))

        self.assertIn("bootstrapping the fixed account", first.stderr)
        self.assertEqual(first_state["service_account_create_calls"], 1)
        self.assertEqual(first_state["auth_public_key_generate_calls"], 1)
        self.assertEqual(first_state["membership_create_calls"], 1)
        self.assertEqual(first_state["profile_update_calls"], 1)
        self.assertGreaterEqual(first_state["human_token_calls"], 1)
        self.assertEqual(first_state["agent_iam_attempts"], 0)
        self.assertEqual(first_state["inherited_auth_attempts"], 0)
        self.assertEqual(first_state["active"], HUMAN_PROFILE)
        self.assertEqual(
            first_state["profile_service_account_ids"][AGENT_PROFILE],
            SERVICE_ACCOUNT_ID,
        )
        self.assertEqual(
            credential_value["subject-credentials"]["iss"], SERVICE_ACCOUNT_ID
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertEqual(list(credential.parent.glob("*.tmp.*")), [])

        second = self.assert_setup_succeeds()
        second_state = self.read_state()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second_state["service_account_create_calls"], 1)
        self.assertEqual(second_state["auth_public_key_generate_calls"], 1)
        self.assertEqual(second_state["membership_create_calls"], 1)
        self.assertEqual(second_state["profile_update_calls"], 1)
        self.assertEqual(
            len(list(credential.parent.glob(f"{credential.name}.bak.*"))), 1
        )

    def test_dry_run_reports_deleted_service_account_recovery_without_mutation(
        self,
    ) -> None:
        credential = self.write_deleted_service_account_credential()
        original = credential.read_bytes()

        result = self.run_dry_run()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("with the validated human profile", result.stderr)
        self.assertIn("generate one replacement authorized-key credential", result.stderr)
        self.assertIn("back up the stale credential at mode 0600", result.stderr)
        self.assertEqual(state["service_account_create_calls"], 0)
        self.assertEqual(state["auth_public_key_generate_calls"], 0)
        self.assertEqual(state["membership_create_calls"], 0)
        self.assertEqual(state["profile_update_calls"], 0)
        self.assertEqual(credential.read_bytes(), original)
        self.assertEqual(list(credential.parent.glob("*.bak.*")), [])
        self.assertEqual(list(credential.parent.glob("*.tmp.*")), [])

    def test_existing_credential_identity_lookup_failures_do_not_rebootstrap(
        self,
    ) -> None:
        cases = (
            "rpc error: code = PermissionDenied desc = access denied",
            "rpc error: code = Unavailable desc = temporary lookup failure",
            "rpc error: code = PermissionDenied desc = wrapped "
            "rpc error: code = NotFound",
            "profile not found",
        )
        for message in cases:
            with self.subTest(message=message):
                self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
                self.write_credential()
                credential = (
                    self.home
                    / ".nebius"
                    / f"codex-agent-authkey.{PROJECT}.json"
                )
                credential.chmod(0o600)
                original = credential.read_bytes()
                state = self.read_state()
                state["service_account_get_error"] = True
                state["service_account_get_error_message"] = message
                state["service_account_get_error_status"] = 1
                self.state_path.write_text(
                    json.dumps(state, sort_keys=True), encoding="utf-8"
                )

                result = self.run_setup()
                state = self.read_state()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refusing mutation", result.stderr)
                self.assertEqual(state["service_account_create_calls"], 0)
                self.assertEqual(state["auth_public_key_generate_calls"], 0)
                self.assertEqual(state["membership_create_calls"], 0)
                self.assertEqual(state["profile_update_calls"], 0)
                self.assertEqual(credential.read_bytes(), original)
                self.assertEqual(list(credential.parent.glob("*.bak.*")), [])

    def test_deleted_service_account_recovery_preserves_stale_credential_when_generation_fails(
        self,
    ) -> None:
        credential = self.write_deleted_service_account_credential()
        original = credential.read_bytes()
        state = self.read_state()
        state["auth_public_key_generate_error"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()
        state = self.read_state()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Replacement credential generation failed", result.stderr)
        self.assertEqual(state["service_account_create_calls"], 1)
        self.assertEqual(state["auth_public_key_generate_calls"], 1)
        self.assertEqual(state["profile_update_calls"], 0)
        self.assertEqual(credential.read_bytes(), original)
        self.assertEqual(list(credential.parent.glob("*.bak.*")), [])
        self.assertEqual(list(credential.parent.glob("*.tmp.*")), [])

    def test_deleted_service_account_recovery_never_generates_a_second_key(
        self,
    ) -> None:
        credential = self.write_deleted_service_account_credential()
        state = self.read_state()
        state["generated_credentials_broken_count"] = 1
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()
        state = self.read_state()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no second credential was generated", result.stderr)
        self.assertEqual(state["auth_public_key_generate_calls"], 1)
        self.assertEqual(
            len(list(credential.parent.glob(f"{credential.name}.bak.*"))), 1
        )

    def test_deleted_identity_reuses_an_existing_canonical_service_account(
        self,
    ) -> None:
        credential = self.write_deleted_service_account_credential(
            replacement_service_account_exists=True
        )

        result = self.assert_setup_succeeds()
        state = self.read_state()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["service_account_create_calls"], 0)
        self.assertEqual(state["auth_public_key_generate_calls"], 1)
        self.assertEqual(
            state["profile_service_account_ids"][AGENT_PROFILE], SERVICE_ACCOUNT_ID
        )
        self.assertEqual(
            len(list(credential.parent.glob(f"{credential.name}.bak.*"))), 1
        )

    def test_ensure_replaces_one_unusable_generated_credential(
        self,
    ) -> None:
        self.write_state(service_account_exists=False)
        state = self.read_state()
        state["generated_credentials_broken_count"] = 1
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        backups = list(credential.parent.glob(f"{credential.name}.bak.*"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replacing it once", result.stderr)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 2)
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

    def test_dry_run_exposes_observed_identity_and_one_group(self) -> None:
        self.write_state()
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        original_plan = self.run_dry_run()

        self.assertEqual(original_plan.returncode, 0, original_plan.stderr)
        self.assertIn(
            f"observed service-account ID: {SERVICE_ACCOUNT_ID}",
            original_plan.stderr,
        )
        self.assertIn(f"observed group ID: {GROUP_ID}", original_plan.stderr)
        self.assertIn("observed credential SHA-256:", original_plan.stderr)
        self.assertIn("group parent: tenant-test", original_plan.stderr)

    def test_failed_bounded_replacement_stops_after_two_total_generations(self) -> None:
        self.write_state(service_account_exists=False)
        state = self.read_state()
        state["generated_credentials_broken_count"] = 2
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no second replacement was attempted", result.stderr)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 2)

    def test_membership_create_conflict_converges_after_readback(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["membership_group_ids"] = []
        state["membership_create_conflict_converges"] = True
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("converged after a create conflict", result.stderr)
        self.assertEqual(self.read_state()["membership_create_calls"], 1)
        self.assertEqual(
            self.read_state()["membership_create_parent_ids"], [GROUP_ID]
        )

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

    def test_removed_repair_flag_fails_fast(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()

        result = self.run_setup(confirm=False, extra_args=("--repair",))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown argument: --repair", result.stderr)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 0)

    def test_broken_matching_credential_is_backed_up_and_replaced_once(
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

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        backups = list(credential.parent.glob(f"{credential.name}.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        self.assertEqual(self.read_state()["auth_public_key_generate_calls"], 1)

    def test_repair_lease_requires_existing_working_auth(
        self,
    ) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])

        missing = self.run_repair_lease(confirm=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("requires an existing working credential", missing.stderr)

        self.write_credential()
        credential = (
            self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        )
        insecure = self.run_repair_lease(confirm=False)
        self.assertNotEqual(insecure.returncode, 0)
        self.assertIn("requires the canonical credential at mode 0600", insecure.stderr)
        credential.chmod(0o600)
        (self.home / ".nebius").chmod(0o700)
        issued = self.run_repair_lease()
        self.assertEqual(issued.returncode, 0, issued.stderr)
        self.assertTrue(self.issued_lease_path(issued).is_file())

    def test_repair_lease_is_bound_and_private(self) -> None:
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
        self.assertIn("canonical service account with project access", result.stderr)
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

        write_mutation("service_account_name", "codex-agent-other")
        noncanonical_account = self.run_local_repair(lease)

        self.assertNotEqual(noncanonical_account.returncode, 0)
        self.assertIn("not the canonical 'codex-agent-sa'", noncanonical_account.stderr)

        lease.write_bytes(b"x" * (64 * 1024 + 1))
        lease.chmod(0o600)
        oversized = self.run_local_repair(lease)

        self.assertNotEqual(oversized.returncode, 0)
        self.assertIn("65536-byte safety limit", oversized.stderr)

    def test_distinct_project_ids_have_distinct_groups(self) -> None:
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

    def test_project_rename_reuses_the_same_id_hash_group(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)
        state = self.read_state()
        state["group_records"] = {}
        state["access_permit_roles"] = []
        state["tenant_access_permit_roles"] = []
        state["membership_group_ids"] = []
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

        first = self.run_setup()
        state = self.read_state()
        state["project_names"][PROJECT] = "Renamed Project"
        self.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        second = self.run_setup()
        final_state = self.read_state()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(final_state["group_create_parent_ids"], [TENANT])
        self.assertEqual(
            final_state["group_records"], {f"{TENANT}:{GROUP_NAME}": GROUP_ID}
        )

    def test_success_output_uses_selector_prefixed_token_check(self) -> None:
        self.write_state(profiles=[HUMAN_PROFILE, AGENT_PROFILE])
        self.write_credential()
        credential = self.home / ".nebius" / f"codex-agent-authkey.{PROJECT}.json"
        credential.chmod(0o600)

        result = self.assert_setup_succeeds()

        self.assertIn(
            f"Token test: CODEX_NEBIUS_PROJECT_ID={PROJECT} nebius iam "
            f"get-access-token --no-browser --profile {AGENT_PROFILE} >/dev/null",
            result.stderr,
        )

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
        self.assertEqual(state["access_permit_list_calls"], 1)
        self.assertEqual(state["membership_list_calls"], 1)
        self.assertEqual(state["agent_token_calls"], 1)
        self.assertEqual(state["agent_whoami_calls"], 1)
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
