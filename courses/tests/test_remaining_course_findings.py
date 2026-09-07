"""Static, visual, source-parity, and pure-helper tests for all courses."""

from __future__ import annotations

import importlib.util
import html
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_course_review_fixes import publication_files

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
)


def test_pytorch_214_is_the_ordinary_environment_authority() -> None:
    manifests = (
        ROOT / "gpu-fundamentals/requirements.txt",
        ROOT / "gpu-optimizations/requirements.txt",
        ROOT / "llm-training/requirements.txt",
        ROOT / "llm-inference/requirements-mechanics.txt",
    )
    for manifest in manifests:
        text = manifest.read_text()
        assert "torch==2.14.0" in text
        assert "torch==2.13" not in text


def load_pure_module(relative: str):
    path = ROOT / relative
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("common", None)
        spec = importlib.util.spec_from_file_location(
            f"pure_{path.stem}_{id(path)}", path
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))
        sys.modules.pop("common", None)


@pytest.mark.parametrize("course", COURSES)
def test_course_validator_passes(course: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / course / "tools/validate_course.py")],
        cwd=ROOT / course,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout


def test_root_catalog_order_and_prerequisites() -> None:
    document = (ROOT / "README.md").read_text()
    positions = [document.index(f"]({course}/README.md)") for course in COURSES]
    assert positions == sorted(positions)
    assert "Fundamentals and Optimizations are prerequisites" in document
    assert "not prerequisites for Custom CUDA" in document


@pytest.mark.parametrize("course", COURSES)
def test_course_has_accessible_visual_topic_plan(course: str) -> None:
    visual = (ROOT / course / "reference/visual-plan.md").read_text()
    assert len(re.findall(r"^\| [^|-]", visual, re.MULTILINE)) >= 7
    assert "(visual-manifest.json)" in visual


@pytest.mark.parametrize("course", COURSES)
def test_html_embeds_every_numbered_lab_exactly(course: str) -> None:
    root = ROOT / course
    document = (root / "index.html").read_text()
    sources = sorted(
        path
        for path in (root / "labs").glob("[0-9][0-9]_*.*")
        if path.suffix in {".py", ".cu", ".cpp"}
    )
    assert sources
    for source in sources:
        relative = source.relative_to(root).as_posix()
        assert f'data-source="{relative}"' in document
        escaped = html.escape(source.read_text())
        assert escaped in document


@pytest.mark.parametrize("course", COURSES)
def test_html_diagrams_are_accessible_and_responsive(course: str) -> None:
    document = (ROOT / course / "index.html").read_text()
    assert document.count('role="img"') >= 6
    assert document.count("<title id=") == document.count('role="img"')
    assert document.count("<desc id=") == document.count('role="img"')
    assert 'name="viewport"' in document
    assert re.search(r"overflow(?:-x)?:\s*auto", document)
    assert re.search(r"@media\s*\(max-width:\s*600px\)", document)
    kinds = set(re.findall(r'data-diagram-kind="([a-z]+)"', document))
    assert len(kinds) >= 3


def test_semantic_diagram_layouts_match_named_relationships() -> None:
    fundamentals = (ROOT / "gpu-fundamentals/index.html").read_text()
    inference = (ROOT / "llm-inference/index.html").read_text()
    assert 'data-diagram-kind="roofline"><svg' in fundamentals
    assert 'data-diagram-kind="comparison"><svg' in fundamentals
    assert 'data-diagram-kind="matrix"><svg' in inference
    assert 'data-diagram-kind="topology"><svg' in inference


def test_pure_scheduling_and_capacity_helpers() -> None:
    scheduler = load_pure_module("gpu-fundamentals/labs/11_scheduler_tail.py")
    assert scheduler.wave_model(133, 132) == {
        "blocks": 133,
        "waves": 2,
        "tail_blocks": 1,
        "tail_utilization": round(1 / 132, 4),
    }
    packing = load_pure_module("llm-training/labs/25_sequence_packing.py")
    bins = packing.first_fit_bins([8, 8, 4, 4], 12)
    assert sorted(sum(bins, [])) == [4, 4, 8, 8]
    assert all(sum(group) <= 12 for group in bins)
    capacity = load_pure_module("llm-inference/labs/26_kv_capacity.py")
    assert capacity.kv_bytes_per_token(32, 8, 128, 2) == 131072
    paged = load_pure_module("llm-inference/labs/27_paged_kv.py")
    result = paged.allocate([17, 31], 16, 4)
    assert result["required_blocks"] == 4
    assert result["internal_waste_tokens"] == 16
    batching = load_pure_module("llm-inference/labs/28_continuous_batching.py")
    assert batching.simulate([8, 16], [2, 2], 4, 8)["scheduled_tokens"] == 28


def test_evidence_lanes_remain_separate_and_pending() -> None:
    for course in COURSES:
        publication = (ROOT / course / "PUBLICATION-REVIEW.md").read_text()
        assert "Source and static" in publication
        assert "Installed" in publication
        assert "Runtime activation" in publication
        assert "Live H100" in publication
        assert "pending" in publication.lower()


def test_only_official_https_references_are_published() -> None:
    allowed = {
        "docs.nvidia.com",
        "developer.nvidia.com",
        "docs.pytorch.org",
        "pytorch.org",
        "docs.vllm.ai",
        "nvidia.github.io",
        "huggingface.co",
        "slurm.schedmd.com",
        "cmake.org",
        "www.lmsys.org",
    }
    from urllib.parse import urlparse

    for course in COURSES:
        text = (ROOT / course / "RESOURCES.md").read_text()
        urls = re.findall(r"\((https://[^)]+)\)", text)
        assert urls
        assert all(
            urlparse(url).hostname in allowed
            or url.startswith("https://github.com/NVIDIA/")
            or url.startswith("https://github.com/triton-inference-server/")
            for url in urls
        )


def test_no_obsolete_course_name_in_any_publication_surface() -> None:
    retired_name = "llm-training-" + "inferencing"
    for course in COURSES:
        for path in publication_files(
            ROOT / course, {".py", ".md", ".html", ".sbatch", ".txt"}
        ):
            document = path.read_text(errors="ignore")
            assert retired_name not in document, path
