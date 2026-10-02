"""Regressions for learner entry gates and launcher-to-client evidence wiring."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest
from test_course_review_fixes import COURSES, ROOT


@pytest.mark.parametrize("course", COURSES)
def test_readme_gates_live_submission_before_first_job(course):
    document = (ROOT / course / "README.md").read_text()
    before_submit = document[: document.index("sbatch ")]
    assert "../README.md#how-to-set-up-the-lab" in before_submit
    assert "umask 077" not in document
    assert "VERSIONS.md" in before_submit
    assert "qualified" in before_submit or "approved" in before_submit
    for block in re.findall(r"```bash\n(.*?)```", document, re.S):
        assert not ("pip install" in block and "sbatch " in block)


@pytest.mark.parametrize("course", COURSES)
def test_readme_closing_invitation_has_no_stray_comma(course):
    document = (ROOT / course / "README.md").read_text()
    assert "for optional,\nreading" not in document
    assert "for optional reading" in " ".join(document.split())


def test_cuda_readme_limits_direct_build_to_allocated_qualified_environment():
    document = (ROOT / "custom-cuda-kernels/README.md").read_text()
    before_build = document[: document.index("cmake -S")]
    assert "allocated H100" in before_build
    assert "qualified" in before_build


@pytest.mark.parametrize("tool", ["memcheck", "racecheck", "initcheck", "synccheck"])
@pytest.mark.parametrize("cpus", ["4", "0", None])
def test_sanitizer_launcher_bounds_racecheck_without_filtering_work(
    tool, cpus, tmp_path
):
    """Run the real launcher with only Slurm execution replaced by an argv spy."""
    capture = tmp_path / "argv.json"
    srun = tmp_path / "srun"
    srun.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['TEST_ARGV']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    srun.chmod(0o700)
    runner = tmp_path / "container-runner"
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o700)
    image = "docker://example.invalid/cuda@sha256:" + "a" * 64
    environment = {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "SLURM_JOB_ID": "17",
        "CUDA_IMAGE_DIGEST": image,
        "COURSE_CONTAINER_RUNNER": str(runner),
        "TEST_ARGV": str(capture),
    }
    if cpus is not None:
        environment["SLURM_CPUS_PER_TASK"] = cpus
    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "custom-cuda-kernels/slurm/sanitizer.sbatch"),
            tool,
            "/qualified/03_tiled_transpose",
            "--profile", "small",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if tool == "racecheck" and cpus != "4":
        assert completed.returncode != 0
        assert not capture.exists()
        return
    assert completed.returncode == 0, completed.stderr
    expected = [
        "--ntasks=1",
        "--gpus-per-task=1",
        str(runner),
        image,
        "compute-sanitizer",
        "--tool",
        tool,
        "--error-exitcode=3",
    ]
    if tool == "racecheck":
        expected += [
            "--racecheck-num-workers",
            "4",
            "--force-synchronization-limit",
            "2",
        ]
    expected += ["/qualified/03_tiled_transpose", "--profile", "small"]
    assert json.loads(capture.read_text()) == expected


@pytest.mark.parametrize("supplied_run_id", [None, "123456789abc"])
@pytest.mark.parametrize(
    "response_text",
    ["fixture answer", "", None, True, 1, ["answer"], {"text": "answer"}],
)
def test_triton_launcher_passes_canonical_output_and_run_id(
    supplied_run_id, response_text, tmp_path
):
    """Exercise the real shell handoff and real client, replacing only HTTP.

    The extracted commands cannot start Slurm or an engine. Artifacts stay in
    pytest's temporary directory; HTTP is replaced inside the child process.
    """
    source = (ROOT / "llm-inference/slurm/trtllm_triton.sbatch").read_text()
    identity = source[
        source.index("if [[ -z ${COURSE_RUN_ID:-} ]]; then") : source.index(
            'mkdir -p "${course_dir}/results/30_engine_profile/logs"'
        )
    ]
    invocation = source[
        source.index(
            '"${course_python}" "${course_dir}/labs/30_engine_profile.py"'
        ) : source.index("printf 'PASS: private Triton log")
    ]
    client_source = ROOT / "llm-inference/labs/30_engine_profile.py"
    client_harness = """
import json, os, runpy, sys
from pathlib import Path
source = Path(os.environ["TEST_CLIENT_SOURCE"])
sys.path.insert(0, str(source.parent))
sys.argv = sys.argv[1:]
namespace = runpy.run_path(str(source))
globals_ = namespace["main"].__globals__
globals_["request_bytes"] = lambda *args, **kwargs: b""
globals_["request_json"] = lambda *args, **kwargs: {
    "text_output": json.loads(os.environ["TEST_RESPONSE_TEXT"])
}
namespace["main"]()
"""
    harness = """
set -eu
client_python() {
  if [[ ${1:-} == -c ]]; then
    "${TEST_PYTHON}" "$@"
  else
    "${TEST_PYTHON}" -c "${TEST_CLIENT_HARNESS}" "$@"
  fi
}
course_python=client_python
course_dir=${TEST_COURSE_DIR}
http_port=20000
model=tensorrt_llm
profile=llmapi
max_token_field=sampling_param_max_tokens
"""
    environment = {
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEST_PYTHON": sys.executable,
        "TEST_CLIENT_SOURCE": str(client_source),
        "TEST_CLIENT_HARNESS": client_harness,
        "TEST_COURSE_DIR": str(tmp_path),
        "TEST_RESPONSE_TEXT": json.dumps(response_text),
    }
    if supplied_run_id:
        environment["COURSE_RUN_ID"] = supplied_run_id
    completed = subprocess.run(
        [
            "bash",
            "-c",
            harness
            + identity
            + '\nprintf "SERVER_RUN_ID=%s\\n" "${COURSE_RUN_ID}"\n'
            + invocation,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    run_id = re.search(r"SERVER_RUN_ID=([0-9a-f]{12})", completed.stdout)[1]
    if supplied_run_id:
        assert run_id == supplied_run_id
    output = tmp_path / "results" / f"30_engine_profile-run-{run_id}.json"
    if not isinstance(response_text, str) or not response_text:
        assert completed.returncode != 0
        assert "did not contain generated text" in completed.stderr
        assert not list(tmp_path.rglob("*.json"))
        return
    assert completed.returncode == 0, completed.stderr
    assert output.is_file(), (
        "client output must use the server's run ID and results directory"
    )
    payload = json.loads(output.read_text())
    assert payload["run_id"] == run_id
    assert payload["correctness"]["response_has_generated_text"] is True
    assert output.stat().st_mode & 0o777 == 0o600
    assert "fixture answer" not in output.read_text()
    assert "--output-dir " in invocation
    assert "--output " not in invocation


@pytest.mark.parametrize("supplied_run_id", [None, "123456789abc"])
def test_chunked_launcher_shares_run_id_with_both_policy_clients(
    supplied_run_id, tmp_path
):
    """Execute the real identity handoff and clients with fixture HTTP only."""
    source = (ROOT / "llm-inference/slurm/vllm_chunked_prefill_ab.sbatch").read_text()
    identity = source[
        source.index("if [[ -z ${COURSE_RUN_ID:-} ]]; then") : source.index(
            'mkdir -p "${course_dir}/results/34_policy_equivalence_client/logs"'
        )
    ]
    invocation = source.split("  if [[ ${phase} == probe ]]; then\n", 1)[1].split(
        "\n  else\n", 1
    )[0]
    client_harness = """
import io, json, os, runpy, sys
from pathlib import Path
source = Path(os.environ["TEST_CLIENT_SOURCE"])
sys.path.insert(0, str(source.parent))
sys.argv = sys.argv[1:]
namespace = runpy.run_path(str(source))
namespace["main"].__globals__["urllib"].request.urlopen = lambda *a, **k: io.BytesIO(
    json.dumps({"choices": [{"text": "fixture answer"}]}).encode()
)
namespace["main"]()
"""
    environment = {
        "PATH": os.defpath,
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEST_PYTHON": sys.executable,
        "TEST_CLIENT_SOURCE": str(
            ROOT / "llm-inference/labs/34_policy_equivalence_client.py"
        ),
        "TEST_CLIENT_HARNESS": client_harness,
        "TEST_COURSE_DIR": str(tmp_path),
    }
    if supplied_run_id:
        environment["COURSE_RUN_ID"] = supplied_run_id
    harness = """
set -eu
umask 077
client_python() {
  if [[ ${1:-} == -c ]]; then
    "${TEST_PYTHON}" "$@"
  else
    "${TEST_PYTHON}" -c "${TEST_CLIENT_HARNESS}" "$@"
  fi
}
course_python=client_python
course_dir=${TEST_COURSE_DIR}
port=20000
model=fixture
revision=1234567890123456789012345678901234567890
trial=1
"""
    completed = subprocess.run(
        [
            "bash",
            "-c",
            harness
            + identity
            + '\nprintf "SERVER_RUN_ID=%s\\n" "${COURSE_RUN_ID}"\n'
            + 'mkdir -p "${base_dir}"\n'
            + "for variant in disabled enabled; do\n"
            + invocation
            + "\ndone\n",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    run_id = re.search(r"SERVER_RUN_ID=([0-9a-f]{12})", completed.stdout)[1]
    if supplied_run_id:
        assert run_id == supplied_run_id
    base = tmp_path / "results" / f"chunked-prefill-ab-run-{run_id}"
    for variant in ("disabled", "enabled"):
        output = base / f"{variant}-trial-1-digests.json"
        payload = json.loads(output.read_text())
        assert payload["run_id"] == run_id
        assert payload["measurements"]["variant"] == variant
        assert len(payload["measurements"]["response_digests"]) == 4
        assert output.stat().st_mode & 0o777 == 0o600
        assert "fixture answer" not in output.read_text()
