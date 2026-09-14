"""Guard prerequisite-first progression and its learner-facing route."""

import html
import re

import pytest

from test_course_content_contract import lab_section, COURSES, ROOT, load_builder

EXPECTED_ORDER = {
    "gpu-fundamentals": [
        "CPU–GPU cooperation",
        "GPU execution software layers",
        "GPU execution architecture",
        "GPU memory hierarchy",
        "Parallel control flow",
        "Occupancy and latency hiding",
        "Memory access efficiency",
        "Asynchronous execution and timing",
        "Numerical precision and accelerated arithmetic",
        "Arithmetic intensity and performance limits",
        "GPU sharing and operational health",
        "GPU communication and distributed execution",
    ],
    "gpu-optimizations": [
        "Controlled GPU optimization",
        "Asynchronous performance measurement",
        "Performance evidence and profiling",
        "Submission overhead and kernel fusion",
        "Reusable GPU execution plans",
        "Input readiness and transfer overlap",
        "Tensor layout and memory traffic",
        "Memory allocation and ownership",
        "Efficient numerical-library execution",
        "Parallel imbalance and completion tails",
        "GPU communication paths and performance",
        "Distributed scaling and communication overlap",
        "Choosing an optimization layer",
    ],
    "llm-training": [
        "Model learning and training objectives",
        "Causal training data",
        "Decoder architecture and information flow",
        "The parameter-update lifecycle",
        "Training evaluation and recovery",
        "Training memory and state lifetimes",
        "Numerical precision in training",
        "Memory savings through accumulation and recomputation",
        "Training input readiness",
        "Training execution optimization",
        "Distributed training-state ownership",
        "Model partitioning and communication",
        "Gradient readiness and communication overlap",
        "Parameter-efficient adaptation",
        "Reward-guided policy optimization",
        "Evidence-based training optimization",
    ],
    "llm-inference": [
        "Model inference and artifact preparation",
        "Autoregressive generation",
        "Decoding policy and output quality",
        "Attention-cache capacity",
        "Inference workload shape",
        "Model-serving architecture",
        "Serving latency and useful throughput",
        "Attention-cache allocation and reclamation",
        "Request scheduling and prompt chunking",
        "Prefix reuse and cache retention",
        "Efficient attention execution",
        "Quantized inference representations",
        "Speculative generation",
        "Distributed inference placement",
        "Serving workloads and phase separation",
        "Evidence-based inference optimization",
    ],
    "custom-cuda-kernels": [
        "Custom kernel decision making",
        "CUDA compilation and execution targets",
        "Kernel indexing and execution safety",
        "Kernel correctness and performance evidence",
        "Elementwise kernel fusion",
        "Memory layout and tiled transposition",
        "Parallel reductions",
        "Neighborhood reuse and boundary handling",
        "Kernel resource use and occupancy",
        "Parallel workload balance",
        "Asynchronous memory pipelines",
        "Matrix multiplication and output fusion",
        "Residual connections and normalization",
        "Kernel acceptance and integration",
        "Advanced GPU data movement and cooperation",
        "Tile-level kernel programming",
    ],
}


@pytest.mark.parametrize("course", COURSES)
def test_course_has_reviewed_competency_order(course):
    builder = load_builder()
    document = (ROOT / course / "COURSE.md").read_text()
    lessons = builder.parse_course(ROOT / course / "COURSE.md")[2]
    assert [lesson["title"] for lesson in lessons] == EXPECTED_ORDER[course]
    assert [int(n) for n in re.findall(r"^## (\d+)\.", document, re.M)] == list(
        range(1, len(lessons) + 1)
    )


@pytest.mark.parametrize("course", COURSES)
def test_syllabus_names_every_lesson_in_reading_order(course):
    text = (ROOT / course / "SYLLABUS.md").read_text()
    rows = re.findall(r"^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", text, re.M)
    assert [int(row[0]) for row in rows] == list(
        range(1, len(EXPECTED_ORDER[course]) + 1)
    )
    assert [row[1].strip() for row in rows] == EXPECTED_ORDER[course]
    assert all(len(row[2].split()) >= 5 and row[3].strip() for row in rows)
    assert "Lab numbers are identifiers, not the execution order" in text


@pytest.mark.parametrize("course", COURSES)
def test_rendered_lesson_toc_follows_reviewed_order(course):
    document = (ROOT / course / "index.html").read_text()
    builder = load_builder()
    for title in EXPECTED_ORDER[course]:
        assert f'id="{builder.slug(title)}"' in document
    positions = [
        document.index(f'<section class="lesson" id="{builder.slug(title)}">')
        for title in EXPECTED_ORDER[course]
    ]
    assert positions == sorted(positions)
    toc = document.split("<h2>Lessons</h2><ol>", 1)[1].split("</ol>", 1)[0]
    assert [
        html.unescape(x) for x in re.findall(r'<a href="#[^"]+">([^<]+)</a>', toc)
    ] == EXPECTED_ORDER[course]


def lesson(course, title):
    return next(
        item
        for item in load_builder().parse_course(ROOT / course / "COURSE.md")[2]
        if item["title"] == title
    )


def test_foundations_teaches_basic_timing_before_first_benchmark():
    entry = lesson("gpu-fundamentals", EXPECTED_ORDER["gpu-fundamentals"][0])
    explanation = entry["How it works"].lower()
    assert "cpu timer" in explanation and "cuda events" in explanation
    concepts = lab_section("gpu-fundamentals", 1, "Concepts and code path").lower()
    assert "warm-up" in concepts and "cuda events" in concepts
    assert "preflight" in lab_section("gpu-fundamentals", 1, "Practice").lower()
    assert "Lesson 10" in lab_section("gpu-fundamentals", 4, "Practice")
    assert "defer" in lab_section("gpu-fundamentals", 4, "Practice").lower()


def test_training_previews_do_not_require_advanced_execution():
    practice = lab_section("llm-training", 32, "Practice")
    assert "CPU example first" in practice
    assert "leave SFT/LoRA and GRPO for Lessons 14–15" in practice
    batching = lesson("llm-training", EXPECTED_ORDER["llm-training"][1])
    assert "logits" in batching["How it works"]
    assert "Lesson 4" in lab_section("llm-training", 25, "Practice")
    assert "Lab 25's packing-plan and causal-mask checks now" in lab_section(
        "llm-training", 25, "Practice"
    )
    assert "does not execute transformer training" in lab_section(
        "llm-training", 25, "Practice"
    )
    local = lesson("llm-training", "Training execution optimization")
    assert "Communication overlap shortens" not in local["How it works"]


def test_inference_starts_with_basic_engine_then_advanced_work():
    assert "single-GPU" in lab_section("llm-inference", 30, "Practice")
    assert "advanced" in lab_section("llm-inference", 30, "Practice").lower()
    metrics = lesson("llm-inference", EXPECTED_ORDER["llm-inference"][6])
    # Both metrics belong here; the explanation should distinguish them.
    assert all(term in metrics["How it works"] for term in ("ITL", "TPOT"))
    practice = lab_section("llm-inference", 15, "Practice")
    assert "AIPerf" in practice and "slurm/aiperf.sbatch" in practice


def test_cuda_safety_practice_uses_completed_vector_lab():
    course = ROOT / "custom-cuda-kernels"
    builder = load_builder()
    lessons = builder.parse_course(course / "COURSE.md")[2]
    data = builder.course_metadata(course)
    safety = next(
        i
        for i, row in enumerate(lessons, 1)
        if row["title"] == "Kernel correctness and performance evidence"
    )
    vector = next(row for row in data["labs"] if row["path"] == "labs/01_vector_add.cu")
    capstone = next(row for row in data["labs"] if row["path"] == "labs/12_capstone.cu")
    assert safety in vector["lessons"]
    assert safety not in capstone["lessons"]
    text = lab_section("custom-cuda-kernels", 1, "Practice")
    assert "01_vector_add" in text


@pytest.mark.parametrize(
    ("course", "distributed_lesson"),
    (
        ("gpu-fundamentals", 12),
        ("gpu-optimizations", 11),
        ("llm-training", 11),
        ("llm-inference", 14),
    ),
)
def test_two_node_preflight_is_assigned_only_after_local_foundations(
    course, distributed_lesson
):
    metadata = load_builder().course_metadata(ROOT / course)
    preflight = next(
        item
        for item in metadata["labs"]
        if item["path"] == "labs/00_cluster_preflight.py"
    )
    assert preflight["lessons"] == [distributed_lesson]
    if course == "gpu-optimizations":
        assert "topology preview" in lab_section("gpu-optimizations", 1, "Practice")
        assert "run that two-node preflight in Lesson 11" in lab_section(
            "gpu-optimizations", 1, "Practice"
        )


@pytest.mark.parametrize(
    ("course", "lab", "title"),
    (
        (
            "gpu-fundamentals",
            "04_layout_and_coalescing",
            "Memory access efficiency",
        ),
        (
            "gpu-fundamentals",
            "05_roofline_microbench",
            "Arithmetic intensity and performance limits",
        ),
        (
            "gpu-optimizations",
            "15_tail_load_balance",
            "Parallel imbalance and completion tails",
        ),
        (
            "gpu-optimizations",
            "13_collective_overlap",
            "Distributed scaling and communication overlap",
        ),
        ("llm-training", "05_lora_sft", "Parameter-efficient adaptation"),
        (
            "llm-training",
            "27_fused_graph_trace",
            "Training execution optimization",
        ),
        ("llm-inference", "20_prefix_cache_client", "Prefix reuse and cache retention"),
        (
            "llm-inference",
            "30_engine_profile",
            "Model-serving architecture",
        ),
        (
            "custom-cuda-kernels",
            "07_resource_sweep",
            "Kernel resource use and occupancy",
        ),
        (
            "custom-cuda-kernels",
            "10_hopper_cluster",
            "Advanced GPU data movement and cooperation",
        ),
    ),
)
def test_reordered_labs_keep_their_semantic_lesson(course, lab, title):
    builder = load_builder()
    lessons = builder.parse_course(ROOT / course / "COURSE.md")[2]
    metadata = builder.course_metadata(ROOT / course)
    item = next(
        item
        for item in metadata["labs"]
        if item["path"].split("/")[-1].rsplit(".", 1)[0] == lab
    )
    assert title in [lessons[number - 1]["title"] for number in item["lessons"]]
