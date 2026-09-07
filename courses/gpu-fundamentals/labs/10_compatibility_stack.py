"""Capture the framework, runtime, driver, compiler, and H100 compatibility stack."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def first_line(command: list[str]) -> str | None:
    """Return a bounded first output line for a public version command."""
    if shutil.which(command[0]) is None:
        return None
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=15, check=False
    )
    text = result.stdout.strip() or result.stderr.strip()
    return text.splitlines()[0][:240] if text else None


def nvcc_release() -> str | None:
    """Return nvcc's release/build line instead of its generic banner."""
    if shutil.which("nvcc") is None:
        return None
    result = subprocess.run(
        ["nvcc", "--version"], capture_output=True, text=True, timeout=15, check=False
    )
    for line in (result.stdout + result.stderr).splitlines():
        if re.search(r"\brelease\s+\d", line, flags=re.IGNORECASE):
            return line.strip()[:240]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    versions = {
        "framework": torch.__version__,
        "framework_cuda_runtime": torch.version.cuda,
        "driver_query": first_line(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]
        ),
        "cuda_compiler_release": nvcc_release(),
        "framework_compiled_arches": list(torch.cuda.get_arch_list()),
    }
    target = write_result(
        args,
        lab_id="10_compatibility_stack",
        environment=environment,
        measurements={
            "layers": versions,
            "framework_kernel_execution": "passed",
            "ptx_or_sass_image_path": (
                "not-proven-by-this-lab; inspect a retained profiler trace or "
                "cuobjdump output before claiming native SASS versus driver JIT"
            ),
        },
        correctness={
            "h100_contract": True,
            "framework_kernel_executed": bool(torch.ones(1, device="cuda").item() == 1),
        },
    )
    print(f"Wrote compatibility evidence: {target}")


if __name__ == "__main__":
    main()
