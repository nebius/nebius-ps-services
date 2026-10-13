"""Guard specific explanatory bridges, not automated semantic completeness."""

import ast


from test_course_content_contract import ROOT
from test_substantive_editorial import lesson


def test_compact_guides_explain_purpose_before_submission():
    for path in ROOT.glob("*/reference/labs/*.md"):
        purpose = path.read_text().split("\n\n", 1)[1].split("\n## ", 1)[0]
        assert purpose.strip() and "sbatch " not in purpose
        assert len(purpose.split()) <= 100


def test_lora_teaches_composition_before_counting_savings():
    primer = lesson("llm-training", "Parameter-efficient adaptation")["How it works"]
    assert "B(Ax)" in primer
    assert "[3, 7]" in primer
    x = [3, 4]
    a_x = x[0]
    b_ax = [0, 2 * a_x]
    result = [base + 0.5 * update for base, update in zip(x, b_ax, strict=True)]
    assert result == [3, 7]


def test_training_capstone_identifies_its_own_workload():
    guide = (ROOT / "llm-training/reference/labs/31_training_capstone.md").read_text()
    assert "linear training step" in guide and "preserving the intended update" in guide
    assert "--variant-order baseline-first" in guide


def test_crossover_scales_tolerance_by_the_cpu_reference():
    source = (ROOT / "gpu-fundamentals/labs/01_cpu_gpu_crossover.py").read_text()
    checks = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "allclose"
    ]
    assert len(checks) == 1
    assert [ast.unparse(arg) for arg in checks[0].args[:2]] == ["result", "reference"]
    # A boundary fixture demonstrates why argument order matters even though
    # absolute error is symmetric: the relative allowance is not symmetric.
    reference, observed = 2.0, 2.0000210001
    error = abs(observed - reference)
    assert error > 1e-6 + 1e-5 * abs(reference)
    assert error <= 1e-6 + 1e-5 * abs(observed)
