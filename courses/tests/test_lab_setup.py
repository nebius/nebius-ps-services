"""One shared guide replaces numbered setup without losing runtime prerequisites."""

from course_builder import build as cb_build, config as cb_config, markdown as cb_markdown, metadata as cb_metadata, pages as cb_pages
import json
import os
import re
import subprocess
import sys

import pytest
from test_course_content_contract import COURSES, ROOT
from test_practice_integration import load_validator

PRACTICAL = (*COURSES, "advanced-gpu-communication")


@pytest.mark.parametrize(
    ("relative", "arguments"),
    [
        ("llm-training/README.md", "labs/32_learning_basics.py --device cpu"),
        (
            "llm-training/reference/labs/32_learning_basics.md",
            "labs/32_learning_basics.py --device cpu",
        ),
        ("llm-inference/README.md", "labs/35_inference_basics.py --device cpu"),
        (
            "llm-inference/reference/labs/35_inference_basics.md",
            "labs/35_inference_basics.py --device cpu",
        ),
        ("llm-training/reference/cluster-smoke-test.md", "-m pip check"),
        ("llm-inference/reference/cluster-smoke-test.md", "-m pip check"),
    ],
)
def test_direct_commands_use_the_restored_runtime(tmp_path, relative, arguments):
    source = (ROOT / relative).read_text()
    command = re.search(
        r'(?:python3?|"\$COURSE_PYTHON") ' + re.escape(arguments), source
    ).group()
    interpreter = tmp_path / "prepared python"
    interpreter.write_text(
        f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n"
    )
    interpreter.chmod(0o700)
    for name in ("python", "python3"):
        system = tmp_path / name
        system.write_text("#!/bin/sh\nexit 37\n")
        system.chmod(0o700)
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env={**os.environ, "PATH": str(tmp_path), "COURSE_PYTHON": str(interpreter)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, (
        "The documented command bypassed the prepared course interpreter"
    )
    assert json.loads(result.stdout) == arguments.split()


@pytest.mark.parametrize("course", PRACTICAL)
def test_courses_link_shared_setup_without_a_numbered_setup_lab(course):
    root = ROOT / course
    metadata = cb_metadata.course_metadata(root)
    document = (root / "index.html").read_text()
    validator = load_validator()
    validator.ROOT = root
    validator.validate_shared_setup_link(document, metadata)
    assert "setup_guide" not in metadata
    assert not (root / "reference/setup.md").exists()
    assert "Lab 00" not in document
    assert all(not row["path"].startswith("labs/00_") for row in metadata["labs"])
    recipes = json.loads((root / "reference/observability.json").read_text())
    assert recipes["setup"]["lab"] == "environment_readiness"
    assert recipes["setup"]["title"] == "Environment readiness"


def test_shared_guide_matches_source_and_required_runtime_routes():
    source = cb_metadata.shared_guide_source()
    assert (
        tuple(re.findall(r"^## (.+)$", source, re.M)) == cb_config.SHARED_GUIDE_SECTIONS
    )
    assert (ROOT / "lab-guide.html").read_text() == cb_pages.render_shared_guide()
    owners = {
        "README.md": (
            "requirements-mechanics.txt",
            "--workload cuda",
            "--workload torch",
        ),
        "llm-inference/README.md": (
            "requirements-serving.txt",
            "COURSE_SERVING_PYTHON",
        ),
        "custom-cuda-kernels/reference/labs/13_h100_preflight.md": (
            "CUTLASS_ROOT",
            "CUDA_IMAGE_DIGEST",
            "COURSE_BUILD_DIR",
        ),
        "advanced-gpu-communication/reference/labs/10_nccl_tests_report.md": (
            "COURSE_MPI",
        ),
        "advanced-gpu-communication/README.md": ("DYNAMO_UCX_PREFIX", "COURSE_ETCD"),
        "advanced-gpu-communication/reference/labs/32_dynamo_disaggregation.md": (
            "MODEL_PATH",
        ),
    }
    for relative, values in owners.items():
        document = (ROOT / relative).read_text()
        for value in values:
            assert value in document
    for command in re.findall(r"```bash\n(.*?)\n```", source, re.S):
        checked = subprocess.run(
            ["bash", "-n"], input=command, text=True, capture_output=True
        )
        assert checked.returncode == 0, checked.stderr


def test_shared_guide_and_lab_referrals_render_as_links():
    document = cb_pages.render_shared_guide()
    for course in cb_config.COURSES:
        assert f'href="{course}/index.html"' in document
    assert 'href="index.html">Browse the catalog</a>' in document
    assert 'href="#license"' in document
    assert (
        'href="https://github.com/nebius/nebius-ps-services/blob/main/courses/skills/run-labs/SKILL.md"'
        in document
    )
    # Relocated prerequisites must land on rendered content, not raw Markdown
    # or a plausible-looking fragment that the course page does not contain.
    for target in (
        "llm-inference/index.html#guide-readme-serving-runtime-preparation",
        "custom-cuda-kernels/index.html#lab-13-h100-preflight",
        "advanced-gpu-communication/index.html#guide-readme-runtime-preparation",
    ):
        assert f'href="{target}"' in document
        page, fragment = target.split("#")
        assert f'id="{fragment}"' in (ROOT / page).read_text()
    assert 'href="#browsing-grafana-and-nsight-profilers"' in document
    for course in PRACTICAL:
        page = (ROOT / course / "index.html").read_text()
        assert (
            'href="../lab-guide.html#how-to-set-up-the-lab">environment setup</a>'
            in page
        )


@pytest.mark.parametrize(
    "target",
    [
        "../../private.html",
        "../lab-guide.html#unknown",
        "unknown/index.html",
        "javascript:alert",
    ],
)
def test_inline_still_rejects_unrecognized_local_destinations(target):
    with pytest.raises(ValueError, match="unresolved destination"):
        cb_markdown.inline(f"[label]({target})")


def test_documented_vendor_runtime_restores_in_a_clean_shell(tmp_path):
    source = (ROOT / "README.md").read_text()
    runtime = tmp_path / "courses/.runtime/advanced-gpu-communication.sh"
    runtime.parent.mkdir(parents=True)
    save_base = re.search(
        r"declare -p COURSE_TOOLS[^\n]+ \\\n  > [^\n]+", source
    ).group()
    vendor = (ROOT / "advanced-gpu-communication/README.md").read_text()
    save_vendor = re.search(r"declare -p COURSE_ETCD[^\n]+", vendor).group()
    model = (
        ROOT / "advanced-gpu-communication/reference/labs/32_dynamo_disaggregation.md"
    ).read_text()
    save_vendor += "\n" + re.search(r"declare -p MODEL_PATH[^\n]+", model).group()
    values = {
        name: str(tmp_path / name)
        for name in (
            "COURSE_TOOLS",
            "COURSE_PUBLISH_PYTHON",
            "COURSE_PYTHON",
            "COURSE_TORCHRUN",
            "COURSE_CUDNN_LIB",
            "COURSE_ETCD",
            "MODEL_PATH",
            "UCX_PREFIX",
            "DYNAMO_UCX_PREFIX",
        )
    }
    minimal = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    saved = subprocess.run(
        ["/bin/bash", "-eu", "-c", save_base + "\n" + save_vendor],
        env={**minimal, **values, "COURSE": "advanced-gpu-communication"},
        capture_output=True,
        text=True,
    )
    assert saved.returncode == 0, saved.stderr
    restored = subprocess.run(
        [
            "/bin/bash",
            "-eu",
            "-c",
            'source "$1"; source ./env/vendor-environment.sh; printf "%s\\n" "$UCX_PREFIX" "$COURSE_ETCD" "$COURSE_PYTHON"',
            "restore",
            str(runtime),
        ],
        cwd=ROOT / "advanced-gpu-communication",
        env=minimal,
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    assert restored.stdout.splitlines() == [
        values["UCX_PREFIX"],
        values["COURSE_ETCD"],
        str(tmp_path / "courses/.venvs/advanced-gpu-communication/bin/python"),
    ]


@pytest.mark.parametrize("defect", ["empty", "extra-section", "order", "symlink"])
def test_shared_guide_rejects_invalid_source(tmp_path, monkeypatch, defect):
    source = cb_metadata.shared_guide_source()
    path = tmp_path / "README.md"
    if defect == "empty":
        source = ""
    elif defect == "extra-section":
        source += "\n## Unrelated\n"
    elif defect == "order":
        source = source.replace("## How to set up the lab", "## How to run the labs", 1)
    path.write_text(source)
    if defect == "symlink":
        target = tmp_path / "other.md"
        path.rename(target)
        path.symlink_to(target)
    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "publication_preflight", lambda outputs: {})
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="shared guide"):
        cb_metadata.shared_guide_source()


@pytest.mark.parametrize(
    "defect", ["missing-link", "legacy-metadata", "embedded-setup"]
)
def test_course_validator_rejects_old_or_missing_setup_contract(defect):
    validator = load_validator()
    document = (ROOT / "gpu-fundamentals/index.html").read_text()
    metadata = {}
    if defect == "missing-link":
        document = document.replace(
            'href="../lab-guide.html"', 'href="missing.html"'
        )
    elif defect == "legacy-metadata":
        metadata["setup_guide"] = "reference/setup.md"
    else:
        document += '<article class="setup-guide"></article>'
    with pytest.raises(SystemExit):
        validator.validate_shared_setup_link(document, metadata)


@pytest.mark.parametrize("course", COURSES)
def test_practice_has_local_commands_without_reading_or_setup_detours(course):
    root = ROOT / course
    for path in (root / "reference/labs").glob("*.md"):
        document = path.read_text()
        practice = document.split("\n## Practice\n")[1].split("\n## ")[0]
        prose = re.sub(r"```.*?```", "", practice, flags=re.S)
        assert "Theory preparation" not in document, path
        assert not re.search(r"\bLessons?\b|README|runbook|walkthrough|\]\(", prose), (
            path
        )
        assert "```bash" in practice, path
        assert len(prose.split()) <= 170, path


@pytest.mark.parametrize("existing", [None, "stale shared guide"])
def test_build_check_rejects_missing_or_stale_shared_page(
    tmp_path, monkeypatch, existing
):
    import sys

    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "publication_preflight", lambda outputs: {})
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "render_shared_guide", lambda: "current guide")
    monkeypatch.setattr(cb_build, "render_catalog", lambda: "current catalog")
    monkeypatch.setattr(cb_build, "render_course", lambda course: "current course")
    monkeypatch.setattr(cb_build, "course_metadata", lambda course: {"profile": "text-only"})
    monkeypatch.setattr(
        sys, "argv", ["build_course_html.py", "gpu-fundamentals", "--check"]
    )
    if existing is not None:
        (tmp_path / "lab-guide.html").write_text(existing)
    with pytest.raises(SystemExit, match="stale or missing.*shared guide"):
        cb_build.main()


def test_shared_guide_embeds_existing_monitoring_diagram():
    import base64

    source = cb_metadata.shared_guide_source()
    document = cb_pages.render_shared_guide()
    assert source.index("### How measurements reach Grafana") < source.index("### Install Grafana on the cluster")
    match = re.search(r'<img alt="([^"]+)" data-source="docs/grafana.png" src="data:image/png;base64,([^"]+)">', document)
    assert match and "telemetry" in match[1]
    assert base64.b64decode(match[2], validate=True) == (ROOT / "docs/grafana.png").read_bytes()
    assert "# Performance Engineering Courses\n" in source
    assert "nebius-cxcli grafana install --config ./config.yaml --target CLUSTER_TARGET --pushgateway" in source


@pytest.mark.parametrize("relative", ["../private.png", "docs/../private.png", "/tmp/private.png", "https://example.invalid/image.png", "docs/missing.png"])
def test_shared_guide_rejects_unsafe_or_missing_images(relative):
    with pytest.raises(ValueError):
        cb_markdown.block(f"![Diagram]({relative})", images=True)


def test_shared_guide_rejects_symlink_and_non_png(tmp_path, monkeypatch):
    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "publication_preflight", lambda outputs: {})
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    image = tmp_path / "docs/diagram.png"
    image.write_bytes(b"not a PNG")
    with pytest.raises(ValueError, match="PNG bytes"):
        cb_markdown.guide_image("docs/diagram.png", "Diagram")
    image.unlink()
    image.symlink_to(ROOT / "docs/grafana.png")
    with pytest.raises(ValueError, match="unsafe"):
        cb_markdown.guide_image("docs/diagram.png", "Diagram")


def test_shared_guide_requires_alt_text_and_escapes_it():
    with pytest.raises(ValueError, match="alternative text"):
        cb_markdown.guide_image("docs/grafana.png", "")
    rendered = cb_markdown.guide_image("docs/grafana.png", '<script>"diagram"</script>')
    assert 'alt="&lt;script&gt;&quot;diagram&quot;&lt;/script&gt;"' in rendered
