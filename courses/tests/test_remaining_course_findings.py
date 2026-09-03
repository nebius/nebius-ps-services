from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock


COURSES_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "a1b2c3d4e5f6"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def load_lab(relative_path: str, name: str) -> Iterator[Any]:
    path = COURSES_ROOT / relative_path
    previous_common = sys.modules.pop("common", None)
    sys.path.insert(0, str(path.parent))
    try:
        yield load_module(path, name)
    finally:
        sys.path.remove(str(path.parent))
        sys.modules.pop("common", None)
        if previous_common is not None:
            sys.modules["common"] = previous_common


class CanonicalRunIdentityTests(unittest.TestCase):
    common_modules = {
        "fundamentals": COURSES_ROOT / "gpu-fundamentals/labs/common.py",
        "optimizations": COURSES_ROOT / "gpu-optimizations/labs/common.py",
        "llm": COURSES_ROOT / "llm-training-inferencing/labs/common.py",
    }

    @staticmethod
    def args_for(output_dir: Path) -> SimpleNamespace:
        return SimpleNamespace(
            output_dir=output_dir,
            profile="smoke",
            seed=17,
            warmup=0,
            iterations=1,
        )

    def test_common_writers_use_one_public_safe_run_identity(self) -> None:
        for course, path in self.common_modules.items():
            with self.subTest(course=course), tempfile.TemporaryDirectory() as temp_dir:
                module = load_module(path, f"remaining_{course}_common")
                output_dir = Path(temp_dir) / "results"
                args = self.args_for(output_dir)
                with mock.patch.dict(
                    os.environ,
                    {"COURSE_RUN_ID": RUN_ID, "SLURM_JOB_ID": "123456"},
                    clear=False,
                ):
                    module.validate_common_args(args)
                    target = module.write_result(
                        args,
                        lab_id="remaining_test",
                        environment={},
                        measurements={},
                        correctness={"accepted": True},
                    )
                payload = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(target.name, f"remaining_test-run-{RUN_ID}.json")
                self.assertEqual(payload["schema"], "gpu-course-result/v1")
                self.assertEqual(payload["run_id"], RUN_ID)
                self.assertNotIn("slurm_job_id", payload)
                self.assertNotIn("123456", target.name)

    def test_invalid_external_run_identity_fails_before_writing(self) -> None:
        for course, path in self.common_modules.items():
            with self.subTest(course=course), tempfile.TemporaryDirectory() as temp_dir:
                module = load_module(path, f"remaining_{course}_invalid_run")
                output_dir = Path(temp_dir) / "results"
                args = self.args_for(output_dir)
                with mock.patch.dict(
                    os.environ, {"COURSE_RUN_ID": "not-valid"}, clear=False
                ):
                    with self.assertRaisesRegex(SystemExit, "COURSE_RUN_ID"):
                        module.validate_common_args(args)
                self.assertFalse(output_dir.exists())

    def test_generated_run_identity_has_the_canonical_shape(self) -> None:
        for course, path in self.common_modules.items():
            with self.subTest(course=course):
                module = load_module(path, f"remaining_{course}_generated_run")
                with mock.patch.dict(os.environ, {}, clear=True):
                    first = module.resolve_run_id()
                    second = module.resolve_run_id()
                self.assertRegex(first, r"^[0-9a-f]{12}$")
                self.assertRegex(second, r"^[0-9a-f]{12}$")
                self.assertNotEqual(first, second)

    def test_llm_application_artifacts_do_not_use_scheduler_identity(self) -> None:
        labs = COURSES_ROOT / "llm-training-inferencing/labs"
        for filename in (
            "01_tiny_transformer_train.py",
            "07_grpo_trainer.py",
            "11_serving_client.py",
            "15_streaming_client.py",
            "20_prefix_cache_client.py",
        ):
            with self.subTest(lab=filename):
                source = (labs / filename).read_text(encoding="utf-8")
                self.assertNotIn('os.environ.get("SLURM_JOB_ID")', source)
                self.assertNotIn('"slurm_job_id"', source)
                self.assertIn("run_id", source)

    def test_vllm_launchers_share_course_run_identity(self) -> None:
        launchers = COURSES_ROOT / "llm-training-inferencing/slurm"
        for path in sorted(launchers.glob("vllm_*.sbatch")):
            with self.subTest(launcher=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("COURSE_RUN_ID", source)
                for line in source.splitlines():
                    if any(
                        marker in line
                        for marker in ("server_log=", "--output", "metrics-run-")
                    ):
                        self.assertNotIn("SLURM_JOB_ID", line)


class RemainingEvidenceContractTests(unittest.TestCase):
    def test_layout_break_even_calculation(self) -> None:
        with load_lab(
            "gpu-fundamentals/labs/04_layout_and_coalescing.py",
            "remaining_layout",
        ) as module:
            self.assertEqual(module.break_even_reuses(2.0, 3.0, 2.2), 3)
            self.assertEqual(module.break_even_reuses(1.0, 3.0, 2.0), 1)
            self.assertIsNone(module.break_even_reuses(1.0, 2.0, 2.0))
            self.assertIsNone(module.break_even_reuses(1.0, 1.5, 2.0))

    def test_layout_lab_emits_promised_evidence(self) -> None:
        source = (
            COURSES_ROOT / "gpu-fundamentals/labs/04_layout_and_coalescing.py"
        ).read_text(encoding="utf-8")
        for marker in (
            '"layouts"',
            '"useful_bandwidth_gib_per_s"',
            '"repack_copy"',
            '"break_even_reuses"',
        ):
            self.assertIn(marker, source)

    def test_input_pipeline_emits_each_measurement_boundary(self) -> None:
        source = (
            COURSES_ROOT / "gpu-optimizations/labs/05_input_pipeline.py"
        ).read_text(encoding="utf-8")
        for marker in (
            '"batch_ready_gap"',
            '"h2d"',
            '"device_consumption"',
            '"end_to_end"',
            '"host_batch_bytes"',
            '"all_batches_pinned"',
        ):
            self.assertIn(marker, source)

    def test_checkpointing_lab_emits_gradient_equivalence(self) -> None:
        source = (
            COURSES_ROOT / "gpu-optimizations/labs/06_activation_checkpointing.py"
        ).read_text(encoding="utf-8")
        for marker in (
            '"correctness_probe"',
            '"max_abs_gradient_error"',
            '"relative_l2_gradient_error"',
            '"gradients_allclose"',
        ):
            self.assertIn(marker, source)

    def test_tiny_training_emits_gradient_update_and_memory_evidence(self) -> None:
        source = (
            COURSES_ROOT / "llm-training-inferencing/labs/01_tiny_transformer_train.py"
        ).read_text(encoding="utf-8")
        for marker in (
            '"gradient_norm"',
            '"baseline_allocated_bytes"',
            '"peak_allocated_bytes"',
            '"parameter_updated"',
            "error_if_nonfinite=True",
        ):
            self.assertIn(marker, source)


class CachedGenerationContractTests(unittest.TestCase):
    def test_generation_schedule_counts_prefill_and_decode_exactly(self) -> None:
        with load_lab(
            "llm-training-inferencing/labs/09_hf_prefill_decode.py",
            "remaining_hf_decode",
        ) as module:
            steps = module.generation_schedule(prompt_tokens=7, new_tokens=4)
            self.assertEqual(len(steps), 4)
            self.assertEqual(steps[0].phase, "prefill")
            self.assertEqual(steps[0].input_tokens, 7)
            self.assertEqual(steps[0].attention_tokens, 7)
            self.assertEqual(steps[0].cache_start, 0)
            self.assertEqual(
                [step.phase for step in steps[1:]], ["decode", "decode", "decode"]
            )
            self.assertEqual([step.attention_tokens for step in steps[1:]], [8, 9, 10])
            self.assertEqual([step.cache_start for step in steps[1:]], [7, 8, 9])

    def test_one_generated_token_has_no_decode_step(self) -> None:
        with load_lab(
            "llm-training-inferencing/labs/09_hf_prefill_decode.py",
            "remaining_hf_single_token",
        ) as module:
            steps = module.generation_schedule(prompt_tokens=5, new_tokens=1)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0].phase, "prefill")

    def test_zero_new_tokens_fails_before_torch_import(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(
                    COURSES_ROOT
                    / "llm-training-inferencing/labs/09_hf_prefill_decode.py"
                ),
                "--new-tokens",
                "0",
            ],
            cwd=COURSES_ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--new-tokens", completed.stderr)
        self.assertNotIn("PyTorch is required", completed.stderr)


class VllmAggregationContractTests(unittest.TestCase):
    @staticmethod
    def request(
        prompt_tokens: int, output_tokens: int, finish_reason: str = "length"
    ) -> Any:
        completion = SimpleNamespace(
            token_ids=list(range(output_tokens)), finish_reason=finish_reason
        )
        return SimpleNamespace(
            prompt_token_ids=list(range(prompt_tokens)), outputs=[completion]
        )

    def test_vllm_aggregation_counts_every_request(self) -> None:
        with load_lab(
            "llm-training-inferencing/labs/10_vllm_offline.py",
            "remaining_vllm_offline",
        ) as module:
            aggregate = module.aggregate_outputs(
                [self.request(3, 2), self.request(5, 4)], max_tokens=8
            )
            self.assertEqual(aggregate["request_count"], 2)
            self.assertEqual(aggregate["prompt_tokens_total"], 8)
            self.assertEqual(aggregate["output_tokens_total"], 6)
            self.assertEqual(aggregate["prompt_tokens_per_request"], [3, 5])
            self.assertEqual(aggregate["output_tokens_per_request"], [2, 4])

    def test_vllm_aggregation_rejects_invalid_outputs(self) -> None:
        with load_lab(
            "llm-training-inferencing/labs/10_vllm_offline.py",
            "remaining_vllm_invalid",
        ) as module:
            with self.assertRaisesRegex(ValueError, "prompt token"):
                module.aggregate_outputs(
                    [SimpleNamespace(prompt_token_ids=None, outputs=[])], max_tokens=8
                )
            with self.assertRaisesRegex(ValueError, "exactly one completion"):
                module.aggregate_outputs(
                    [
                        SimpleNamespace(
                            prompt_token_ids=[1],
                            outputs=[
                                SimpleNamespace(token_ids=[1], finish_reason="stop"),
                                SimpleNamespace(token_ids=[2], finish_reason="stop"),
                            ],
                        )
                    ],
                    max_tokens=8,
                )
            with self.assertRaisesRegex(ValueError, "output token"):
                module.aggregate_outputs([self.request(1, 9)], max_tokens=8)


class ValidatorTraversalContractTests(unittest.TestCase):
    validators = (
        "gpu-fundamentals/tools/validate_course.py",
        "gpu-optimizations/tools/validate_course.py",
        "llm-training-inferencing/tools/validate_course.py",
    )

    def test_local_virtual_environments_are_excluded_from_publication(self) -> None:
        for index, relative_path in enumerate(self.validators):
            with (
                self.subTest(validator=relative_path),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                local_cache = (
                    root
                    / ".venv-test/lib/python3.12/site-packages/example/__pycache__/module.pyc"
                )
                course_cache = root / "labs/__pycache__/lab.pyc"
                local_cache.parent.mkdir(parents=True)
                course_cache.parent.mkdir(parents=True)
                local_cache.touch()
                course_cache.touch()
                module = load_module(
                    COURSES_ROOT / relative_path,
                    f"remaining_validator_traversal_{index}",
                )
                with mock.patch.object(module, "ROOT", root):
                    paths = set(module.iter_course_paths())
                self.assertNotIn(local_cache, paths)
                self.assertIn(course_cache, paths)

    def test_official_references_require_https(self) -> None:
        for index, relative_path in enumerate(self.validators):
            with self.subTest(validator=relative_path):
                module = load_module(
                    COURSES_ROOT / relative_path,
                    f"remaining_validator_references_{index}",
                )
                self.assertTrue(
                    module.is_official_reference("https://docs.nvidia.com/example")
                )
                self.assertFalse(
                    module.is_official_reference("http://docs.nvidia.com/example")
                )


class StaticCourseContractTests(unittest.TestCase):
    def test_all_embedded_listings_remain_exact(self) -> None:
        for course in (
            "gpu-fundamentals",
            "gpu-optimizations",
            "llm-training-inferencing",
        ):
            with self.subTest(course=course):
                completed = subprocess.run(
                    [sys.executable, "tools/validate_course.py"],
                    cwd=COURSES_ROOT / course,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_no_portable_schema_mentions_scheduler_identity(self) -> None:
        pattern = re.compile(r'"slurm_job_id"')
        for path in COURSES_ROOT.glob("*/labs/*.py"):
            with self.subTest(path=path):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
