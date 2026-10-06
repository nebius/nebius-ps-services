"""Learners have one setup route and a catalog-complete preparation lookup."""

import re

import pytest

from course_bootstrap import catalog, selection
from course_builder.config import COURSES, ROOT
from test_practice_integration import load_validator


GUIDES = sorted(ROOT.glob("*/reference/labs/*.md"))
REFERRAL = "[Lab Guide](../../../lab-guide.html#lab-preparation-scripts)"


def test_course_number_lookup_matches_every_runtime_binding():
    course_names = {
        "GPU Fundamentals": "gpu-fundamentals",
        "GPU Performance Optimization": "gpu-optimizations",
        "LLM Training": "llm-training",
        "LLM Inference": "llm-inference",
        "Custom CUDA Kernels": "custom-cuda-kernels",
        "Advanced Labs: Multi-GPUs Multi-Nodes communication optimization": "advanced-gpu-communication",
    }
    group_anchors = {
        "regular-labs": "regular",
        "cuda-labs": "cuda",
        "communication-labs": "communication",
        "serving-labs": "serving",
        "transformer-engine-lab": "transformer-engine",
    }
    source = (ROOT / "README.md").read_text()
    lookup = source.split("### Find your course and lab number\n", 1)[1].split("### ", 1)[0]
    documented = {}
    for title, numbers, anchor in re.findall(
        r"^\| ([^|]+) \| ([0-9, –]+) \| \[[^]]+\]\(#([a-z-]+)\) \|$", lookup, re.M
    ):
        for item in numbers.split(", "):
            bounds = [int(number) for number in item.split("–")]
            for number in range(bounds[0], bounds[-1] + 1):
                identity = (course_names[title], number)
                assert identity not in documented, identity
                documented[identity] = group_anchors[anchor]
    _, courses = catalog.discover(ROOT / "tools/regular-lab-setup.py")
    definitions = catalog.load_catalog()
    labs, _ = selection.inventory(courses, definitions)
    expected = {
        (identity.split("/")[0], int(identity.split("/")[1][:2])):
        definitions["runtimes"][runtime]["group"]
        for identity, runtime in labs.items()
    }
    assert len(expected) == len(GUIDES) == 110
    assert documented == expected
    assert "Soperator and GPU Performance Tools are reading courses" in lookup
    assert "#optional-container-exercises" in lookup


@pytest.mark.parametrize("path", GUIDES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_lab_has_one_preparation_referral(path):
    validator = load_validator()
    validator.validate_preparation_referral(path.read_text())
    page = (path.parents[2] / "index.html").read_text()
    anchor = "lab-" + path.stem.replace("_", "-")
    article = re.search(rf'<article class="lab" id="{anchor}".*?</article>', page, re.S)
    assert article, path
    assert article[0].count('href="../lab-guide.html#lab-preparation-scripts"') == 1


@pytest.mark.parametrize(
    "extra",
    [
        REFERRAL,
        "[Another setup link](../../../lab-guide.html)",
        '`python3.12 "$HOME/courses/tools/regular-lab-setup.py"`',
        "```bash\npython3 tools/course_setup.py\n```",
        "```bash\npip install torch\n```",
        "```bash\npython3.12 -m venv .venv\n```",
    ],
)
def test_validator_rejects_a_second_preparation_path(extra):
    guide = f"## Before you start\n\n{REFERRAL}\n\n## Practice\n\n{extra}\n"
    with pytest.raises(SystemExit):
        load_validator().validate_preparation_referral(guide)


def test_validator_requires_referral_in_prerequisites():
    with pytest.raises(SystemExit):
        load_validator().validate_preparation_referral(
            f"## Before you start\n\nOne GPU.\n\n## Practice\n\n{REFERRAL}\n"
        )


@pytest.mark.parametrize("course", COURSES)
def test_current_teaching_has_no_separate_installer_commands(course):
    for path in (ROOT / course).rglob("*.md"):
        if path.name == "PUBLICATION-REVIEW.md" or any(
            part in path.parts for part in (".venv", ".runtime", "results", "lab-results", "tools")
        ):
            continue
        source = path.read_text()
        assert not re.search(
            r"[\w-]+-lab-setup\.py|course_setup\.py|install-vendor-candidates\.sh|vendor-environment\.sh",
            source,
        ), path
        preparation_links = re.findall(
            r"\[[^\]]+\]\([^)]*lab-guide\.html#lab-preparation-scripts\)", source
        )
        assert len(preparation_links) <= 1, path
