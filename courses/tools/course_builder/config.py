"""Course registry and presentation constants."""

from __future__ import annotations
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


COURSES = (
    "soperator",
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
    "advanced-gpu-communication",
)

# Reading resources include the shared guide, which is not a course package.
CATALOG_GROUPS = (
    (
        "Getting started",
        "Learn the cluster, then prepare for practice",
        "foundations",
        ("soperator", "lab-guide"),
    ),
    (
        "GPU foundations",
        "Take these in order",
        "foundations",
        ("gpu-fundamentals", "gpu-optimizations"),
    ),
    (
        "Specializations",
        "Choose your direction",
        "specializations",
        ("llm-training", "llm-inference", "custom-cuda-kernels"),
    ),
    (
        "Advanced communication labs",
        "Two eight-H100 workers with InfiniBand",
        "",
        ("advanced-gpu-communication",),
    ),
)
CATALOG_ENTRIES = tuple(name for _, _, _, names in CATALOG_GROUPS for name in names)
GUIDE_TITLE = "Lab Guide"


def catalog_destination(name: str) -> str:
    if name not in CATALOG_ENTRIES:
        raise ValueError(f"unknown catalog resource: {name}")
    return "lab-guide.html" if name == "lab-guide" else f"{name}/index.html"


TEXT_TITLE = "Soperator: A Nebius Slurm cluster running on Kubernetes"


TEXT_FIELDS = (
    "Objective",
    "How it works",
    "Practice",
    "Mental model",
)


LICENSE_PATH = ROOT.parent / "LICENSE"


CATALOG_COPY = {
    "lab-guide": (
        "Prepare once, then run the labs",
        "Set up the shared lab environment, run course experiments, and inspect measurements and profiles in Grafana and NVIDIA Nsight.",
        (
            "Prepare the cluster and course runtimes",
            "Submit labs and find their results",
            "Browse dashboards and profiler reports",
        ),
        ("Shared setup", "Lab execution", "Results"),
    ),
    "advanced-gpu-communication": (
        "Follow the bytes between GPUs",
        "A laboratory course for sixteen H100 GPUs. Diagnose communication, tune training throughput and investigate distributed serving latency.",
        (
            "Measure NVLink and InfiniBand paths",
            "Overlap training communication",
            "Tune Dynamo routing and KV transfers",
        ),
        ("16 H100 GPUs", "InfiniBand", "Labs only"),
    ),
    "soperator": (
        "Understand the cluster behind your jobs",
        "A concise text-only introduction to Slurm and Soperator. Follow resource requests through scheduling, execution and job investigation.",
        (
            "Explain Slurm and Kubernetes responsibilities",
            "Read batch and interactive job commands",
            "Interpret resource requests, status and logs",
        ),
        ("Slurm", "Kubernetes", "Text only · No labs"),
    ),
    "gpu-fundamentals": (
        "Understand the machine",
        "Build a working mental model of GPU execution before you begin optimizing. Connect what your code does to the hardware that runs it.",
        (
            "Explain threads, warps and scheduling",
            "Reason about memory and precision",
            "Read topology and health evidence",
        ),
        ("Execution", "Memory", "Precision"),
    ),
    "gpu-optimizations": (
        "Turn measurements into decisions",
        "Learn a repeatable approach to improving PyTorch workloads: establish a baseline, find the limiter, make one change and measure again.",
        (
            "Asynchronous performance measurement",
            "Use profiler evidence to find bottlenecks",
            "Evaluate changes with equivalent work",
        ),
        ("PyTorch", "Profiling", "Data movement"),
    ),
    "llm-training": (
        "Understand how models learn",
        "Connect the learning objective to the systems that make training possible. Explore correctness, memory, precision and distributed execution.",
        (
            "Preserve the intended learning objective",
            "Manage training memory and precision",
            "Investigate communication and recovery",
        ),
        ("Gradients", "Distributed training", "Recovery"),
    ),
    "llm-inference": (
        "Follow a request from prompt to tokens",
        "Understand how language models generate responses, then explore the trade-offs between serving capacity, latency, throughput and quality.",
        (
            "Explain prefill, decode and KV caches",
            "Measure latency and throughput",
            "Evaluate batching and serving behavior",
        ),
        ("Serving", "KV cache", "Latency"),
    ),
    "custom-cuda-kernels": (
        "Make the hardware your programming model",
        "Learn when a custom kernel is warranted, then build, validate and profile CUDA C++ kernels against a clear correctness and performance baseline.",
        (
            "Map computations to GPU execution",
            "Build and validate CUDA kernels",
            "Compare custom code with library baselines",
        ),
        ("CUDA C++", "Kernel design", "Validation"),
    ),
}


COMMON_GUIDES = (
    "README.md",
    "GLOSSARY.md",
    "VERSIONS.md",
    "reference/benchmark-record.md",
    "reference/lab-mechanisms.md",
    "reference/cluster-smoke-test.md",
    "reference/evidence-security.md",
)


SUPPORTING_GUIDES = {"gpu-optimizations": ("reference/tooling-setup.md",)}


FIELD_CLASSES = {
    "Objective": "lesson-outcome",
    "How it works": "how-it-works",
    "Practice": "practice-links",
    "Mental model": "mental-model",
    "References": "lesson-references",
}


LESSON_FIELDS = tuple(FIELD_CLASSES)[:-1]


LAB_SECTIONS = (
    "Before you start",
    "Concepts and code path",
    "Practice",
    "Check your results",
    "Investigate the behavior",
    "If something goes wrong",
    "Takeaways and next step",
)


SHARED_GUIDE_SECTIONS = (
    "How to set up the lab",
    "How to run the labs",
    "Browsing Grafana and Nsight Profilers",
)
