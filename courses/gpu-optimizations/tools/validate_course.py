"""Run offline static and publication checks for a portable GPU course."""

from __future__ import annotations

import html.parser
import html
import os
import py_compile
import re
import subprocess
import tempfile
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOSTS = {
    "docs.nvidia.com",
    "developer.nvidia.com",
    "docs.pytorch.org",
    "pytorch.org",
    "docs.vllm.ai",
    "huggingface.co",
    "slurm.schedmd.com",
    "www.nvidia.com",
    "mlcommons.org",
}


def is_official_reference(href: str) -> bool:
    parsed = urllib.parse.urlparse(href)
    if parsed.scheme != "https":
        return False
    official_github_prefixes = (
        "https://github.com/NVIDIA/",
        "https://github.com/triton-inference-server/",
    )
    return parsed.hostname in ALLOWED_HOSTS or href.startswith(official_github_prefixes)


def iter_course_paths() -> Iterator[Path]:
    """Yield publishable course paths without descending into local environments."""
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            name for name in sorted(dirnames) if not name.startswith(".venv")
        ]
        base = Path(directory)
        yield from (base / name for name in dirnames)
        yield from (base / name for name in sorted(filenames))


class CourseParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.fragments: list[str] = []
        self.errors: list[str] = []
        self.reference_depth = 0
        self.details_source: str | None = None
        self.capture_source: str | None = None
        self.capture: list[str] = []
        self.embedded: dict[str, str] = {}
        self.lesson_count = 0
        self.lesson_depth = 0
        self.current_lesson_features: set[str] = set()
        self.lesson_feature_sets: list[set[str]] = []
        self.lab_count = 0
        self.complete_lab_guides = 0
        self.lab_depth = 0
        self.current_lab_text: list[str] = []
        self.lab_texts: list[str] = []
        self.official_links = 0
        self.external_links: list[str] = []
        self.local_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "section" and "lesson" in classes:
            self.lesson_count += 1
            self.lesson_depth = 1
            self.current_lesson_features = set()
        elif tag == "section" and self.lesson_depth:
            self.lesson_depth += 1
        for feature in REQUIRED_LESSON_FEATURES:
            if feature in classes and self.lesson_depth:
                self.current_lesson_features.add(feature)
        if tag == "article" and "lab" in classes:
            self.lab_count += 1
            self.lab_depth = 1
            self.current_lab_text = []
            if values.get("data-lab-guide") == "complete":
                self.complete_lab_guides += 1
        elif tag == "article" and self.lab_depth:
            self.lab_depth += 1
        element_id = values.get("id")
        if element_id:
            self.ids.append(element_id)
        if tag == "section" and element_id == "official-references":
            self.reference_depth += 1
        if tag in {"script", "iframe", "object", "embed", "form"}:
            self.errors.append(f"{tag} elements are not allowed")
        if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            self.errors.append("meta refresh is not allowed")
        if any(name.lower().startswith("on") for name, _ in attrs):
            self.errors.append(f"inline event handler is not allowed on {tag}")
        inline_style = values.get("style") or ""
        if re.search(r"(?:@import\b|url\s*\()", inline_style, re.IGNORECASE):
            self.errors.append(f"remote-capable inline style is not allowed on {tag}")
        href = values.get("href")
        if href and href.startswith("#"):
            self.fragments.append(href[1:])
        href_scheme = urllib.parse.urlparse(href).scheme.lower() if href else ""
        if href_scheme in {"javascript", "data", "file", "vbscript"}:
            self.errors.append(f"unsafe link scheme: {href_scheme}")
        if href and not href_scheme and not href.startswith("#"):
            self.local_links.append(href)
        if href and href_scheme in {"http", "https"}:
            self.official_links += 1
            self.external_links.append(href)
            if self.reference_depth == 0:
                self.errors.append("external link outside final official references")
            if not is_official_reference(href):
                self.errors.append(
                    f"non-official reference host: {urllib.parse.urlparse(href).hostname}"
                )
        source = values.get("src")
        if source and not re.match(
            r"^data:image/(?:png|jpeg|gif|webp);base64,", source, re.IGNORECASE
        ):
            self.errors.append("asset source must be an embedded raster image")
        if tag == "details" and "lab-source" in (values.get("class") or "").split():
            self.details_source = values.get("data-source")
        if tag == "code" and self.details_source:
            self.capture_source = self.details_source
            self.capture = []

    def handle_data(self, data: str) -> None:
        if self.capture_source:
            self.capture.append(data)
        if self.lab_depth:
            self.current_lab_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "code" and self.capture_source:
            self.embedded[self.capture_source] = "".join(self.capture)
            self.capture_source = None
            self.capture = []
        if tag == "details":
            self.details_source = None
        if tag == "article" and self.lab_depth:
            self.lab_depth -= 1
            if not self.lab_depth:
                self.lab_texts.append(" ".join(self.current_lab_text))
                self.current_lab_text = []
        if tag == "section" and self.lesson_depth:
            self.lesson_depth -= 1
            if not self.lesson_depth:
                self.lesson_feature_sets.append(set(self.current_lesson_features))
                self.current_lesson_features = set()
        if tag == "section" and self.reference_depth:
            self.reference_depth -= 1


REQUIRED_LESSON_FEATURES = {
    "lesson-outcome",
    "recall",
    "worked-example",
    "exercise",
    "answer-key",
    "review-cue",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def plain_text(markup: str) -> str:
    """Return normalized text from a small, trusted HTML fragment."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", markup)).split())


def main() -> None:
    course_paths = tuple(iter_course_paths())
    html_path = ROOT / "index.html"
    if not html_path.is_file():
        fail("index.html is missing")
    document = html_path.read_text(encoding="utf-8")
    if '<html lang="en">' not in document or 'name="viewport"' not in document:
        fail("index.html needs an English language and responsive viewport contract")
    parser = CourseParser()
    parser.feed(document)
    if parser.errors:
        fail("; ".join(sorted(set(parser.errors))))
    style_blocks = "\n".join(
        re.findall(r"<style\b[^>]*>(.*?)</style>", document, re.DOTALL | re.IGNORECASE)
    )
    if re.search(r"(?:@import\b|url\s*\()", style_blocks, re.IGNORECASE):
        fail("remote-capable CSS imports and URLs are not allowed")
    forbidden_artifacts = sorted(
        path.relative_to(ROOT)
        for path in course_paths
        if "__pycache__" in path.parts
        or ".ruff_cache" in path.parts
        or path.suffix.lower() in {".pyc", ".pyo"}
    )
    if forbidden_artifacts:
        fail(f"portable tree contains generated cache artifacts: {forbidden_artifacts}")
    for target in parser.local_links:
        parsed_target = urllib.parse.urlparse(target)
        relative_target = urllib.parse.unquote(parsed_target.path)
        resolved_target = (ROOT / relative_target).resolve()
        if ROOT.resolve() not in resolved_target.parents and resolved_target != ROOT:
            fail(f"local HTML link escapes the course root: {target}")
        if not resolved_target.exists():
            fail(f"broken local HTML link: {target}")
    resources_path = ROOT / "RESOURCES.md"
    if not resources_path.is_file():
        fail("RESOURCES.md is missing")
    resource_links = re.findall(
        r"\]\((https?://[^)\s]+)\)", resources_path.read_text(encoding="utf-8")
    )
    if len(resource_links) < 5:
        fail("RESOURCES.md needs a substantial official reference trail")
    invalid_resource_links = sorted(
        link for link in resource_links if not is_official_reference(link)
    )
    if invalid_resource_links:
        fail(f"non-official RESOURCES.md links: {invalid_resource_links}")
    duplicate_resource_links = sorted(
        {link for link in resource_links if resource_links.count(link) > 1}
    )
    if duplicate_resource_links:
        fail(f"duplicate RESOURCES.md links: {duplicate_resource_links}")
    for markdown_path in (path for path in course_paths if path.suffix == ".md"):
        markdown = markdown_path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", markdown):
            parsed_target = urllib.parse.urlparse(target)
            if parsed_target.scheme or target.startswith("#"):
                continue
            relative_target = urllib.parse.unquote(parsed_target.path)
            resolved_target = (markdown_path.parent / relative_target).resolve()
            if (
                ROOT.resolve() not in resolved_target.parents
                and resolved_target != ROOT
            ):
                fail(
                    "local Markdown link escapes the course root: "
                    f"{markdown_path.relative_to(ROOT)} -> {target}"
                )
            if not resolved_target.exists():
                fail(
                    "broken local Markdown link: "
                    f"{markdown_path.relative_to(ROOT)} -> {target}"
                )
    if len(parser.ids) != len(set(parser.ids)):
        fail("duplicate HTML id")
    missing_fragments = sorted(set(parser.fragments) - set(parser.ids))
    if missing_fragments:
        fail(f"missing anchor targets: {missing_fragments}")
    title_match = re.search(r"<head>.*?<title>(.*?)</title>", document, re.DOTALL)
    h1_match = re.search(r"<h1>(.*?)</h1>", document, re.DOTALL)
    readme_match = re.search(
        r"^#\s+(.+)$", (ROOT / "README.md").read_text(encoding="utf-8"), re.MULTILINE
    )
    if not title_match or not h1_match or not readme_match:
        fail("document title, H1, and README title must all be present")
    canonical_titles = {
        plain_text(title_match.group(1)),
        plain_text(h1_match.group(1)),
        readme_match.group(1).strip(),
    }
    if len(canonical_titles) != 1:
        fail(f"course title drift: {sorted(canonical_titles)}")
    if canonical_titles != {"GPU Performance Optimization"}:
        fail(f"unexpected canonical course title: {sorted(canonical_titles)}")
    header_match = re.search(r"<header>(.*?)</header>", document, re.DOTALL)
    expected_header = (
        f"<h1>{h1_match.group(1)}</h1>"
        '<p class="course-hours"><strong>40 guided hours</strong></p>'
    )
    if not header_match or "".join(header_match.group(1).split()) != "".join(
        expected_header.split()
    ):
        fail("top banner must contain only the course title and 40 guided hours")
    if document.count('<a class="skip-link" href="#course-content">') != 1:
        fail("course needs one skip link to the main content")
    if document.count('<main id="course-content">') != 1:
        fail("course needs one main content landmark")
    nav_match = re.search(r"<nav\b.*?</nav>", document, re.DOTALL)
    if not nav_match or "<h2>Course contents</h2>" not in nav_match.group(0):
        fail("course needs a consistently named contents navigation")
    nav_lessons = [
        (target, plain_text(label))
        for target, label in re.findall(
            r'<a href="#([^"]+)">(.*?)</a>', nav_match.group(0), re.DOTALL
        )
        if re.match(r"^\d+\.\s", plain_text(label))
    ]
    lesson_headings = [
        (target, plain_text(heading))
        for target, heading in re.findall(
            r'<section id="([^"]+)" class="lesson">\s*'
            r'<h3 class="lesson-title">(.*?)</h3>',
            document,
            re.DOTALL,
        )
    ]
    if nav_lessons != lesson_headings:
        fail("numbered contents entries must exactly match lesson titles and targets")
    expected_parts = (
        "Part I · Performance foundations and evidence",
        "Part II · Diagnostic tools and practical profiling",
        "Part III · Measurement and targeted optimization",
        "Part IV · Distributed performance and causal decisions",
    )
    parts = tuple(
        plain_text(part)
        for part in re.findall(
            r'<h2 class="part">(.*?)</h2>',
            document,
            re.DOTALL,
        )
    )
    if parts != expected_parts:
        fail(f"course part hierarchy drift: {parts}")
    syllabus = (ROOT / "SYLLABUS.md").read_text(encoding="utf-8")
    missing_syllabus_parts = [
        part
        for part in expected_parts
        if f"## {part.replace(' · ', ': ')}" not in syllabus
    ]
    if missing_syllabus_parts:
        fail(f"syllabus part hierarchy drift: {missing_syllabus_parts}")
    duplicate_links = sorted(
        {
            link
            for link in parser.external_links
            if parser.external_links.count(link) > 1
        }
    )
    if duplicate_links:
        fail(f"duplicate external references: {duplicate_links}")
    reference_numbers = [
        int(number) for number in re.findall(r'<li id="ref-o(\d+)">', document)
    ]
    if reference_numbers != list(range(1, 30)):
        fail(
            "course needs exactly 29 sequential official references, "
            f"found: {reference_numbers}"
        )
    if any(
        plain_text(label) != f"O{target}"
        for target, label in re.findall(
            r'<a href="#ref-o(\d+)">(.*?)</a>', document, re.DOTALL
        )
    ):
        fail("official-source link labels must match their reference targets")
    if document.count("<svg") < 3 or document.count("<svg") + 1 != document.count(
        "<title"
    ):
        fail("every course needs at least three titled inline SVG diagrams")
    for marker in re.findall(r"<marker\b[^>]*>", document):
        width = re.search(r'markerWidth="([0-9.]+)"', marker)
        height = re.search(r'markerHeight="([0-9.]+)"', marker)
        orient = re.search(r'orient="([^"]+)"', marker)
        if (
            not width
            or not height
            or float(width.group(1)) > 8
            or float(height.group(1)) > 8
            or not orient
            or orient.group(1) not in {"auto", "auto-start-reverse"}
            or 'markerUnits="userSpaceOnUse"' not in marker
        ):
            fail(f"SVG arrow marker violates the size or direction contract: {marker}")
    required_files = (
        "MISSION.md",
        "COURSE.md",
        "GLOSSARY.md",
        "SYLLABUS.md",
        "PUBLICATION-REVIEW.md",
        "reference/source-coverage.md",
        "reference/evidence-security.md",
        "reference/tooling-setup.md",
    )
    missing_files = [name for name in required_files if not (ROOT / name).is_file()]
    if missing_files:
        fail(f"required course files missing: {missing_files}")
    if parser.lesson_count != 19:
        fail(f"course needs exactly 19 detailed lessons, found {parser.lesson_count}")
    incomplete_lessons = [
        index + 1
        for index, features in enumerate(parser.lesson_feature_sets)
        if features != REQUIRED_LESSON_FEATURES
    ]
    if len(parser.lesson_feature_sets) != parser.lesson_count or incomplete_lessons:
        fail(f"lesson teaching features incomplete: {incomplete_lessons}")
    if parser.lab_count != parser.complete_lab_guides:
        fail("every lab card needs a complete prediction/interpretation guide")
    if (
        document.count("<strong>Run through Slurm</strong><pre><code>sbatch")
        != parser.lab_count
    ):
        fail("every lab card must start with an sbatch command")
    guide_markers = (
        "Predict before running",
        "Interpret after running",
        "If the result surprises you",
    )
    if any(
        any(marker not in text for marker in guide_markers) for text in parser.lab_texts
    ):
        fail("every lab guide needs prediction, interpretation, and troubleshooting")
    if parser.official_links < 5:
        fail("course needs a substantial final official reference trail")
    for relative, embedded in parser.embedded.items():
        source = ROOT / relative
        if not source.is_file():
            fail(f"embedded source is missing: {relative}")
        if embedded != source.read_text(encoding="utf-8"):
            fail(f"embedded source drift: {relative}")
    lab_files = sorted(
        path
        for path in (ROOT / "labs").glob("*.py")
        if path.name not in {"common.py", "tiny_lm.py"}
    )
    missing_embeds = [
        path.name for path in lab_files if f"labs/{path.name}" not in parser.embedded
    ]
    if missing_embeds:
        fail(f"lab source listings missing: {missing_embeds}")
    if len(lab_files) != 15:
        fail(f"course needs exactly 15 runnable labs, found {len(lab_files)}")
    profiler_lab_text = (ROOT / "labs" / "14_profiler_bottlenecks.py").read_text(
        encoding="utf-8"
    )
    if "useful_operations_estimate" in profiler_lab_text:
        fail(
            "profiler lab must not assign a portable FLOP count to transcendental work"
        )
    if "relative_l2_error" not in profiler_lab_text:
        fail("profiler lab needs a normalized BF16 GEMM correctness gate")
    required_concepts = (
        "five primary bottleneck classes",
        "compute-bound",
        "memory-bandwidth-bound",
        "launch- or cpu-bound",
        "communication-bound",
        "input- or storage-bound",
        "pytorch profiler",
        "nvtx",
        "nsight systems",
        "nsight compute",
        "arithmetic intensity",
        "effective workload tflop/s",
        "dcgm",
        "genai-perf",
        "aiperf",
    )
    normalized_document = plain_text(document).lower()
    missing_concepts = [
        term for term in required_concepts if term not in normalized_document
    ]
    if missing_concepts:
        fail(f"required bottleneck or tooling concepts missing: {missing_concepts}")
    with tempfile.TemporaryDirectory(prefix="gpu-course-compile-") as temporary:
        compile_root = Path(temporary)
        for lab in sorted((ROOT / "labs").glob("*.py")):
            py_compile.compile(
                str(lab),
                cfile=str(compile_root / f"{lab.stem}.pyc"),
                doraise=True,
            )
    for lab in lab_files:
        lab_text = lab.read_text(encoding="utf-8")
        if "argparse.ArgumentParser" not in lab_text or ".parse_args()" not in lab_text:
            fail(f"static help contract missing: labs/{lab.name}")
    forbidden = (
        "/users/",
        "youtube.com",
        "youtu.be",
        "video-transcripts",
        "speaker name",
        "nebius-ps-services",
    )
    scan_paths = [
        path
        for path in course_paths
        if path.is_file()
        and path.resolve() != Path(__file__).resolve()
        and path.suffix in {".html", ".md", ".py", ".sbatch", ".txt"}
    ]
    for path in scan_paths:
        lowered = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token in lowered:
                fail(f"private-source marker found in {path.relative_to(ROOT)}")
        sensitive_patterns = {
            "private key": r"-----begin(?: rsa| ec| openssh| dsa)? private key-----",
            "credential assignment": (
                r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|"
                r"client[_-]?secret)\s*[:=]\s*['\"]?[a-z0-9_./+=-]{8,}"
            ),
            "bearer credential": (r"authorization\s*:\s*bearer\s+[a-z0-9._~+/-]{8,}"),
            "known credential prefix": (
                r"(?:akia[a-z0-9]{16}|gh[pousr]_[a-z0-9_]{16,}|"
                r"xox[baprs]-[a-z0-9-]{12,}|sk-[a-z0-9_-]{16,}|"
                r"aiza[a-z0-9_-]{20,})"
            ),
            "private network address": (
                r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
                r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
            ),
        }
        for risk_class, pattern in sensitive_patterns.items():
            if re.search(pattern, lowered, re.IGNORECASE):
                fail(f"{risk_class} marker found in {path.relative_to(ROOT)}")
    for launcher in (ROOT / "slurm").glob("*.sbatch"):
        text = launcher.read_text(encoding="utf-8")
        if "#SBATCH --gpus-per-node=1" not in text:
            fail(f"launcher does not request one GPU per node: {launcher.name}")
        if "umask 077" not in text:
            fail(f"launcher does not create private artifacts: {launcher.name}")
        if "PYTHONDONTWRITEBYTECODE=1" not in text:
            fail(f"launcher does not suppress Python bytecode: {launcher.name}")
        if subprocess.run(
            ["bash", "-n", str(launcher)],
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        ).returncode:
            fail(f"launcher has invalid Bash syntax: {launcher.name}")
        if "show_usage" not in text or '"--help"' not in text or "Usage:" not in text:
            fail(f"static help contract missing: slurm/{launcher.name}")
    nsys_launcher = (ROOT / "slurm" / "nsys_single_gpu.sbatch").read_text(
        encoding="utf-8"
    )
    required_nsys_markers = ("--sample=none", "--cpuctxsw=none", "nsys stats")
    if any(marker not in nsys_launcher for marker in required_nsys_markers):
        fail("Nsight Systems launcher lacks focused collection or statistics export")
    if "--external-only" in nsys_launcher:
        fail("Nsight Systems launcher must not inject a lab-specific argument")
    if "mktemp -d results/nsys-run-XXXXXXXX" not in nsys_launcher:
        fail("Nsight Systems launcher needs a private unique result directory")
    ncu_launcher = (ROOT / "slurm" / "ncu_single_gpu.sbatch").read_text(
        encoding="utf-8"
    )
    required_ncu_markers = (
        '--set="${ncu_set}"',
        "NCU_SECTIONS",
        '--section "${section_name}"',
        "--nvtx-include 'profile_region/'",
        "--launch-count=1",
    )
    if any(marker not in ncu_launcher for marker in required_ncu_markers):
        fail("Nsight Compute launcher lacks the focused range or section contract")
    if "--set=full" in ncu_launcher or "--external-only" in ncu_launcher:
        fail("Nsight Compute launcher uses broad collection or a lab-specific argument")
    if "mktemp -d results/ncu-run-XXXXXXXX" not in ncu_launcher:
        fail("Nsight Compute launcher needs a private unique result directory")
    if not (ROOT / "slurm" / "tooling_preflight.sbatch").is_file():
        fail("compute-node tooling preflight launcher is missing")
    tooling_preflight = (ROOT / "slurm" / "tooling_preflight.sbatch").read_text(
        encoding="utf-8"
    )
    required_preflight_markers = (
        "nsys status -e",
        "ncu --list-sets",
        "ncu --list-sections",
        "dcgmi profile --list --entity-id gpu:0",
        "dcgm-exporter",
        "nvbandwidth",
        "all_reduce_perf",
        "all_gather_perf",
        "reduce_scatter_perf",
        "alltoall_perf",
        "genai-perf --version",
        "aiperf --version",
    )
    if any(marker not in tooling_preflight for marker in required_preflight_markers):
        fail("tooling preflight does not verify every documented capability")
    tooling_guide = (ROOT / "reference" / "tooling-setup.md").read_text(
        encoding="utf-8"
    )
    tool_scope_markers = (
        "DCGM Exporter",
        "`nvbandwidth`",
        "NCCL Tests",
        "vLLM Bench",
        "MLPerf",
        "all-to-all",
        "real PyTorch workload",
    )
    if any(marker not in tooling_guide for marker in tool_scope_markers):
        fail("tooling guide lacks a required external-tool evidence boundary")
    exposed_preflight_metadata = (
        "$(hostname)",
        "name,uuid",
        '"$(command -v',
        "Slurm job:",
    )
    if any(marker in tooling_preflight for marker in exposed_preflight_metadata):
        fail("tooling preflight exposes environment-specific identifiers")
    common_text = (ROOT / "labs" / "common.py").read_text(encoding="utf-8")
    common_markers = (
        '"gpu-course-result/v1"',
        '"run_id"',
        "COURSE_RUN_ID",
        "os.O_EXCL",
    )
    if any(marker not in common_text for marker in common_markers):
        fail("lab results lack the canonical run identity or exclusive-write contract")
    if '"slurm_job_id"' in common_text:
        fail("portable lab results must omit scheduler identities")
    input_pipeline = (ROOT / "labs" / "05_input_pipeline.py").read_text(
        encoding="utf-8"
    )
    pipeline_markers = (
        '"batch_ready_gap"',
        '"h2d"',
        '"device_consumption"',
        '"end_to_end"',
        "single-stream serialized component evidence",
    )
    if any(marker not in input_pipeline for marker in pipeline_markers):
        fail("input-pipeline lab lacks the four-phase serialized timing contract")
    checkpointing = (ROOT / "labs" / "06_activation_checkpointing.py").read_text(
        encoding="utf-8"
    )
    checkpoint_markers = (
        "gradient_snapshot",
        "torch.allclose",
        '"max_abs_gradient_error"',
        '"relative_l2_gradient_error"',
        '"gradients_allclose"',
    )
    if any(marker not in checkpointing for marker in checkpoint_markers):
        fail("checkpointing lab lacks the matched forward/backward gradient gate")
    security_guide = (ROOT / "reference" / "evidence-security.md").read_text(
        encoding="utf-8"
    )
    security_markers = (
        "umask 077",
        "GPU UUIDs",
        "Share summaries, not raw artifacts",
        "does not execute",
    )
    if any(marker not in security_guide for marker in security_markers):
        fail("evidence-security guide is missing a required safeguard")
    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    smoke_text = (ROOT / "reference" / "cluster-smoke-test.md").read_text(
        encoding="utf-8"
    )
    if "reference/evidence-security.md" not in readme_text:
        fail("README does not link the evidence-security guide")
    if "evidence-security.md" not in smoke_text:
        fail("cluster smoke test does not link the evidence-security guide")
    if "pip install -r requirements.txt" in document:
        fail("course must not resolve the compatibility constraint as a lock")
    requirements_text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    if "not an immutable lock file" not in requirements_text:
        fail("requirements.txt must declare its non-lockfile boundary")
    published_text = "\n".join(path.read_text(encoding="utf-8") for path in scan_paths)
    for lab_name in set(
        re.findall(r"two_node\.sbatch\s+labs/([A-Za-z0-9_]+\.py)", published_text)
    ):
        lab_text = (ROOT / "labs" / lab_name).read_text(encoding="utf-8")
        if (
            "init_nccl" not in lab_text
            and "configure_slurm_distributed_env" not in lab_text
        ):
            fail(f"non-distributed lab documented with two_node.sbatch: {lab_name}")
    distributed = (ROOT / "slurm" / "two_node.sbatch").read_text(encoding="utf-8")
    distributed_markers = (
        "#SBATCH --nodes=2",
        "torchrun",
        "--nnodes=2",
        "--nproc-per-node=1",
        "--node-rank=",
    )
    if any(marker not in distributed for marker in distributed_markers):
        fail("two_node.sbatch does not implement the two-node torchrun contract")
    vllm_two_node = ROOT / "slurm" / "vllm_two_node.sbatch"
    if vllm_two_node.is_file():
        serving_text = vllm_two_node.read_text(encoding="utf-8")
        bounded_markers = (
            "/health",
            "labs/11_serving_client.py",
            "trap cleanup",
            "results/",
        )
        if any(marker not in serving_text for marker in bounded_markers):
            fail(
                "vllm_two_node.sbatch lacks bounded readiness, client, result, or cleanup"
            )
    print(
        f"PASS: {ROOT.name}: {len(lab_files)} runnable labs, "
        f"{len(parser.embedded)} synchronized listings, offline course contracts validated"
    )


if __name__ == "__main__":
    main()
