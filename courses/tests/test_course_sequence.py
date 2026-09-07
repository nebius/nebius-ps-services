"""Guard prerequisite-first progression and its learner-facing route."""

import html
import re

import pytest

from test_course_content_contract import lab_section, COURSES, ROOT, load_builder

EXPECTED_ORDER = {
    "gpu-fundamentals": [
        "Separate CPU work, GPU work, and orchestration",
        "Read the driver, runtime, toolkit, PTX, SASS, and framework stack",
        "Map H100, GPCs, SMs, warps, and Tensor Cores",
        "Follow data through registers, caches, shared memory, and HBM",
        "Reason about SIMT divergence and independent work",
        "Use occupancy to hide latency rather than chase a maximum",
        "Make global memory accesses coalesced",
        "Time transfers, streams, and synchronization correctly",
        "Choose precision and Tensor Core paths deliberately",
        "Classify workloads with arithmetic intensity and roofline",
        "Read sharing and health state without changing it",
        "Understand GPU networking, topology, and collectives",
    ],
    "gpu-optimizations": [
        "Freeze the workload and correctness contract",
        "Measure asynchronous GPU work correctly",
        "Select Nsight Systems, PyTorch Profiler, or Nsight Compute",
        "Reduce launch and Python overhead",
        "Use CUDA Graphs only for stable execution",
        "Keep the input pipeline ahead of the GPU",
        "Optimize memory layout and intermediate traffic",
        "Manage allocator lifetime and peak memory",
        "Select shapes and precision for efficient libraries",
        "Find load imbalance and tail waves",
        "Qualify GPU networking and tune NCCL with evidence",
        "Diagnose distributed scaling and collective overlap",
        "Decide between framework, library, compiler, and custom kernel paths",
    ],
    "llm-training": [
        "Understand model training and its learning objective",
        "Build causal batches with tokens, labels, masks, and packing",
        "Trace a decoder-only transformer",
        "Execute a correct training step",
        "Evaluate, checkpoint, and resume exactly",
        "Build a training memory ledger",
        "Use BF16, FP16, and FP8 without losing the signal",
        "Trade accumulation and recomputation for memory",
        "Prevent input-pipeline starvation",
        "Profile fused operations and CUDA Graphs in training",
        "Choose DDP and FSDP2 from state placement",
        "Understand TP, PP, CP, and EP mechanics",
        "Overlap communication with useful backward work",
        "Perform SFT and LoRA with explicit savings",
        "Understand GRPO objective and system loop",
        "Deliver a causal training optimization report",
    ],
    "llm-inference": [
        "Understand inference and prepare a model safely",
        "Follow tokenization, prefill, decode, and stopping",
        "Keep sampling and quality semantics fixed",
        "Calculate KV-cache capacity for MHA, GQA, and MQA",
        "Define ISL × OSL × concurrency workloads",
        "Operate vLLM and TensorRT-LLM with Triton",
        "Measure TTFT, ITL, TPOT, throughput, and goodput",
        "Allocate and recycle paged KV blocks",
        "Use continuous batching and chunked prefill",
        "Reuse prefix KV safely",
        "Select SDPA and attention backends by phase",
        "Quantize weights and KV with quality gates",
        "Verify speculative decoding acceptance and recovery",
        "Choose inference DP, TP, PP, and EP",
        "Benchmark with AIPerf and bound disaggregation claims",
        "Deliver a causal inference optimization report",
    ],
    "custom-cuda-kernels": [
        "Decide whether a custom kernel is justified",
        "Build and inspect an SM90 CUDA program",
        "Launch a correct vector kernel",
        "Validate with sanitizers and focused profilers",
        "Fuse elementwise work to remove HBM traffic",
        "Coalesce global memory and tile a transpose",
        "Reduce with warp, block, atomic, and CUB paths",
        "Reuse halos in a shared-memory stencil",
        "Balance blocks, registers, spills, and occupancy",
        "Diagnose divergence, imbalance, and tail waves",
        "Pipeline global-to-shared copies",
        "Preserve library GEMM and customize the epilogue",
        "Fuse residual addition and RMSNorm",
        "Complete a production acceptance capstone",
        "Gate H100 TMA and thread-block clusters",
        "Optional appendix: Evaluate CUDA Tile C++",
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
    assert "warm-up" in entry["Mechanism"]
    assert "preflight" in lab_section("gpu-fundamentals", 1, "Practice").lower()
    assert "Lesson 10" in lab_section("gpu-fundamentals", 4, "Practice")
    assert "defer" in lab_section("gpu-fundamentals", 4, "Practice").lower()


def test_training_previews_do_not_require_advanced_execution():
    assert "read-only preview" in lab_section("llm-training", 32, "Practice")
    assert "Do not run" in lab_section("llm-training", 32, "Practice")
    batching = lesson("llm-training", EXPECTED_ORDER["llm-training"][1])
    assert "logits" in batching["Prerequisite bridge"]
    assert "Lesson 4" in lab_section("llm-training", 25, "Practice")
    assert "Lab 25's packing-plan and causal-mask checks now" in lab_section(
        "llm-training", 25, "Practice"
    )
    assert "does not execute transformer training" in lab_section(
        "llm-training", 25, "Practice"
    )
    local = lesson(
        "llm-training", "Profile fused operations and CUDA Graphs in training"
    )
    assert "Communication overlap shortens" not in local["Prerequisite bridge"]


def test_inference_starts_with_basic_engine_then_advanced_work():
    assert "single-GPU" in lab_section("llm-inference", 30, "Practice")
    assert "advanced" in lab_section("llm-inference", 30, "Practice").lower()
    metrics = lesson("llm-inference", EXPECTED_ORDER["llm-inference"][6])
    assert "AIPerf" in metrics["Mechanism"]


def test_cuda_safety_practice_uses_completed_vector_lab():
    course = ROOT / "custom-cuda-kernels"
    builder = load_builder()
    lessons = builder.parse_course(course / "COURSE.md")[2]
    data = builder.course_metadata(course)
    safety = next(
        i
        for i, row in enumerate(lessons, 1)
        if row["title"] == "Validate with sanitizers and focused profilers"
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
            "Make global memory accesses coalesced",
        ),
        (
            "gpu-fundamentals",
            "05_roofline_microbench",
            "Classify workloads with arithmetic intensity and roofline",
        ),
        (
            "gpu-optimizations",
            "15_tail_load_balance",
            "Find load imbalance and tail waves",
        ),
        (
            "gpu-optimizations",
            "13_collective_overlap",
            "Diagnose distributed scaling and collective overlap",
        ),
        ("llm-training", "05_lora_sft", "Perform SFT and LoRA with explicit savings"),
        (
            "llm-training",
            "27_fused_graph_trace",
            "Profile fused operations and CUDA Graphs in training",
        ),
        ("llm-inference", "20_prefix_cache_client", "Reuse prefix KV safely"),
        (
            "llm-inference",
            "30_engine_profile",
            "Operate vLLM and TensorRT-LLM with Triton",
        ),
        (
            "custom-cuda-kernels",
            "07_resource_sweep",
            "Balance blocks, registers, spills, and occupancy",
        ),
        (
            "custom-cuda-kernels",
            "10_hopper_cluster",
            "Gate H100 TMA and thread-block clusters",
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
