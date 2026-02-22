from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.paths import resolve_instance_paths, validate_path_alignment
from nebius_cxcli.render import render_instance


def _stack_variable_names() -> set[str]:
    repo_root = Path(__file__).resolve().parents[3]
    variables_tf = repo_root / "platform-infra" / "stacks" / "customer-platform" / "variables.tf"
    text = variables_tf.read_text(encoding="utf-8")
    return set(re.findall(r'^variable "([a-z0-9_]+)"', text, flags=re.MULTILINE))


def _deep_merge(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
            continue
        result[key] = value
    return result


def _rendered_tfvars(tmp_path: Path, payload_override: dict | None = None) -> dict[str, object]:
    cluster_dir = (
        tmp_path / "deployments" / "instances" / "client-a--tenant-123" / "prod" / "client-a-prod"
    )
    cluster_dir.mkdir(parents=True, exist_ok=True)
    source = starter_config_yaml(
        client_name="client-a",
        tenant_id="tenant-123",
        env="prod",
        cluster_name="client-a-prod",
        project_id="project-456",
        region_id="eu-north1",
        subnet_id="subnet-abc123",
        email="ops@example.com",
    )
    payload = yaml.safe_load(source)
    if payload_override:
        payload = _deep_merge(payload, payload_override)

    config_path = cluster_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = load_config(config_path)
    paths = resolve_instance_paths(config_path)
    validate_path_alignment(config, paths)
    render_instance(config, paths)
    return json.loads((paths.infra_dir / "terraform.auto.tfvars.json").read_text(encoding="utf-8"))


def test_rendered_tfvars_keys_are_declared_in_stack_variables(tmp_path: Path) -> None:
    tfvars = _rendered_tfvars(tmp_path)
    stack_vars = _stack_variable_names()
    missing = sorted(set(tfvars.keys()) - stack_vars)
    assert missing == []


def test_optional_rendered_tfvars_keys_are_declared_in_stack_variables(tmp_path: Path) -> None:
    payload_override = {
        "infra": {
            "mk8s": {
                "cluster_overrides": {"control_plane": {"version": "1.31", "etcd_cluster_size": 3}},
                "gpu_nodes": {
                    "enabled": True,
                    "node_groups": 1,
                    "nodes_per_group": 1,
                    "platform": "gpu-h200-sxm",
                    "preset": "8gpu-128vcpu-1600gb",
                    "preemptible": False,
                    "public_ips": False,
                    "driverfull_image": True,
                    "mig": {"enabled": True, "strategy": "single", "parted_config": "all-1g.10gb"},
                },
            }
        }
    }
    tfvars = _rendered_tfvars(tmp_path, payload_override=payload_override)
    stack_vars = _stack_variable_names()
    missing = sorted(set(tfvars.keys()) - stack_vars)
    assert missing == []
