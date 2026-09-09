"""Allocation must use real sbatch directives and bind projected executable bytes."""

import copy

import pytest

from nebius_cxcli.soperator_checks_allocation import (
    SCRIPT_ANNOTATION,
    SCRIPT_VOLUME,
    allocation_script,
)
from nebius_cxcli.soperator_checks_contract import job_execution_digest


def test_allocation_preserves_native_body_and_overrides_earlier_directives():
    header = "#!/bin/bash\n#SBATCH --nodes=2\n#SBATCH --time=30\n\n"
    body = "set -euo pipefail\nsrun native-diagnostic --original-argument\n"
    result = allocation_script(header + body, worker="worker-1", gpu_count=8)
    assert (
        result
        == header
        + "#SBATCH --nodelist=worker-1\n#SBATCH --nodes=1\n#SBATCH --gpus-per-node=8\n"
        + body
    )


@pytest.mark.parametrize(
    "script",
    [
        "srun test\n",
        "#!/bin/bash\n",
        "#!/bin/bash\r\nsrun test\n",
        "#!/bin/bash\n#SBATCH hetjob\nsrun test\n",
        "#!/bin/bash\n" + "#x\n" * 23000 + "srun test\n",
    ],
)
def test_unsupported_script_fails_before_submission(script):
    with pytest.raises(RuntimeError):
        allocation_script(script, worker="worker-0", gpu_count=8)


@pytest.mark.parametrize(
    "worker", ["worker-0,worker-1", "worker-0\n#SBATCH --nodes=2", "$(hostname)"]
)
def test_worker_cannot_inject_an_allocation(worker):
    with pytest.raises(RuntimeError):
        allocation_script("#!/bin/bash\nsrun test\n", worker=worker, gpu_count=8)


def test_projected_script_content_is_executable_identity():
    job = {
        "spec": {
            "template": {
                "metadata": {"annotations": {SCRIPT_ANNOTATION: "#!/bin/bash\nsrun original\n"}},
                "spec": {"volumes": [copy.deepcopy(SCRIPT_VOLUME)]},
            }
        }
    }
    original = job_execution_digest(job)
    job["spec"]["template"]["metadata"]["annotations"][SCRIPT_ANNOTATION] += "echo altered\n"
    assert job_execution_digest(job) != original
    del job["spec"]["template"]["metadata"]["annotations"][SCRIPT_ANNOTATION]
    with pytest.raises(RuntimeError, match="annotation"):
        job_execution_digest(job)
