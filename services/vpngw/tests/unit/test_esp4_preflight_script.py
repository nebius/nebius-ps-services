from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = PROJECT_ROOT / "src/nebius_vpngw/systemd/nebius-vpngw-esp4-preflight.sh"
FIX_SCRIPT = PROJECT_ROOT / "misc/fix-vpngw-esp4.sh"


@pytest.mark.parametrize("script", [HELPER_SCRIPT, FIX_SCRIPT])
def test_shell_scripts_parse(script: Path) -> None:
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def _helper_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "VPNGW_ESP4_MODPROBE_DIR": str(tmp_path / "modprobe.d"),
            "VPNGW_ESP4_STATE_DIR": str(tmp_path / "state"),
            "VPNGW_ESP4_REBOOT_REQUIRED_FILE": str(tmp_path / "reboot-required"),
            "VPNGW_ESP4_SKIP_INITRAMFS": "1",
            "VPNGW_ESP4_SKIP_MODPROBE": "1",
            "VPNGW_ESP4_ALLOW_NON_ROOT": "1",
        }
    )
    return env


def test_esp4_preflight_comments_only_esp4_blocks(tmp_path: Path) -> None:
    modprobe_dir = tmp_path / "modprobe.d"
    modprobe_dir.mkdir()
    policy_file = modprobe_dir / "dirty-frag.conf"
    policy_file.write_text(
        "\n".join(
            [
                "install esp4 /bin/false",
                "install esp6 /bin/false",
                "install rxrpc /bin/false",
                "blacklist esp4",
                "blacklist esp6",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(HELPER_SCRIPT), "--prepare"],
        capture_output=True,
        text=True,
        env=_helper_env(tmp_path),
    )

    assert result.returncode == 75, result.stderr
    updated = policy_file.read_text(encoding="utf-8")
    assert "# nebius-vpngw: disabled ESP4 block: install esp4 /bin/false" in updated
    assert "# nebius-vpngw: disabled ESP4 block: blacklist esp4" in updated
    assert "install esp6 /bin/false" in updated
    assert "install rxrpc /bin/false" in updated
    assert "blacklist esp6" in updated
    assert list(modprobe_dir.glob("dirty-frag.conf.nebius-vpngw-esp4.*.bak"))
    assert (tmp_path / "state/esp4-reboot-pending").exists()


def test_esp4_preflight_noop_when_esp4_is_not_blocked(tmp_path: Path) -> None:
    modprobe_dir = tmp_path / "modprobe.d"
    modprobe_dir.mkdir()
    policy_file = modprobe_dir / "dirty-frag.conf"
    original = "install esp6 /bin/false\ninstall rxrpc /bin/false\n"
    policy_file.write_text(original, encoding="utf-8")

    result = subprocess.run(
        [str(HELPER_SCRIPT), "--prepare"],
        capture_output=True,
        text=True,
        env=_helper_env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    assert policy_file.read_text(encoding="utf-8") == original
    assert not list(modprobe_dir.glob("*.bak"))
    assert (tmp_path / "state/esp4-ready").exists()
    assert not (tmp_path / "state/esp4-reboot-pending").exists()


def test_fix_vpngw_esp4_help_and_dry_run_host() -> None:
    help_result = subprocess.run([str(FIX_SCRIPT), "--help"], capture_output=True, text=True)
    dry_run_result = subprocess.run(
        [str(FIX_SCRIPT), "--dry-run", "--host", "203.0.113.10"],
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0
    assert "fix-vpngw-esp4.sh" in help_result.stdout
    assert dry_run_result.returncode == 0, dry_run_result.stderr
    assert "ubuntu@203.0.113.10" in dry_run_result.stdout
    assert "dry-run" in dry_run_result.stdout


def test_fix_vpngw_esp4_discovers_hosts_from_local_config(tmp_path: Path) -> None:
    config_path = tmp_path / "nebius-vpngw.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "gateway_group": {
                    "external_ips": [["203.0.113.10"], ["${VPNGW_IP}"], ["203.0.113.11"]],
                    "vm_spec": {
                        "ssh_username": "operator",
                        "ssh_private_key_path": "~/.ssh/id_ed25519",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(FIX_SCRIPT), "--dry-run", "--local-config-file", str(config_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "operator@203.0.113.10" in result.stdout
    assert "operator@203.0.113.11" in result.stdout
    assert "${VPNGW_IP}" not in result.stdout


def test_fix_vpngw_esp4_expands_tilde_identity_path() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {FIX_SCRIPT}; HOME=/tmp/vpngw-home expand_path '~/.ssh/id_ed25519'",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "/tmp/vpngw-home/.ssh/id_ed25519"


def test_fix_vpngw_esp4_waits_for_boot_id_change(tmp_path: Path) -> None:
    counter_path = tmp_path / "ssh-calls"
    counter_path.write_text("0", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"""
source {FIX_SCRIPT}
counter_path={shlex.quote(str(counter_path))}
fake_ssh() {{
  calls="$(<"${{counter_path}}")"
  calls=$((calls + 1))
  printf '%s' "${{calls}}" > "${{counter_path}}"
  if [[ "${{calls}}" -eq 1 ]]; then
    printf 'old-boot\\n'
  else
    printf 'new-boot\\n'
  fi
}}
build_ssh_cmd() {{ SSH_CMD=(fake_ssh); }}
sleep() {{ :; }}
WAIT_TIMEOUT=5
wait_for_rebooted_ssh 'ubuntu@203.0.113.10' 'old-boot'
printf 'calls=%s\\n' "$(<"${{counter_path}}")"
""",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "calls=2\n"


def test_fix_vpngw_esp4_rejects_unknown_option() -> None:
    result = subprocess.run([str(FIX_SCRIPT), "--unknown"], capture_output=True, text=True)

    assert result.returncode == 1
    assert "Unknown option" in result.stderr


def test_fix_vpngw_esp4_requires_confirmation_for_mutating_run() -> None:
    result = subprocess.run(
        [str(FIX_SCRIPT), "--host", "203.0.113.10"],
        input="",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Confirmation aborted" in result.stderr
