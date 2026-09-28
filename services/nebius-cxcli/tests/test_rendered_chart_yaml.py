from __future__ import annotations

import pytest
import yaml

from nebius_cxcli.compatibility_adapters import _rendered_chart_documents


def test_rendered_chart_equals_scalars_preserve_native_types_and_text():
    text = """enum: [=, '=', '!=', '=~']
=: value
enabled: true
count: 3
empty: null
text: |
  - =
defaults: &defaults
  mode: =
settings:
  <<: *defaults
---
"""
    docs = _rendered_chart_documents(text)
    assert docs == [
        {
            "enum": ["=", "=", "!=", "=~"],
            "=": "value",
            "enabled": True,
            "count": 3,
            "empty": None,
            "text": "- =\n",
            "defaults": {"mode": "="},
            "settings": {"mode": "="},
        },
        None,
    ]
    # Importing and using the scoped loader must not mutate PyYAML globally.
    with pytest.raises(yaml.constructor.ConstructorError, match="2002:value"):
        yaml.safe_load("enum: [=]\n")


@pytest.mark.parametrize(
    "text",
    [
        "value: [unterminated",
        "value: !unknown data",
        "value: !!python/object/apply:builtins.str [data]",
        "value: !!value =",
    ],
)
def test_rendered_chart_loader_rejects_malformed_or_unsupported_yaml(text):
    with pytest.raises(yaml.YAMLError):
        _rendered_chart_documents(text)


def workload(kind, container_field="containers", *, duplicate=True):
    variables = [{"name": "POD_IP", "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}}]
    if duplicate:
        variables.append({"name": "POD_IP", "value": "secret-sentinel"})
    pod = {container_field: [{"name": "grafana", "env": variables}]}
    version = (
        "v1"
        if kind in {"Pod", "ReplicationController"}
        else "batch/v1"
        if kind in {"Job", "CronJob"}
        else "apps/v1"
    )
    spec = pod if kind == "Pod" else {"template": {"spec": pod}}
    if kind == "CronJob":
        spec = {"jobTemplate": {"spec": spec}}
    return {"apiVersion": version, "kind": kind, "metadata": {"name": "example"}, "spec": spec}


@pytest.mark.parametrize(
    "kind",
    [
        "Pod",
        "Deployment",
        "StatefulSet",
        "DaemonSet",
        "ReplicaSet",
        "ReplicationController",
        "Job",
        "CronJob",
    ],
)
@pytest.mark.parametrize("container_field", ["containers", "initContainers", "ephemeralContainers"])
def test_render_admission_rejects_duplicate_environment_without_exposing_values(
    kind, container_field
):
    with pytest.raises(ValueError, match="duplicate env name POD_IP") as error:
        _rendered_chart_documents(yaml.safe_dump(workload(kind, container_field)))
    assert "secret-sentinel" not in str(error.value)


def test_environment_names_are_local_to_each_container_and_list_items_are_checked():
    good = workload("Deployment", duplicate=False)
    pod = good["spec"]["template"]["spec"]
    pod["initContainers"] = [{"name": "init", "env": [{"name": "POD_IP", "value": "one"}]}]
    pod["containers"].append({"name": "sidecar", "env": [{"name": "POD_IP", "value": "two"}]})
    assert _rendered_chart_documents(yaml.safe_dump(good)) == [good]
    with pytest.raises(ValueError, match="duplicate env"):
        _rendered_chart_documents(
            yaml.safe_dump({"apiVersion": "v1", "kind": "List", "items": [good, workload("Job")]})
        )


def test_custom_resource_data_is_not_mistaken_for_a_native_pod_template():
    document = workload("Deployment")
    document["apiVersion"] = "custom.example.invalid/v1"
    assert _rendered_chart_documents(yaml.safe_dump(document)) == [document]


def test_optional_null_environment_and_container_lists_remain_valid():
    document = workload("Deployment", duplicate=False)
    pod = document["spec"]["template"]["spec"]
    pod["containers"][0]["env"] = None
    pod["initContainers"] = None
    pod["ephemeralContainers"] = None
    assert _rendered_chart_documents(yaml.safe_dump(document)) == [document]


@pytest.mark.parametrize("field", ["containers", "initContainers", "ephemeralContainers"])
@pytest.mark.parametrize(
    "reference",
    [
        "private.invalid/image@sha256:sha256:" + "a" * 64,
        "private.invalid/image@sha256:" + "a" * 63,
        "private.invalid/image@sha256:" + "z" * 64,
        "private.invalid/image@sha256:" + "a" * 64 + " ",
    ],
)
def test_native_image_digest_admission_rejects_invalid_values_without_echo(field, reference):
    document = workload("Pod", field, duplicate=False)
    document["spec"][field][0]["image"] = reference
    with pytest.raises(ValueError, match="malformed SHA-256 image digest") as error:
        _rendered_chart_documents(yaml.safe_dump(document))
    assert "private.invalid" not in str(error.value)


@pytest.mark.parametrize(
    "reference",
    [
        "grafana/grafana:13.2.2@sha256:" + "a" * 64,
        "registry.example:5000/path/image@sha256:" + "a" * 64,
        "localhost/image@sha256:" + "a" * 64,
        "busybox:latest",
        "image",
        "registry.example/image@sha512:" + "a" * 128,
    ],
)
def test_existing_tagged_and_digest_images_remain_accepted(reference):
    document = workload("Deployment", duplicate=False)
    document["spec"]["template"]["spec"]["containers"][0]["image"] = reference
    assert _rendered_chart_documents(yaml.safe_dump(document)) == [document]


def test_custom_resource_image_data_is_not_a_native_container_contract():
    document = workload("Deployment", duplicate=False)
    document["apiVersion"] = "custom.example.invalid/v1"
    document["spec"]["template"]["spec"]["containers"][0]["image"] = "image@sha256:template"
    assert _rendered_chart_documents(yaml.safe_dump(document)) == [document]
