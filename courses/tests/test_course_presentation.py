"""Presentation, current-content integrity, and publication boundaries."""

from __future__ import annotations

from course_builder import config as cb_config, content as cb_content, markdown as cb_markdown, metadata as cb_metadata
import html
import importlib.util
import json
import re
from pathlib import Path

import pytest
from test_course_content_contract import COURSES, ROOT
from test_course_review_fixes import load_lab


@pytest.mark.parametrize("course", COURSES)
def test_banner_is_only_title_and_short_guided_hours(course: str) -> None:
    root = ROOT / course
    metadata = json.loads((root / "reference/course.json").read_text())
    hours = metadata["estimated_guided_hours"]
    document = (root / "index.html").read_text()
    assert re.findall(r"<header>(.*?)</header>", document, re.S) == [
        f'<h1>{html.escape(metadata["title"])}</h1><p class="guided-hours">Estimated guided hours: {hours}</p>'
    ]
    assert f"**{hours} hours**" in (root / "README.md").read_text()
    assert f"**{hours} hours**" in (root / "SYLLABUS.md").read_text()
    for item in metadata["labs"]:
        target = "lab-" + cb_markdown.slug(Path(item["path"]).stem)
        lab = re.search(
            rf'<article class="lab" id="{target}".*?</article>', document, re.S
        ).group()
        assert ("Optional extension" if item["optional"] else "Core practice") in lab


@pytest.mark.parametrize("course", COURSES)
def test_pages_share_style_and_navigate_readable_supporting_guides(course: str) -> None:
    root = ROOT / course
    document = (root / "index.html").read_text()
    assert re.findall(r"<style>(.*?)</style>", document, re.S) == [
        (ROOT / "tools/course.css").read_text()
    ]
    for relative in cb_config.COMMON_GUIDES:
        target = cb_content.guide_id(relative)
        assert f'href="#{target}"' in document
        assert f'id="{target}" data-source="{relative}"' in document
    glossary = re.search(
        r'<section[^>]+id="guide-glossary".*?</section>', document, re.S
    ).group()
    assert "<dl><dt>" in glossary
    assert "<pre>" not in glossary
    worksheet = re.search(
        r'<article[^>]+id="guide-reference-benchmark-record".*?</article>',
        document,
        re.S,
    ).group()
    assert "<table>" in worksheet and '<th scope="col">Baseline</th>' in worksheet
    assert 'href="#official-references"' in document


def test_guide_markdown_preserves_order_tables_lists_code_and_safe_links() -> None:
    rendered = cb_markdown.block(
        "## Example\n\nFirst paragraph.\n\n- Item one\n- Item two\n\n"
        "| Factor | Value |\n| --- | --- |\n| Size | 4 |\n\n"
        "```bash\nprintf '<hello>'\n```\n\n"
        "[Run](labs/example.py)",
        {"labs/example.py": "#lab-example"},
        prefix="guide-",
    )
    assert '<h4 id="guide-example">Example</h4>' in rendered
    assert "<ul><li>Item one</li><li>Item two</li></ul>" in rendered
    assert '<th scope="col">Factor</th>' in rendered
    assert "<td>Size</td><td>4</td>" in rendered
    assert "printf &#x27;&lt;hello&gt;&#x27;" in rendered
    assert '<a href="#lab-example">Run</a>' in rendered
    with pytest.raises(ValueError, match="unresolved destination"):
        cb_markdown.block("[Unsafe](javascript:bad)")
    assert (
        rendered.index("First paragraph")
        < rendered.index("<ul>")
        < rendered.index("<table>")
        < rendered.index("<pre")
    )
    with pytest.raises(ValueError, match="unterminated"):
        cb_markdown.block("```python\nunclosed")


@pytest.mark.parametrize("course", COURSES)
def test_glossary_terms_are_readable_and_current(course: str) -> None:
    root = ROOT / course
    terms = re.findall(r"^- \*\*(.+?)\*\* —", (root / "GLOSSARY.md").read_text(), re.M)
    assert len(terms) >= 15
    document = (root / "index.html").read_text()
    assert all(f"<dt>{cb_markdown.inline(term)}</dt>" in document for term in terms)


def test_every_detailed_visual_has_one_destination_and_reachable_lessons() -> None:
    diagrams: set[str] = set()
    for course in COURSES:
        root = ROOT / course
        document = (root / "index.html").read_text()
        entries = json.loads((root / "reference/visual-manifest.json").read_text())[
            "diagrams"
        ]
        _, _, lessons = cb_metadata.parse_course(root / "COURSE.md")
        for entry in entries:
            identity = str(root / entry["path"])
            assert identity not in diagrams
            diagrams.add(identity)
            svg = (root / entry["path"]).read_text().strip()
            assert svg in document
            target = "detail-" + cb_markdown.slug(Path(entry["path"]).stem)
            for number in entry["lessons"]:
                lesson_id = cb_markdown.slug(lessons[number - 1]["title"])
                lesson = re.search(
                    rf'<section class="lesson" id="{lesson_id}".*?</section>',
                    document,
                    re.S,
                ).group()
                if number == entry["lessons"][0] and entry["home"] == "lesson":
                    assert f'id="{target}"' in lesson
                else:
                    assert f'href="#{target}"' in lesson
    assert len(diagrams) == 54
    advanced = ROOT / "advanced-gpu-communication"
    moved = json.loads((advanced / "reference/visual-manifest.json").read_text())[
        "diagrams"
    ]
    assert len(moved) == 4
    for entry in moved:
        assert (advanced / entry["path"]).read_text().strip() in (
            advanced / "index.html"
        ).read_text()


def test_generated_launch_recipes_use_owned_runtime_and_actual_build_directory() -> (
    None
):
    samples = {
        "advanced-gpu-communication/labs/02_collective_readiness.py": "slurm/two_node.sbatch",
        "advanced-gpu-communication/labs/05_transport_readiness.py": "slurm/two_node.sbatch",
        "advanced-gpu-communication/labs/03_training_readiness.py": "slurm/training_two_rank.sbatch",
        "advanced-gpu-communication/labs/04_inference_readiness.py": "slurm/two_node.sbatch",
        "advanced-gpu-communication/labs/08_distributed_collectives.py": "slurm/two_node.sbatch",
        "advanced-gpu-communication/labs/12_distributed_scaling.py": "slurm/two_node.sbatch",
        "llm-inference/labs/11_serving_client.py": "slurm/vllm_benchmark.sbatch",
        "llm-inference/labs/15_streaming_client.py": "slurm/vllm_streaming_benchmark.sbatch",
        "llm-inference/labs/34_policy_equivalence_client.py": "slurm/vllm_chunked_prefill_ab.sbatch",
        "custom-cuda-kernels/labs/03_tiled_transpose.cu": "${COURSE_BUILD_DIR:?set the completed build directory}/03_tiled_transpose",
    }
    for source, expected in samples.items():
        path = ROOT / source
        course = path.parents[1]
        lessons = (
            []
            if course.name == "advanced-gpu-communication"
            else cb_metadata.parse_course(course / "COURSE.md")[2]
        )
        guides = cb_metadata.lab_guides(
            course, cb_metadata.course_metadata(course), len(lessons)
        )
        guide = next(item for item in guides if item["source"] == path)
        references = {
            name: "#" + cb_content.guide_id(name)
            for name in cb_config.COMMON_GUIDES
            + cb_config.SUPPORTING_GUIDES.get(course.name, ())
        }
        rendered = cb_content.lab_markup(course, guide, lessons, references)
        commands = [
            html.unescape(block)
            for block in re.findall(
                r"<pre[^>]*><code>(.*?)</code></pre>", rendered, re.S
            )
        ]
        assert any(expected in command for command in commands)


def test_private_raw_artifacts_are_rejected_outside_ignored_runtime_directories(
    tmp_path: Path, monkeypatch
) -> None:
    spec = importlib.util.spec_from_file_location(
        "presentation_validator", ROOT / "tools/validate_course_template.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/run.log").write_text("synthetic private output")
    (tmp_path / "COURSE.md").write_text("Public teaching text")
    # The independent resource validator now also covers pre-results courses.
    from course_archives import publish_results

    (tmp_path / "reference/grafana").mkdir(parents=True)
    (tmp_path / "reference/course.json").write_text(json.dumps({"slug": "example", "labs": []}))
    (tmp_path / "reference/grafana/environment_readiness.json").write_text('{}')
    publish_results(tmp_path)
    module.validate_publication_artifacts(module.course_paths())
    for suffix in (".out", ".log", ".qdrep", ".nsys-rep", ".pem"):
        path = tmp_path / f"accidental{suffix}"
        path.write_text("synthetic private output")
        with pytest.raises(SystemExit, match="raw runtime or credential"):
            module.validate_publication_artifacts(module.course_paths())
        path.unlink()


def test_checkpoint_loss_and_every_gradient_are_independent_acceptance_gates() -> None:
    torch = pytest.importorskip("torch")
    with load_lab("llm-training/labs/14_activation_checkpointing.py") as lab:
        loss = torch.tensor(1.0)
        grads = [torch.tensor([1.0, 2.0]), torch.tensor([3.0])]
        assert lab.require_checkpoint_equivalence(
            torch, loss, grads, loss.clone(), grads
        ) == {"loss_allclose": True, "gradients_allclose": True}
        for candidate_loss, candidate_grads in (
            (torch.tensor(2.0), grads),
            (torch.tensor(float("nan")), grads),
            (loss, []),
            (loss, grads[:1]),
            (loss, [grads[0], torch.tensor([4.0])]),
            (loss, [grads[0], torch.tensor([float("nan")])]),
        ):
            with pytest.raises(
                SystemExit, match="Checkpoint loss or full parameter gradients"
            ):
                lab.require_checkpoint_equivalence(
                    torch, loss, grads, candidate_loss, candidate_grads
                )


@pytest.mark.parametrize("mode", ("selective", "full"))
def test_checkpointed_tiny_model_preserves_fp32_cpu_loss_and_all_gradients(
    mode: str,
) -> None:
    torch = pytest.importorskip("torch")
    torch.manual_seed(17)
    with load_lab("llm-training/labs/14_activation_checkpointing.py") as lab:
        model = lab.build_tiny_lm(
            torch, vocab_size=16, hidden_size=8, layers=2, heads=2, max_sequence=4
        )
        inputs = torch.tensor([[1, 2, 3, 4]])
        targets = torch.tensor([[2, 3, 4, 5]])
        loss = torch.nn.functional.cross_entropy(
            model(inputs).flatten(0, 1), targets.flatten()
        )
        loss.backward()
        gradients = [p.grad.detach().clone() for p in model.parameters()]
        model.zero_grad(set_to_none=True)
        candidate = lab.checkpointed_forward(torch, model, inputs, mode=mode)
        candidate_loss = torch.nn.functional.cross_entropy(
            candidate.flatten(0, 1), targets.flatten()
        )
        candidate_loss.backward()
        assert torch.allclose(loss, candidate_loss, rtol=1e-5, atol=1e-6)
        assert all(p.grad is not None for p in model.parameters())
        assert all(
            torch.allclose(a, p.grad, rtol=1e-5, atol=1e-6)
            for a, p in zip(gradients, model.parameters(), strict=True)
        )
