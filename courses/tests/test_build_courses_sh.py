"""Exercise the single authoring entry point and its failure boundaries."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def wrapper(tmp_path):
    course_root = tmp_path / "course project with spaces"
    (course_root / "tools").mkdir(parents=True)
    script = course_root / "build-courses.sh"
    shutil.copy2(ROOT / script.name, script)
    builder = course_root / "tools/build_course_html.py"
    builder.write_text(
        "import json, os, sys\n"
        "with open(os.environ['BUILD_CALLS'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv) + '\\n')\n"
        "phase = 'CHECK_STATUS' if '--check' in sys.argv else 'BUILD_STATUS'\n"
        "status = int(os.environ.get(phase, '0'))\n"
        "if not status:\n"
        "    print('current 1.00 MB index.html' if phase == 'CHECK_STATUS' else 'built 1.00 MB index.html')\n"
        "    if '--no-summary' not in sys.argv:\n"
        "        print('Publication summary\\n  Per-file limit: 104.86 MB')\n"
        "sys.exit(status)\n"
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    (binaries / "cat").symlink_to("/bin/cat")
    calls = tmp_path / "calls.jsonl"

    def run(*args, **overrides):
        env = {
            **os.environ,
            "PATH": f"{binaries}:/usr/bin:/bin",
            "BUILD_CALLS": str(calls),
            "NO_COLOR": "",
            **overrides,
        }
        result = subprocess.run(
            ["/bin/bash", str(script), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        recorded = (
            [json.loads(line) for line in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )
        return result, recorded

    return run, builder, binaries


@pytest.mark.parametrize("args", [(), ("--",)])
def test_wrapper_builds_all_then_checks_from_another_directory(wrapper, args):
    run, builder, _ = wrapper
    result, calls = run(*args)
    assert result.returncode == 0, result.stderr
    assert calls == [[str(builder), "--no-summary"], [str(builder), "--check"]]
    assert result.stdout.splitlines() == [
        "built 1.00 MB index.html",
        "",
        "Checking...",
        "current 1.00 MB index.html",
        "Publication summary",
        "  Per-file limit: 104.86 MB",
    ]
    assert "\x1b" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "phase,status,count", [("BUILD_STATUS", 42, 1), ("CHECK_STATUS", 43, 2)]
)
def test_wrapper_preserves_failure_and_never_reports_success(
    wrapper, phase, status, count
):
    run, _, _ = wrapper
    result, calls = run(**{phase: str(status)})
    assert result.returncode == status
    assert len(calls) == count
    assert "rebuilt and verified" not in result.stdout
    assert "Publication summary" not in result.stdout
    assert "ERROR:" in result.stderr
    assert ("Checking..." in result.stdout) == (count == 2)


def test_help_needs_no_python_and_describes_results_archives(wrapper):
    run, _, binaries = wrapper
    (binaries / "python3").unlink()
    result, calls = run("--help", PATH=str(binaries))
    assert result.returncode == 0
    assert calls == []
    assert "check HTML\nand ZIPs against their sources" in result.stdout
    assert "lab-kit ZIPs are not generated" in result.stdout
    assert "decimal MB (1 MB = 1,000,000 bytes)" in result.stdout
    assert (
        "One final publication summary follows successful verification" in result.stdout
    )
    assert "including exceeded size limits, in red" in result.stdout


def test_invalid_argument_does_not_build(wrapper):
    run, _, _ = wrapper
    result, calls = run("unexpected-course")
    assert result.returncode == 2
    assert calls == []
