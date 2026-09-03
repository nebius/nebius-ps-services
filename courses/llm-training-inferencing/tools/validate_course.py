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
}


def is_official_reference(href: str) -> bool:
    parsed = urllib.parse.urlparse(href)
    if parsed.scheme != "https":
        return False
    return parsed.hostname in ALLOWED_HOSTS or href.startswith(
        "https://github.com/NVIDIA/"
    )


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
    header_match = re.search(r"<header>(.*?)</header>", document, re.DOTALL)
    expected_header = (
        f"<h1>{h1_match.group(1)}</h1>"
        '<p class="course-hours"><strong>60 guided hours</strong></p>'
    )
    if not header_match or "".join(header_match.group(1).split()) != "".join(
        expected_header.split()
    ):
        fail("top banner must contain only the course title and 60 guided hours")
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
        "Part I · LLM training",
        "Part II · LLM inference",
        "Part III · Training performance optimization",
        "Part IV · Inference performance optimization",
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
        int(number) for number in re.findall(r'<li id="ref-l(\d+)">', document)
    ]
    if reference_numbers != list(range(1, len(reference_numbers) + 1)):
        fail(f"official reference identifiers are not sequential: {reference_numbers}")
    if any(
        plain_text(label) != f"L{target}"
        for target, label in re.findall(
            r'<a href="#ref-l(\d+)">(.*?)</a>', document, re.DOTALL
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
    )
    missing_files = [name for name in required_files if not (ROOT / name).is_file()]
    if missing_files:
        fail(f"required course files missing: {missing_files}")
    if parser.lesson_count != 24:
        fail(f"course needs exactly 24 detailed lessons, found {parser.lesson_count}")
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
        "-----begin private key-----",
        "authorization: bearer",
        "api_key=",
        "password=",
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
            "bearer credential": r"authorization\s*:\s*bearer\s+[a-z0-9._~+/-]{8,}",
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
        if subprocess.run(["bash", "-n", str(launcher)], check=False).returncode:
            fail(f"launcher has invalid Bash syntax: {launcher.name}")
    single_gpu = (ROOT / "slurm" / "single_gpu.sbatch").read_text(encoding="utf-8")
    if "umask 077" not in single_gpu or "PYTHONDONTWRITEBYTECODE=1" not in single_gpu:
        fail("single-GPU launcher does not create private, cache-free lab artifacts")
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
            "/metrics",
            "labs/11_serving_client.py",
            "trap cleanup",
            "results/",
        )
        if any(marker not in serving_text for marker in bounded_markers):
            fail(
                "vllm_two_node.sbatch lacks bounded readiness, client, result, or cleanup"
            )
    for name in ("vllm_benchmark.sbatch", "vllm_streaming_benchmark.sbatch"):
        serving_text = (ROOT / "slurm" / name).read_text(encoding="utf-8")
        if "/metrics" not in serving_text:
            fail(f"{name} does not preserve a server metrics snapshot")
    prefix_launcher = ROOT / "slurm" / "vllm_prefix_cache.sbatch"
    if not prefix_launcher.is_file():
        fail("prefix-cache A/B launcher is missing")
    prefix_text = prefix_launcher.read_text(encoding="utf-8")
    prefix_markers = (
        "--enable-prefix-caching",
        "--no-enable-prefix-caching",
        "/health",
        "/metrics",
        "labs/20_prefix_cache_client.py",
        "trap stop_server",
        "for variant in disabled enabled",
    )
    if any(marker not in prefix_text for marker in prefix_markers):
        fail("prefix-cache launcher lacks a bounded disabled/enabled evidence path")
    mixed_precision = (ROOT / "labs" / "21_mixed_precision_training.py").read_text(
        encoding="utf-8"
    )
    mixed_precision_markers = (
        '"fp32": None',
        '"bf16": torch.bfloat16',
        '"fp16": torch.float16',
        'torch.amp.GradScaler("cuda", enabled=use_fp16_scaler)',
        '"identical_initial_state_and_batch": True',
        '"loss_relative_error_vs_fp32"',
        '"gradient_relative_l2_vs_fp32"',
        '"update_relative_l2_vs_fp32"',
        '"one_step_numerical_acceptance"',
        '"first_timed_loss_after_warmup"',
        "not math.isfinite(value) or value <= 0",
        "any(not math.isfinite(value) for value in equivalence_errors)",
    )
    if any(marker not in mixed_precision for marker in mixed_precision_markers):
        fail("mixed-precision lab lacks the matched FP32/BF16/scaled-FP16 contract")
    fp8_lab = (ROOT / "labs" / "22_transformer_engine_fp8.py").read_text(
        encoding="utf-8"
    )
    fp8_markers = (
        "Optional lab blocked:",
        "DelayedScaling",
        "Format.HYBRID",
        "with te.autocast(enabled=True, recipe=recipe):",
        "compare_warmed_once",
        "numerical_samples.append(compare_warmed_once())",
        '"validation_state": "warmed delayed-scaling recipe before and after timing"',
        "not math.isfinite(value) or value <= 0",
        "if args.warmup < 1:",
        '"--warmup must be at least one so BF16 and FP8 use warmed boundaries."',
        '"incremental_peak_above_baseline_mib"',
        '"not a standalone full-model footprint"',
        "not errors_finite",
        '"backward_outside_fp8_autocast": True',
    )
    if any(marker not in fp8_lab for marker in fp8_markers):
        fail("optional FP8 lab lacks the dependency, recipe, or autocast boundary")
    bf16_measurement = fp8_lab.find("bf16_samples, bf16_memory = measure_steps(")
    first_fp8_warmup = fp8_lab.find("for _ in range(args.warmup):")
    if not 0 <= bf16_measurement < first_fp8_warmup:
        fail("optional FP8 lab must measure BF16 before initializing FP8 state")
    training_requirements = (ROOT / "requirements-training.txt").read_text(
        encoding="utf-8"
    )
    if "transformer-engine" in training_requirements.lower():
        fail(
            "optional Transformer Engine must not enter the base training requirements"
        )
    speculation = (ROOT / "labs" / "23_speculative_decoding.py").read_text(
        encoding="utf-8"
    )
    speculation_markers = (
        "baseline_tokens == high_tokens == low_tokens",
        '"high_acceptance_reduces_target_calls"',
        '"low_acceptance_does_not_reduce_target_calls"',
        '"accepted_prefix_histogram"',
        "verification_inputs = [current, *proposals]",
        "proposals, verified[:-1], strict=True",
        '"target_bonus_tokens"',
        '"target_recovered_tokens"',
        '"full_acceptance_emits_target_bonus_tokens"',
        '"first_rejection_emits_target_recovery_tokens"',
        "args.max_new_tokens <= args.draft_length",
    )
    if any(marker not in speculation for marker in speculation_markers):
        fail("speculative-decoding lab lacks output or acceptance-mechanism gates")
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
    training_lab = (ROOT / "labs" / "01_tiny_transformer_train.py").read_text(
        encoding="utf-8"
    )
    training_markers = (
        "error_if_nonfinite=True",
        '"gradient_norm"',
        '"incremental_peak_allocated_bytes"',
        '"parameter_updated"',
    )
    if any(marker not in training_lab for marker in training_markers):
        fail("tiny-transformer lab lacks gradient, memory, or update evidence")
    hf_generation = (ROOT / "labs" / "09_hf_prefill_decode.py").read_text(
        encoding="utf-8"
    )
    generation_markers = (
        "DynamicCache",
        "generation_schedule",
        "attention_mask",
        "cache_position",
        '"generated_requested_token_count"',
    )
    if any(marker not in hf_generation for marker in generation_markers):
        fail("HF generation lab lacks explicit cache state or exact token accounting")
    vllm_offline = (ROOT / "labs" / "10_vllm_offline.py").read_text(encoding="utf-8")
    aggregation_markers = (
        "aggregate_outputs",
        '"prompt_tokens_total"',
        '"output_tokens_total"',
        '"finish_reasons"',
        '"output_tokens_per_second"',
    )
    if any(marker not in vllm_offline for marker in aggregation_markers):
        fail("vLLM offline lab lacks full-window prompt/output aggregation")
    loss_masking = (ROOT / "labs" / "13_loss_masking.py").read_text(encoding="utf-8")
    if "validate_common_args(args)" not in loss_masking:
        fail("loss-masking lab bypasses common result and argument validation")
    direct_artifact_labs = (
        "01_tiny_transformer_train.py",
        "07_grpo_trainer.py",
        "11_serving_client.py",
        "15_streaming_client.py",
        "20_prefix_cache_client.py",
    )
    for name in direct_artifact_labs:
        text = (ROOT / "labs" / name).read_text(encoding="utf-8")
        if "run_id" not in text or "slurm_job_id" in text:
            fail(f"labs/{name} lacks portable run identity for direct artifacts")
    for name in (
        "vllm_benchmark.sbatch",
        "vllm_streaming_benchmark.sbatch",
        "vllm_prefix_cache.sbatch",
        "vllm_two_node.sbatch",
    ):
        text = (ROOT / "slurm" / name).read_text(encoding="utf-8")
        if "COURSE_RUN_ID" not in text:
            fail(f"slurm/{name} does not propagate the canonical course run ID")
        artifact_lines = (
            line
            for line in text.splitlines()
            if "server_log=" in line or "--output" in line or "metrics-run-" in line
        )
        if any("SLURM_JOB_ID" in line for line in artifact_lines):
            fail(f"slurm/{name} uses a scheduler identity in portable artifacts")
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
    print(
        f"PASS: {ROOT.name}: {len(lab_files)} runnable labs, "
        f"{len(parser.embedded)} synchronized listings, offline course contracts validated"
    )


if __name__ == "__main__":
    main()
