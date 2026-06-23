from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh"


def _write_lock(path: Path, *, release: str) -> None:
    path.write_text(
        "\n".join(
            [
                'repository: "https://github.com/nebius/soperator"',
                f'release: "{release}"',
                f'tag: "{release}"',
                'commit: "deadbeef"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_fake_curl(
    path: Path,
    *,
    page_one_releases: list[dict[str, object]],
    page_two_releases: list[dict[str, object]] | None = None,
) -> None:
    page_one_json = json.dumps(page_one_releases)
    page_two_json = json.dumps(page_two_releases or [])
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
url="${{@: -1}}"
case "${{url}}" in
  *"/releases?per_page=100&page=1")
    cat <<'JSON'
{page_one_json}
JSON
    ;;
  *"/releases?per_page=100&page=2")
    cat <<'JSON'
{page_two_json}
JSON
    ;;
  *"/releases?per_page=100&page=3")
    printf '[]\\n'
    ;;
  *)
    printf 'unexpected curl URL: %s\\n' "${{url}}" >&2
    exit 22
    ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_check_latest(tmp_path: Path, *, locked_release: str) -> subprocess.CompletedProcess[str]:
    return _run_check_latest_with_release_pages(
        tmp_path,
        locked_release=locked_release,
        page_one_releases=[
            {"tag_name": "3.0.7", "draft": False, "prerelease": False},
            {"tag_name": "4.0.2", "draft": False, "prerelease": False},
        ],
    )


def _run_check_latest_with_release_pages(
    tmp_path: Path,
    *,
    locked_release: str,
    page_one_releases: list[dict[str, object]],
    page_two_releases: list[dict[str, object]] | None = None,
) -> subprocess.CompletedProcess[str]:
    lock_file = tmp_path / "upstream-soperator.lock.yaml"
    _write_lock(lock_file, release=locked_release)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_curl(
        bin_dir / "curl",
        page_one_releases=page_one_releases,
        page_two_releases=page_two_releases,
    )
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)
    return subprocess.run(
        [str(SCRIPT), "--lock-file", str(lock_file), "--check-latest"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_check_latest_uses_highest_semver_release_not_latest_badge(tmp_path: Path) -> None:
    result = _run_check_latest(tmp_path, locked_release="4.0.2")

    assert result.returncode == 0, result.stderr
    assert "Pinned Soperator release '4.0.2' is the highest GitHub SemVer release." in (
        result.stdout
    )


def test_check_latest_reports_lock_behind_highest_semver_release(tmp_path: Path) -> None:
    result = _run_check_latest(tmp_path, locked_release="3.0.7")

    assert result.returncode == 1
    assert "Highest Soperator release is '4.0.2', but lock is pinned to '3.0.7'." in (
        result.stderr
    )


def test_check_latest_continues_after_prerelease_only_page(tmp_path: Path) -> None:
    result = _run_check_latest_with_release_pages(
        tmp_path,
        locked_release="4.0.2",
        page_one_releases=[
            {"tag_name": "5.0.0-rc.1", "draft": False, "prerelease": True},
            {"tag_name": "6.0.0", "draft": True, "prerelease": False},
        ],
        page_two_releases=[
            {"tag_name": "4.0.2", "draft": False, "prerelease": False},
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "Pinned Soperator release '4.0.2' is the highest GitHub SemVer release." in (
        result.stdout
    )


def test_ci_preview_no_branch_requires_sync() -> None:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        [str(SCRIPT), "--ci-preview-no-branch"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "--ci-preview-no-branch can only be used with --sync in disposable CI previews."
        in result.stderr
    )
