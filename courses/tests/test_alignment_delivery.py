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
    assert "umask 077" in before_submit
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


@pytest.mark.parametrize("supplied_run_id", [None, "123456789abc"])
@pytest.mark.parametrize("valid_response", [True, False])
def test_triton_launcher_passes_canonical_output_and_run_id(
    supplied_run_id, valid_response, tmp_path
):
    """Exercise the real shell handoff and real client, replacing only HTTP.

    The extracted commands cannot start Slurm or an engine. Artifacts stay in
    pytest's temporary directory; HTTP is replaced inside the child process.
    """
    source = (ROOT / "llm-inference/slurm/trtllm_triton.sbatch").read_text()
    identity = source[
        source.index("if [[ -z ${COURSE_RUN_ID:-} ]]; then") : source.index(
            'mkdir -p "${course_dir}/logs"'
        )
    ]
    invocation = source[
        source.index(
            '"${course_python}" "${course_dir}/labs/30_engine_profile.py"'
        ) : source.index("printf 'PASS: private Triton log")
    ]
    client_source = ROOT / "llm-inference/labs/30_engine_profile.py"
    client_harness = """
import os, runpy, sys
from pathlib import Path
source = Path(os.environ["TEST_CLIENT_SOURCE"])
sys.path.insert(0, str(source.parent))
sys.argv = sys.argv[1:]
namespace = runpy.run_path(str(source))
globals_ = namespace["main"].__globals__
globals_["request_bytes"] = lambda *args, **kwargs: b""
globals_["request_json"] = lambda *args, **kwargs: {
    "text_output": "fixture answer" if os.environ["TEST_VALID_RESPONSE"] == "1" else ""
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
        "TEST_VALID_RESPONSE": "1" if valid_response else "0",
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
    if not valid_response:
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
