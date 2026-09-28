#!/usr/bin/env python3
"""Render course-owned Grafana JSON from explicit lab measurement recipes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
    "advanced-gpu-communication",
)
DATASOURCE = {"type": "prometheus", "uid": "course-soperator-metrics"}


def selected_expression(selector: str) -> str:
    # Resolve the selection at wall time before applying any unit conversion.
    # An @ modifier on outer arithmetic can produce an empty implicit rollup.
    return f"({selector} @ now())"


def case_ordering(metric: dict) -> list[dict]:
    """Keep positional measurements in artifact order after the label join."""
    if "*" not in metric["path"]:
        return []
    if metric["path"].count("*") != 1:
        raise ValueError("Dashboard ordering requires one measurement case index")
    numeric_labels = bool(metric.get("case_values"))
    field = "case" if numeric_labels else "course_case_index"
    transforms = []
    if not numeric_labels:
        prefix = re.escape(metric.get("case", "value"))
        transforms.append(
            {
                "id": "extractFields",
                "options": {
                    "source": "case",
                    "format": "regexp",
                    "regExp": rf"/^{prefix}-(?<course_case_index>\d+)$/",
                    "replace": False,
                },
            }
        )
    transforms.extend(
        [
            {
                "id": "convertFieldType",
                "options": {
                    "conversions": [{"targetField": field, "destinationType": "number"}]
                },
            },
            {"id": "sortBy", "options": {"sort": [{"field": field, "desc": False}]}},
        ]
    )
    if numeric_labels:
        transforms.append(
            {
                "id": "convertFieldType",
                "options": {
                    "conversions": [
                        {"targetField": "case", "destinationType": "string"}
                    ]
                },
            }
        )
    return transforms


def panel(
    number: int,
    title: str,
    expression: str,
    unit: str,
    description: str,
    *,
    instant: bool = True,
    legend_format: str = "{{Hostname}} GPU {{gpu}}",
) -> dict:
    entry = {
        "id": number,
        "title": title,
        "type": "table" if instant else "timeseries",
        "description": description,
        "datasource": DATASOURCE,
        "gridPos": {"x": 0, "y": (number - 1) * 7, "w": 24, "h": 7},
        "targets": [
            {
                "refId": "A",
                "expr": selected_expression(expression) if instant else expression,
                "instant": instant,
                "range": not instant,
                "format": "table" if instant else "time_series",
                "legendFormat": legend_format,
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit if not instant else "none",
                "noValue": "No measured data",
            },
            "overrides": [],
        },
        "options": {"showHeader": True}
        if instant
        else {"legend": {"displayMode": "table", "placement": "bottom"}},
    }
    if instant:
        entry["transformations"] = [
            {
                "id": "filterFieldsByName",
                "options": {
                    "include": {"names": ["slot", "case", "Value", "Value #A"]}
                },
            }
        ]
        entry["fieldConfig"]["overrides"] = [
            {
                "matcher": {"id": "byRegexp", "options": "^Value(?: #A)?$"},
                "properties": [{"id": "unit", "value": unit}],
            }
        ]
    return entry


def dashboard(course: str, recipe: dict) -> dict:
    lab = recipe["lab"]
    selected = (
        f'course="{course}",lab="{lab}",workspace="$workspace",profile="$profile"'
    )
    panels = [
        panel(
            1,
            "Selected comparison generation",
            f"course_lab_publication_generation{{{selected}}}",
            "none",
            "These are explicitly selected completed runs. Submission of another job does not replace them. Summary panels always show the current selected pair, even with a historical time range. Check generation and experiment timestamps.",
        )
    ]
    for metric in recipe["metrics"]:
        chart = panel(
            len(panels) + 1, metric["title"], "", metric["unit"], metric["description"]
        )
        chart.update(
            type="barchart",
            options={
                "xField": "case",
                "orientation": "vertical",
                "groupWidth": 0.7,
                "legend": {"displayMode": "list", "placement": "bottom"},
            },
        )
        if "*" in metric["path"]:
            chart["options"]["xTickLabelSpacing"] = 100
            chart["options"]["xTickLabelRotation"] = 45
        chart["targets"] = [
            {
                "refId": ref,
                "expr": selected_expression(
                    f'course_lab_{metric["name"]}{{{selected},slot="{slot}"}}'
                ),
                "instant": True,
                "format": "table",
            }
            for ref, slot in (("A", "baseline"), ("B", "candidate"))
        ]
        chart["transformations"] = [
            {
                "id": "joinByField",
                "options": {"byField": "case", "mode": "outerTabular"},
            },
            *case_ordering(metric),
            {
                "id": "filterFieldsByName",
                "options": {"include": {"names": ["case", "Value #A", "Value #B"]}},
            },
        ]
        chart["fieldConfig"]["overrides"] = [
            {
                "matcher": {"id": "byName", "options": "Value #" + ref},
                "properties": [
                    {"id": "displayName", "value": slot},
                    {"id": "unit", "value": metric["unit"]},
                ],
            }
            for ref, slot in (("A", "Baseline"), ("B", "Candidate"))
        ]
        case_properties = [{"id": "unit", "value": "string"}]
        if "*" in metric["path"]:
            # Grafana formats skipped axis ticks as null case values. Keep them
            # blank while preserving the missing-data message for measurements.
            case_properties.append({"id": "noValue", "value": ""})
        chart["fieldConfig"]["overrides"].append(
            {
                "matcher": {"id": "byName", "options": "case"},
                "properties": case_properties,
            }
        )
        panels.append(chart)
    for name, title, unit in (
        ("correctness", "Correctness of selected results", "none"),
        ("started_unix_seconds", "Experiment start", "dateTimeAsIso"),
        ("ended_unix_seconds", "Experiment end", "dateTimeAsIso"),
        ("slurm_job_id", "Slurm allocation", "none"),
    ):
        entry = panel(
            len(panels) + 1,
            title,
            f"course_lab_{name}{{{selected}}}",
            unit,
            "Choose the experiment's UTC interval for the telemetry panels; scrape time is not execution time.",
        )
        if name.endswith("unix_seconds"):
            entry["targets"][0]["expr"] += " * 1000"
        panels.append(entry)
    if recipe.get("gpu_telemetry", True):
        gpu_selector = 'Hostname=~"${gpu_node:regex}",gpu=~"${gpu:regex}"'
        for metric, title, unit in (
            (
                "DCGM_FI_DEV_GPU_UTIL",
                "GPU activity during the selected window",
                "percent",
            ),
            ("DCGM_FI_DEV_FB_USED", "Framebuffer memory used", "decmbytes"),
            ("DCGM_FI_DEV_POWER_USAGE", "GPU power", "watt"),
            ("DCGM_FI_DEV_GPU_TEMP", "GPU temperature", "celsius"),
        ):
            panels.append(
                panel(
                    len(panels) + 1,
                    title,
                    f"{metric}{{{gpu_selector}}}"
                    + (" * 1048576" if metric == "DCGM_FI_DEV_FB_USED" else ""),
                    "bytes" if metric == "DCGM_FI_DEV_FB_USED" else unit,
                    "Sampled device context, not per-kernel timing or proof of exclusive job attribution. Empty data is unknown, never zero.",
                    instant=False,
                )
            )
    for expression, title, unit in (
        (
            '100 * (1 - avg by(instance)(rate(node_cpu_seconds_total{mode="idle",instance=~"${node:regex}"}[5m])))',
            "Node CPU busy",
            "percent",
        ),
        (
            'node_memory_MemAvailable_bytes{instance=~"${node:regex}"}',
            "Node available memory",
            "bytes",
        ),
    ):
        panels.append(
            panel(
                len(panels) + 1,
                title,
                expression,
                unit,
                "Node context; correlate the instance and experiment interval. Other jobs can contribute. Missing series means unavailable telemetry.",
                instant=False,
                legend_format="{{instance}}",
            )
        )
    if lab == "01_cpu_gpu_crossover":
        chart = panel(
            len(panels) + 1,
            "CPU–GPU crossover by element count",
            "",
            "s",
            "Compare CPU, resident-GPU and transfer-inclusive GPU times at the same element count. NVTX describes submission phases; timers define measurement scope.",
        )
        chart.update(
            type="barchart",
            options={
                "xField": "case",
                "orientation": "vertical",
                "groupWidth": 0.7,
                "legend": {"displayMode": "list", "placement": "bottom"},
            },
        )
        chart["targets"] = [
            {
                "refId": ref,
                "expr": selected_expression(
                    f'course_lab_{name}{{{selected},slot="baseline"}}'
                ),
                "instant": True,
                "format": "table",
            }
            for ref, name in (
                ("A", "cpu_duration_seconds"),
                ("B", "resident_duration_seconds"),
                ("C", "transfer_duration_seconds"),
            )
        ]
        chart["transformations"] = [
            {
                "id": "joinByField",
                "options": {"byField": "case", "mode": "outerTabular"},
            },
            {
                "id": "filterFieldsByName",
                "options": {
                    "include": {"names": ["case", "Value #A", "Value #B", "Value #C"]}
                },
            },
        ]
        chart["fieldConfig"]["overrides"] = [
            {
                "matcher": {"id": "byName", "options": "Value #" + ref},
                "properties": [
                    {"id": "displayName", "value": label},
                    {"id": "unit", "value": "s"},
                ],
            }
            for ref, label in (
                ("A", "CPU"),
                ("B", "Resident GPU"),
                ("C", "GPU including transfers"),
            )
        ]
        chart["fieldConfig"]["overrides"].append(
            {
                "matcher": {"id": "byName", "options": "case"},
                "properties": [{"id": "unit", "value": "string"}],
            }
        )
        # Prometheus labels are strings. Sort the element counts numerically,
        # then restore categorical labels for the grouped bar chart.
        chart["transformations"].extend(
            [
                {
                    "id": "convertFieldType",
                    "options": {
                        "conversions": [
                            {"targetField": "case", "destinationType": "number"}
                        ]
                    },
                },
                {
                    "id": "sortBy",
                    "options": {"sort": [{"field": "case", "desc": False}]},
                },
                {
                    "id": "convertFieldType",
                    "options": {
                        "conversions": [
                            {"targetField": "case", "destinationType": "string"}
                        ]
                    },
                },
            ]
        )
        chart["title"] += " — baseline"
        panels.append(chart)
        import copy

        candidate = copy.deepcopy(chart)
        candidate.update(
            id=len(panels) + 1, title="CPU–GPU crossover by element count — candidate"
        )
        candidate["gridPos"]["y"] += 7
        for target in candidate["targets"]:
            target["expr"] = target["expr"].replace(
                'slot="baseline"', 'slot="candidate"'
            )
        panels.append(candidate)
    # Results arrive after timing ends. Evaluate the selected cache at wall time,
    # independently of Grafana's historical end time; telemetry stays historical.
    # MetricsQL's now() is wall time, unlike time()/end() in an instant query.
    for entry in panels:
        for target in entry["targets"]:
            if target.get("instant"):
                target["editorMode"] = "code"
    return {
        "uid": "course-"
        + hashlib.sha256((course + "/" + lab).encode()).hexdigest()[:24],
        "title": recipe["title"],
        "tags": ["gpu-course", course, lab],
        "schemaVersion": 39,
        "version": 3,
        "editable": False,
        "timezone": "utc",
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "30s",
        "description": recipe["question"]
        + " "
        + recipe["interpretation"]
        + (
            " Nsight Systems: " + recipe["systems"]["reason"]
            if "systems" in recipe
            else ""
        ),
        "templating": {
            "list": [
                {
                    "name": "workspace",
                    "type": "textbox",
                    "label": "Fixed learner workspace",
                    "current": {"text": "learner-01", "value": "learner-01"},
                },
                {
                    "name": "profile",
                    "type": "custom",
                    "query": "small,large",
                    "current": {"text": "small", "value": "small"},
                },
                {
                    "name": "node",
                    "type": "query",
                    "label": "Node exporter instance",
                    "datasource": DATASOURCE,
                    "query": "label_values(node_memory_MemAvailable_bytes, instance)",
                    "includeAll": True,
                    "allValue": ".*",
                    "refresh": 1,
                },
                *(
                    [
                        {
                            "name": "gpu_node",
                            "type": "query",
                            "label": "GPU worker",
                            "datasource": DATASOURCE,
                            "query": "label_values(DCGM_FI_DEV_GPU_UTIL, Hostname)",
                            "includeAll": True,
                            "allValue": ".*",
                            "current": {"text": "All", "value": "$__all"},
                            "refresh": 1,
                        },
                        {
                            "name": "gpu",
                            "type": "query",
                            "label": "GPU index on selected workers",
                            "datasource": DATASOURCE,
                            "query": 'label_values(DCGM_FI_DEV_GPU_UTIL{Hostname=~"${gpu_node:regex}"}, gpu)',
                            "includeAll": True,
                            "allValue": ".*",
                            "current": {"text": "All", "value": "$__all"},
                            "refresh": 1,
                        },
                    ]
                    if recipe.get("gpu_telemetry", True)
                    else []
                ),
            ]
        },
        "panels": panels,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for course in COURSES:
        path = ROOT / course / "reference/observability.json"
        recipes = json.loads(path.read_text())
        for lab, recipe in {
            "environment_readiness": recipes["setup"],
            **recipes["labs"],
        }.items():
            target = path.parent / "grafana" / (lab + ".json")
            content = json.dumps(dashboard(course, recipe), indent=2) + "\n"
            if args.check:
                if not target.is_file() or target.read_text() != content:
                    raise SystemExit(f"Stale dashboard: {target}")
            else:
                target.parent.mkdir(exist_ok=True)
                target.write_text(content)


if __name__ == "__main__":
    main()
