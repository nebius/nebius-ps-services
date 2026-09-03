from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


COURSES_ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResultContractTests(unittest.TestCase):
    common_modules = {
        "fundamentals": COURSES_ROOT / "gpu-fundamentals/labs/common.py",
        "optimizations": COURSES_ROOT / "gpu-optimizations/labs/common.py",
        "llm": COURSES_ROOT / "llm-training-inferencing/labs/common.py",
    }

    def args_for(self, output_dir: Path) -> SimpleNamespace:
        return SimpleNamespace(
            output_dir=output_dir,
            profile="smoke",
            run_id="a1b2c3d4e5f6",
            seed=17,
        )

    def test_literal_false_correctness_is_rejected_before_writing(self) -> None:
        for name, path in self.common_modules.items():
            with self.subTest(course=name), tempfile.TemporaryDirectory() as temp_dir:
                module = load_module(path, f"review_{name}_common_false")
                output_dir = Path(temp_dir) / "results"
                with self.assertRaisesRegex(SystemExit, "correctness"):
                    module.write_result(
                        self.args_for(output_dir),
                        lab_id="review_test",
                        environment={},
                        measurements={},
                        correctness={"accepted": False},
                    )
                self.assertFalse(output_dir.exists())

    def test_fundamentals_and_llm_results_are_private(self) -> None:
        for name in ("fundamentals", "llm"):
            with self.subTest(course=name), tempfile.TemporaryDirectory() as temp_dir:
                module = load_module(
                    self.common_modules[name], f"review_{name}_common_permissions"
                )
                output_dir = Path(temp_dir) / "results"
                old_umask = os.umask(0o022)
                try:
                    target = module.write_result(
                        self.args_for(output_dir),
                        lab_id="review_test",
                        environment={},
                        measurements={},
                        correctness={"accepted": True},
                    )
                finally:
                    os.umask(old_umask)
                self.assertEqual(stat.S_IMODE(output_dir.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_serialization_failure_leaves_no_reserved_result(self) -> None:
        for name, path in self.common_modules.items():
            with self.subTest(course=name), tempfile.TemporaryDirectory() as temp_dir:
                module = load_module(path, f"review_{name}_common_serialization")
                output_dir = Path(temp_dir) / "results"
                with self.assertRaises(TypeError):
                    module.write_result(
                        self.args_for(output_dir),
                        lab_id="review_test",
                        environment={},
                        measurements={"not_json": object()},
                        correctness={"accepted": True},
                    )
                self.assertEqual(list(output_dir.glob("*.json")), [])

    def test_llm_direct_writers_use_the_exclusive_helper(self) -> None:
        labs = COURSES_ROOT / "llm-training-inferencing/labs"
        for filename in (
            "11_serving_client.py",
            "15_streaming_client.py",
            "20_prefix_cache_client.py",
        ):
            with self.subTest(lab=filename):
                source = (labs / filename).read_text(encoding="utf-8")
                self.assertIn("write_json_exclusive", source)
                self.assertNotIn("output.write_text(", source)

    def test_llm_checkpoint_uses_exclusive_private_creation(self) -> None:
        source = (
            COURSES_ROOT / "llm-training-inferencing/labs/01_tiny_transformer_train.py"
        ).read_text(encoding="utf-8")
        self.assertIn("open_private_exclusive", source)
        self.assertIn("torch.save(", source)


class IntegerOverrideTests(unittest.TestCase):
    cases = (
        ("gpu-fundamentals/labs/02_tensor_core_precision.py", "--matrix-size"),
        ("gpu-fundamentals/labs/03_transfer_and_pinning.py", "--size-mib"),
        ("gpu-fundamentals/labs/06_distributed_collectives.py", "--payload-mib"),
        ("gpu-optimizations/labs/02_sync_trap.py", "--steps"),
        ("gpu-optimizations/labs/05_input_pipeline.py", "--batches"),
        ("gpu-optimizations/labs/08_distributed_scaling.py", "--global-batch"),
    )

    def test_zero_is_rejected_before_gpu_initialization(self) -> None:
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        for relative_path, option in self.cases:
            with self.subTest(lab=relative_path):
                completed = subprocess.run(
                    [sys.executable, str(COURSES_ROOT / relative_path), option, "0"],
                    cwd=COURSES_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(option, completed.stderr)


class LauncherContractTests(unittest.TestCase):
    llm_slurm = COURSES_ROOT / "llm-training-inferencing/slurm"
    vllm_launchers = (
        "vllm_benchmark.sbatch",
        "vllm_streaming_benchmark.sbatch",
        "vllm_prefix_cache.sbatch",
        "vllm_two_node.sbatch",
    )

    def test_launchers_create_private_no_clobber_artifacts(self) -> None:
        launchers = tuple((COURSES_ROOT / "gpu-fundamentals/slurm").glob("*.sbatch"))
        launchers += tuple(self.llm_slurm.glob("*.sbatch"))
        for path in launchers:
            with self.subTest(launcher=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("umask 077", source)
                self.assertIn("PYTHONDONTWRITEBYTECODE=1", source)
                if path.name.startswith("vllm_"):
                    self.assertRegex(source, r"set -o noclobber|set -[^\n]*C")

    def test_vllm_signal_handlers_exit_and_cleanup_is_bounded(self) -> None:
        for filename in self.vllm_launchers:
            with self.subTest(launcher=filename):
                source = (self.llm_slurm / filename).read_text(encoding="utf-8")
                start_match = re.search(r"(?m)^(?:cleanup|stop_server)\(\) \{", source)
                self.assertIsNotNone(start_match)
                assert start_match is not None
                markers = [
                    position
                    for marker in ("\nready=0", "\nfor variant in")
                    if (position := source.find(marker, start_match.start())) != -1
                ]
                self.assertTrue(markers)
                block = source[start_match.start() : min(markers)]
                self.assertIn("kill -KILL", block)
                script = "\n".join(
                    (
                        "set -eu",
                        "server_pid=''",
                        "server_step_pid=''",
                        block,
                        'kill -TERM "$$"',
                        "printf 'EXECUTION_CONTINUED\\n'",
                    )
                )
                completed = subprocess.run(
                    ["bash"],
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(completed.returncode, 143)
                self.assertNotIn("EXECUTION_CONTINUED", completed.stdout)


if __name__ == "__main__":
    unittest.main()
