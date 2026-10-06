"""One shared guide replaces numbered setup without losing runtime prerequisites."""

from course_builder import (
    build as cb_build,
    config as cb_config,
    markdown as cb_markdown,
    metadata as cb_metadata,
    pages as cb_pages,
)
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from test_course_content_contract import COURSES, ROOT
from test_practice_integration import load_validator

PRACTICAL = (*COURSES, "advanced-gpu-communication")


def test_fabric_preparation_belongs_to_automatic_setup():
    definitions = json.loads((ROOT / "tools/course_bootstrap/catalog.json").read_text())
    fabric = definitions["components"]["fabric-perftest"]
    assert fabric["recipe"] == "fabric"
    assert "toolchain" in fabric["depends"]
    assert "fabric/perftest/bin/ib_write_bw" in fabric["artifacts"]
    guide = (ROOT / "advanced-gpu-communication/reference/labs/01_fabric_topology.md").read_text()
    assert "tools/install_fabric_tools.py --prefix" not in guide
    assert "source tools/course_env.sh 01_fabric_topology --lab" in guide


def test_monitoring_verification_restores_course_from_a_new_terminal(tmp_path):
    guide = (ROOT / "README.md").read_text()
    block = next(
        block
        for block in re.findall(r"```bash\n(.*?)```", guide, re.S)
        if "tools/verify_monitoring.py" in block
    )
    checkout = tmp_path / "checkout with spaces/courses"
    course = checkout / "gpu-fundamentals"
    (course / "tools").mkdir(parents=True)
    (course / "tools/verify_monitoring.py").write_text("# fixture\n")
    setup = tmp_path / "monitoring setup"
    setup.mkdir()
    (setup / "laptop-environment.sh").write_text(
        f'export COURSE_SETUP_DIR="{setup}"\n'
        'export KUBECONFIG="/fixture/kubeconfig"\n'
        'export CLUSTER_CONTEXT="fixture-context"\n'
    )
    interpreter = tmp_path / ".gpu-course-tools/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
        "assert Path(sys.argv[1]).is_file(), 'verification must use the selected course'\n"
        'assert sys.argv[-1] == "fixture-context"\n'
        "print(Path.cwd())\n"
    )
    interpreter.chmod(0o700)
    block = block.replace("<absolute setup directory>", str(setup))
    block = block.replace("<absolute checkout path>/courses", str(checkout))
    completed = subprocess.run(
        ["bash", "-eu", "-c", f'source "{setup}/laptop-environment.sh"\n' + block],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": os.defpath},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(course)


@pytest.mark.parametrize(
    ("relative", "arguments"),
    [
        ("llm-training/README.md", "labs/32_learning_basics.py --device cpu"),
        (
            "llm-training/reference/labs/32_learning_basics.md",
            "slurm/32_learning_basics.sbatch --device cpu",
        ),
        ("llm-inference/README.md", "labs/35_inference_basics.py --device cpu"),
        (
            "llm-inference/reference/labs/35_inference_basics.md",
            "slurm/35_inference_basics.sbatch --device cpu",
        ),
        ("llm-training/reference/cluster-smoke-test.md", "-m pip check"),
        ("llm-inference/reference/cluster-smoke-test.md", "-m pip check"),
    ],
)
def test_documented_commands_use_the_restored_runtime(tmp_path, relative, arguments):
    from native_job_fixtures import executable, local_commands, prepare_job

    source = (ROOT / relative).read_text()
    env = {**os.environ, **local_commands(tmp_path)}
    interpreter = tmp_path / "prepared python"
    executable(
        interpreter,
        "import json,sys\nfrom pathlib import Path\n"
        "Path('prepared-argv.json').write_text(json.dumps(sys.argv[1:]))\n",
    )
    env["COURSE_PYTHON"] = str(interpreter)
    if "/reference/labs/" in relative:
        course = relative.split("/", 1)[0]
        job = arguments.split()[0]
        lab = job.split("/")[-1].removesuffix(".sbatch")
        prepare_job(tmp_path, course, lab)
        (tmp_path / "slurm").mkdir()
        (tmp_path / job).write_text((ROOT / course / job).read_text())
        command = next(
            block
            for block in re.findall(r"```bash\n(.*?)```", source, re.S)
            if block.startswith("sbatch ")
        )
        executable(
            tmp_path / "bin/sbatch",
            "import os,sys\n"
            "args = sys.argv[1:]\n"
            "while args[0].startswith('--'): args.pop(0)\n"
            f"assert args[0] == {job!r}\n"
            "os.execvpe('bash', ['bash', *args], os.environ)\n",
        )
        expected = [f"labs/{lab}.py", "--workload", "small", "--device", "cpu"]
    else:
        command = re.search(
            r'(?:python3?|"\$COURSE_PYTHON") ' + re.escape(arguments), source
        ).group()
        expected = arguments.split()
    for name in ("python", "python3"):
        executable(tmp_path / "bin" / name, "raise SystemExit(37)\n")
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "prepared-argv.json").read_text()) == expected
    if "/reference/labs/" in relative:
        assert not any(
            "gpu" in arg for arg in json.loads((tmp_path / "srun.json").read_text())
        )


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
            "regular-lab-setup.py",
            "--workload cuda",
            "--workload torch",
        ),
        "llm-inference/README.md": (
            "requirements-serving.txt",
            "requirements-mechanics.txt",
        ),
        "custom-cuda-kernels/reference/labs/13_h100_preflight.md": (
            "CUTLASS_ROOT",
            "CUDA_HOME",
            "COURSE_BUILD_DIR",
        ),
        "advanced-gpu-communication/reference/labs/10_nccl_tests_report.md": (
            "COURSE_MPI",
        ),
        "advanced-gpu-communication/README.md": ("DYNAMO_UCX_PREFIX", "COURSE_ETCD"),
        "advanced-gpu-communication/reference/labs/32_dynamo_disaggregation.md": (
            "COURSE_MODEL_DIR",
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
    assert 'href="index.html">Browse the courses catalog</a>' in document
    assert (
        'href="https://nebius.github.io/nebius-ps-services/courses/index.html">Explore the courses</a>'
        in document
    )
    for removed in (
        "GitHub Pages",
        "Course maintainer guide",
        "no lab-kit ZIP is needed",
        "./build-courses.sh",
        "The seven courses share one reading format",
    ):
        assert removed not in document
    assert "<h1>Lab Guide</h1>" in document
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
        assert 'href="../lab-guide.html#lab-preparation-scripts">Lab Guide</a>' in page


def test_shared_guide_toc_matches_topic_and_subsection_order():
    source = cb_metadata.shared_guide_source()
    document = cb_pages.render_shared_guide()
    navigation = document.split('<nav aria-label="Lab guide contents">', 1)[1].split(
        "</nav>", 1
    )[0]
    toc = ET.fromstring(
        navigation.split("<summary>Table of contents</summary>", 1)[1].removesuffix(
            "</details>"
        )
    )
    chunks = re.split(r"^## (.+)\n", source, flags=re.M)
    topics = list(zip(chunks[1::2], chunks[2::2], strict=True))
    assert [item.find("a").text for item in toc] == list(
        cb_config.SHARED_GUIDE_SECTIONS
    )
    targets = cb_build.PageTargets(document.encode()).ids
    for item, (heading, content) in zip(toc, topics, strict=True):
        assert item.find("a").get("href") == "#" + cb_markdown.slug(heading)
        children = item.findall("ul/li/a")
        expected = re.findall(r"^### (.+)$", content, re.M)
        assert [child.text for child in children] == expected
        assert [child.get("href") for child in children] == [
            "#" + cb_markdown.slug(title) for title in expected
        ]
        assert all(child.get("href")[1:] in targets for child in children)


def test_shared_guide_toc_ignores_fenced_headings(monkeypatch):
    source = cb_metadata.shared_guide_source().replace(
        "### Regular labs\n",
        "### Regular labs\n\n```bash\n### Example comment\n```\n",
        1,
    )
    monkeypatch.setattr(cb_pages, "shared_guide_source", lambda: source)
    document = cb_pages.render_shared_guide()
    assert "### Example comment" in document
    assert 'href="#example-comment"' not in document
    assert 'id="example-comment"' not in document
    assert 'href="#regular-labs"' in document


def test_readme_browser_pointer_and_attribution_have_distinct_html_homes(monkeypatch):
    source = cb_metadata.shared_guide_source()
    pointer = (
        "[Read this guide online]"
        "(https://nebius.github.io/nebius-ps-services/courses/lab-guide.html)."
    )
    attribution = (
        "© 2026 Nebius B.V. Free educational material under "
        "[Apache License 2.0](../LICENSE)."
    )
    assert source.count(pointer) == 1
    assert "## How to set up the lab\n\n" + pointer + "\n\n" in source
    assert source.count(attribution) == 1
    assert source.endswith("\n\n" + attribution)

    document = cb_pages.render_shared_guide()
    assert "Read this guide online" not in document
    assert document.count("© 2026 Nebius B.V.") == 1
    assert "Free educational material under" not in document
    assert document.count('<footer class="license-footer">') == 1
    assert '</section><footer class="license-footer">' in document
    assert document.endswith("</footer></main></div></body></html>")
    assert "Third-party materials retain their respective licenses." in document
    assert "contains the same instructions." in document

    # Removing only those presentation fragments from the source must produce
    # identical HTML; every other paragraph and command remains rendered.
    article = source.replace(pointer + "\n\n", "", 1).removesuffix("\n\n" + attribution)
    monkeypatch.setattr(cb_pages, "shared_guide_source", lambda: article)
    assert cb_pages.render_shared_guide() == document


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


def test_documented_setup_uses_one_automatic_runtime_contract():
    source = (ROOT / "README.md").read_text()
    assert 'python3.12 "$HOME/courses/tools/regular-lab-setup.py"' in source
    assert "declare -p COURSE_PYTHON" not in source
    assert "regular-lab-setup.py\" prepare" not in source
    assert not (ROOT / "advanced-gpu-communication/env/vendor-environment.sh").exists()
    assert not (ROOT / "advanced-gpu-communication/env/install-vendor-candidates.sh").exists()
    definitions = json.loads((ROOT / "tools/course_bootstrap/catalog.json").read_text())
    for row in definitions["runtimes"].values():
        if "bridge" in row["components"]:
            assert row["environment"]["COURSE_PYTHON"] == "{bridge}/venv/bin/python"


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
        document = document.replace('href="../lab-guide.html"', 'href="missing.html"')
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
    monkeypatch.setattr(
        cb_build, "course_metadata", lambda course: {"profile": "text-only"}
    )
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
    assert source.index("### Publish a measured comparison") < source.index(
        "![Course measurements"
    )
    assert source.index("![Course measurements") < source.index(
        "### Capture and qualify profiling"
    )
    match = re.search(
        r'<img alt="([^"]+)" data-source="docs/grafana.png" src="data:image/png;base64,([^"]+)">',
        document,
    )
    assert match and "telemetry" in match[1]
    assert (
        base64.b64decode(match[2], validate=True)
        == (ROOT / "docs/grafana.png").read_bytes()
    )
    assert "# Performance Engineering Courses\n" in source
    assert (
        'nebius-cxcli grafana install --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" --pushgateway'
        in source
    )


@pytest.mark.parametrize(
    "relative",
    [
        "../private.png",
        "docs/../private.png",
        "/tmp/private.png",
        "https://example.invalid/image.png",
        "docs/missing.png",
    ],
)
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
