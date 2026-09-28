"""Exercise the declared four-case comparison contract through the publisher."""

import copy
import json

import pytest
from test_observability_integration import ROOT, load, result


def compute_pair(profile):
    width = 1024 if profile == "small" else 4096
    pair = []
    for mode, dtype, itemsize in [
        ("baseline", "float32", 4),
        ("optimized", "bfloat16", 2),
    ]:
        document = result(case="compute", mode=mode)
        document.update(
            lab_id="14_profiler_bottlenecks",
            profile=profile,
            correctness={"finite_output": True, "numerically_equivalent": True},
            measurements={
                "case": "compute",
                "mode": mode,
                "width": width,
                "dtype": dtype,
                "input_scale_divisor": width**0.5,
                "useful_operations": 2 * width**3,
                "minimum_input_output_bytes": 3 * width**2 * itemsize,
                "timing": {"median_ms": 2},
                "checksum": 0.0001,
            },
        )
        pair.append(document)
    return pair


def recipe():
    return json.loads(
        (ROOT / "gpu-optimizations/reference/observability.json").read_text()
    )["labs"]["14_profiler_bottlenecks"]


@pytest.mark.parametrize("profile", ["small", "large"])
def test_compute_mode_can_change_precision_and_derived_logical_bytes(profile):
    baseline, candidate = compute_pair(profile)
    rows = load("publish_results").comparison_payload(baseline, candidate, recipe())
    assert ("timing_median_seconds", "baseline", "value", 0.002) in rows
    assert ("timing_median_seconds", "candidate", "value", 0.002) in rows


@pytest.mark.parametrize("field", ["width", "input_scale_divisor", "useful_operations"])
def test_compute_precision_change_preserves_workload(field):
    baseline, candidate = compute_pair("small")
    candidate["measurements"][field] *= 2
    with pytest.raises(ValueError, match="workload invariant: " + field):
        load("publish_results").comparison_payload(baseline, candidate, recipe())


@pytest.mark.parametrize("field", ["dtype", "minimum_input_output_bytes"])
def test_compute_repeat_cannot_change_precision_or_logical_bytes(field):
    baseline, _ = compute_pair("small")
    candidate = copy.deepcopy(baseline)
    candidate["measurements"][field] = (
        "bfloat16" if field == "dtype" else baseline["measurements"][field] // 2
    )
    with pytest.raises(ValueError, match="workload invariant: " + field):
        load("publish_results").comparison_payload(baseline, candidate, recipe())
