"""One fenced recovery of Helm SSA's empty-map failure on an existing VMAgent.

The failed Helm revision supplies desired content; its successful rollback is
our preimage. No release input is rewritten and only fingerprints are persisted.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Mapping
from typing import Any

import yaml

from .soperator_child_publication import _binding
from .soperator_failures import SoperatorSafetyPauseError
from .soperator_graph_transition import (
    HR,
    NativeGraphTransition,
    canonical_spec,
    identity,
    owned_by,
    ownership_digest,
    spec_digest,
    witness,
)
from .soperator_operation import soperator_sha256
from .soperator_release_source import default_soperator_source_cache_root

AGENT = "vmagents.operator.victoriametrics.com"
REQUEST = "reconcile.fluxcd.io/requestedAt"
RESET = "reconcile.fluxcd.io/resetAt"
FORCE = "reconcile.fluxcd.io/forceAt"
STATE = "vmagentMaterialization"


def require(ok: object, detail: str) -> None:
    if not ok:
        raise SoperatorSafetyPauseError("Native metrics recovery " + detail)


def condition(child: Mapping[str, Any], kind: str, reason: str | None = None) -> bool:
    return any(
        c.get("type") == kind
        and c.get("status") == "True"
        and (reason is None or c.get("reason") == reason)
        for c in child.get("status", {}).get("conditions", [])
    )


def helm_get(operation, child, verb, revision, *, json_output=True):
    spec = child["spec"]
    args = [
        "helm",
        "get",
        verb,
        spec.get("releaseName") or identity(child)[1],
        "-n",
        spec.get("storageNamespace") or identity(child)[0],
        "--revision",
        str(revision),
    ]
    if json_output:
        args += ["-o", "json"]
    result = operation.run(args).stdout
    return (
        json.loads(result)
        if json_output
        else [r for r in yaml.safe_load_all(result) if isinstance(r, Mapping)]
    )


def effective_values(operation, child):
    """Flux's map merge for the admitted, non-targetPath ConfigMap references."""

    def merge(before, after):
        result = copy.deepcopy(before)
        for key, value in after.items():
            result[key] = (
                merge(result[key], value)
                if isinstance(result.get(key), dict) and isinstance(value, dict)
                else copy.deepcopy(value)
            )
        return result

    values, sources = {}, []
    for ref in child["spec"].get("valuesFrom", []):
        require(
            ref.get("kind") == "ConfigMap" and not ref.get("targetPath"),
            "has an unsupported values reference",
        )
        obj = operation.get("configmap", identity(child)[0], ref["name"])
        key = ref.get("valuesKey") or "values.yaml"
        if not obj or key not in obj.get("data", {}):
            require(ref.get("optional") is True, "lost a required values reference")
            sources.append({"referenceSha256": soperator_sha256(ref), "absent": True})
            continue
        value = yaml.safe_load(obj["data"][key]) or {}
        require(isinstance(value, dict), "has non-map referenced values")
        sources.append(
            {
                "referenceSha256": soperator_sha256(ref),
                "uid": obj["metadata"]["uid"],
                "dataSha256": soperator_sha256(obj["data"][key]),
            }
        )
        values = merge(values, value)
    return merge(values, child["spec"].get("values", {})), sources


def failed_frontier(child):
    status, spec = child.get("status", {}), child["spec"]
    history = status.get("history", [])
    require(
        condition(child, "Stalled", "RetriesExceeded")
        and not condition(child, "Reconciling")
        and condition(child, "Remediated", "RollbackSucceeded")
        and status.get("lastAttemptedReleaseAction") == "upgrade"
        and history
        and history[0].get("action") == "rollback"
        and history[0].get("status") == "deployed",
        "has no exhausted, completed rollback frontier",
    )
    rollback = history[0]
    failed = [
        r
        for r in history
        if r.get("version") == rollback["version"] - 1
        and r.get("action") == "upgrade"
        and r.get("status") == "failed"
    ]
    require(
        len(failed) == 1 and all(r.get("version", 0) <= rollback["version"] for r in history),
        "has ambiguous failed revision history",
    )
    failed = failed[0]
    require(
        failed.get("configDigest") == status.get("lastAttemptedConfigDigest")
        and failed.get("configDigest")
        and failed.get("chartVersion") == status.get("lastAttemptedRevision")
        and all(
            failed.get(k) == rollback.get(k)
            for k in ("name", "namespace", "chartName", "chartVersion")
        )
        and failed.get("name") == (spec.get("releaseName") or identity(child)[1])
        and failed.get("namespace") == (spec.get("targetNamespace") or identity(child)[0]),
        "lost its failed revision association",
    )
    return failed, rollback


def verify_desired_agent(operation, child, row, values, desired):
    """Derive mutation authority from the already verified frozen chart package."""
    from .soperator_release_artifacts import _sha256_file

    package = (
        default_soperator_source_cache_root()
        / "charts"
        / (row["revision"].removeprefix("sha256:") + ".tgz")
    )
    require(
        package.is_file() and not package.is_symlink() and _sha256_file(package) == row["revision"],
        "cannot authenticate its cached chart package",
    )
    require(not child["spec"].get("postRenderers"), "has unqualified child post-renderers")
    spec = child["spec"]
    rendered = operation.run(
        [
            "helm",
            "template",
            spec.get("releaseName") or identity(child)[1],
            str(package),
            "--namespace",
            spec.get("targetNamespace") or identity(child)[0],
            "--values",
            "/dev/stdin",
        ],
        input_text=json.dumps(values),
    ).stdout
    agents = [
        r
        for r in yaml.safe_load_all(rendered)
        if isinstance(r, dict) and r.get("kind") == "VMAgent"
    ]
    require(
        len(agents) == 1
        and identity(agents[0]) == identity(desired)
        and agents[0].get("apiVersion") == desired.get("apiVersion")
        and soperator_sha256(agents[0].get("spec")) == soperator_sha256(desired.get("spec")),
        "failed VMAgent manifest differs from the frozen chart render",
    )


def agent_patch(operation, agent, desired_spec, *, dry_run=False):
    metadata = agent["metadata"]
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        {"op": "test", "path": "/spec", "value": agent["spec"]},
        {
            "op": "replace",
            "path": "/spec" if dry_run else "/spec/remoteWriteSettings",
            "value": desired_spec if dry_run else {},
        },
    ]
    args = [
        "kubectl",
        "-n",
        metadata["namespace"],
        "patch",
        AGENT,
        metadata["name"],
        "--type=json",
        "--field-manager=cxcli-native-empty-map",
        "--patch-file",
        "/dev/stdin",
        "-o",
        "json",
    ]
    if dry_run:
        args += ["--dry-run=server"]
    operation.authority()
    return json.loads(operation.run(args, input_text=json.dumps(patch)).stdout)


def control_digest(child):
    annotations = child["metadata"].get("annotations", {})
    return soperator_sha256({k: annotations.get(k) for k in (REQUEST, RESET, FORCE)})


def publication(operation, child):
    parent = operation._published_parent()
    manifest = operation.parent_manifest(parent)
    require(owned_by(child, parent), "lost child ownership")
    matches = [
        r for r in manifest if r.get("kind") == "HelmRelease" and identity(r) == identity(child)
    ]
    require(len(matches) == 1, "lost its published child")
    desired = canonical_spec(operation.run, child, matches[0]["spec"])
    require(
        spec_digest({"spec": desired}) == spec_digest(child), "differs from its published child"
    )
    return _binding(operation, parent, child, manifest)


def qualify(operation, child, row):
    binding = publication(operation, child)
    failed, rollback = failed_frontier(child)
    ref = child["spec"].get("chartRef", {})
    require(
        ref.get("kind") == row["sourceKind"] == "HelmChart"
        and ref.get("name") == row["sourceName"],
        "lost its frozen chart reference",
    )
    source = operation.get("helmchart", ref.get("namespace") or identity(child)[0], ref["name"])
    require(
        source
        and not source["metadata"].get("deletionTimestamp")
        and source.get("status", {}).get("observedGeneration")
        == source["metadata"].get("generation")
        and condition(source, "Ready")
        and source["status"].get("artifact", {}).get("digest") == row["revision"]
        and source["spec"].get("chart") == failed["chartName"]
        and source["spec"].get("version") == failed["chartVersion"],
        "lost its frozen chart artifact",
    )
    manifests = []
    for release in (failed, rollback):
        metadata = helm_get(operation, child, "metadata", release["version"])
        require(
            metadata.get("applyMethod") == "ssa"
            and metadata.get("revision") == release["version"]
            and metadata.get("status") == release["status"]
            and metadata.get("name") == release["name"]
            and metadata.get("namespace") == release["namespace"],
            "lost its exact SSA Helm revision",
        )
        manifests.append(
            helm_get(operation, child, "manifest", release["version"], json_output=False)
        )
    values, sources = effective_values(operation, child)
    require(
        soperator_sha256(helm_get(operation, child, "values", failed["version"]))
        == soperator_sha256(values),
        "failed revision has different effective values",
    )
    desired, previous = [
        [
            r
            for r in docs
            if r.get("kind") == "VMAgent"
            and r.get("apiVersion") == "operator.victoriametrics.com/v1beta1"
        ]
        for docs in manifests
    ]
    require(
        len(desired) == len(previous) == 1 and identity(desired[0]) == identity(previous[0]),
        "has ambiguous VMAgent manifests",
    )
    desired, previous = desired[0], previous[0]
    verify_desired_agent(operation, child, row, values, desired)
    require(
        desired["spec"].get("remoteWriteSettings") == {}
        and isinstance(previous["spec"].get("remoteWriteSettings"), dict)
        and previous["spec"]["remoteWriteSettings"],
        "has no explicit nonempty-to-empty VMAgent map transition",
    )
    namespace, name = identity(previous)
    agent = operation.get(AGENT, namespace, name)
    meta = agent.get("metadata", {})
    annotations, labels = meta.get("annotations", {}), meta.get("labels", {})
    require(
        agent
        and not meta.get("deletionTimestamp")
        and meta.get("uid")
        and annotations.get("meta.helm.sh/release-name") == failed["name"]
        and annotations.get("meta.helm.sh/release-namespace") == failed["namespace"]
        and not annotations.get("helm.sh/resource-policy")
        and not annotations.get("helm.sh/hook")
        and labels.get("app.kubernetes.io/managed-by") == "Helm"
        and labels.get("helm.toolkit.fluxcd.io/name") == identity(child)[1]
        and labels.get("helm.toolkit.fluxcd.io/namespace") == identity(child)[0],
        "lost VMAgent ownership",
    )
    before = agent_patch(operation, agent, previous["spec"], dry_run=True)["spec"]
    after = copy.deepcopy(before)
    after["remoteWriteSettings"] = {}
    require(
        agent_patch(operation, agent, after, dry_run=True)["spec"] == after,
        "cannot materialize the empty map",
    )
    proof = {
        "publication": binding,
        "child": witness(child),
        "row": dict(row),
        "failedRevision": failed["version"],
        "rollbackRevision": rollback["version"],
        "failedHistorySha256": soperator_sha256(failed),
        "rollbackHistorySha256": soperator_sha256(rollback),
        "desiredManifestSha256": soperator_sha256(manifests[0]),
        "rollbackManifestSha256": soperator_sha256(manifests[1]),
        "sourceUid": source["metadata"]["uid"],
        "sourceSpecSha256": spec_digest(source),
        "valuesSources": sources,
        "effectiveValuesSha256": soperator_sha256(values),
        "agent": {
            "namespace": namespace,
            "name": name,
            "uid": meta["uid"],
            "ownershipSha256": ownership_digest(agent),
            "beforeSha256": soperator_sha256(before),
            "afterSha256": soperator_sha256(after),
            "targetSha256": soperator_sha256(
                agent_patch(operation, agent, desired["spec"], dry_run=True)["spec"]
            ),
        },
    }
    # Suspension is an execution fence, not part of revision identity.
    proof["child"].pop("suspend")
    return proof, agent


def save(operation, record, phase):
    record["phase"] = phase
    operation.state[STATE] = record
    operation.save(operation.state["phase"])


def verify_retry_inputs(operation, child, proof):
    require(
        child["metadata"].get("uid") == proof["child"]["uid"]
        and ownership_digest(child) == proof["child"]["ownershipSha256"]
        and spec_digest(child) == proof["child"]["specSha256"],
        "child changed after retry intent",
    )
    ref = child["spec"]["chartRef"]
    source = operation.get("helmchart", ref.get("namespace") or identity(child)[0], ref["name"])
    require(
        source.get("metadata", {}).get("uid") == proof["sourceUid"]
        and not source["metadata"].get("deletionTimestamp")
        and spec_digest(source) == proof["sourceSpecSha256"]
        and source.get("status", {}).get("artifact", {}).get("digest") == proof["row"]["revision"],
        "chart source changed after retry intent",
    )
    values, sources = effective_values(operation, child)
    require(
        soperator_sha256(values) == proof["effectiveValuesSha256"]
        and sources == proof["valuesSources"],
        "effective values changed after retry intent",
    )


def verify_agent(operation, proof, hashes):
    expected = proof["agent"]
    current = operation.get(AGENT, expected["namespace"], expected["name"])
    require(
        current.get("metadata", {}).get("uid") == expected["uid"]
        and not current["metadata"].get("deletionTimestamp")
        and ownership_digest(current) == expected["ownershipSha256"]
        and soperator_sha256(current.get("spec")) in hashes,
        "VMAgent changed after materialization",
    )
    return current


def verify_fences(operation, child, proof):
    current = operation.get(HR, *identity(child))
    require(
        current["spec"].get("suspend") is True
        and not condition(current, "Reconciling")
        and publication(operation, current) == proof["publication"],
        "lost its fences before materialization",
    )
    verify_retry_inputs(operation, current, proof)
    return current


def advance(operation, child, row):
    record = operation.state.get(STATE)
    if record is not None and record["phase"] in {"reset-requested", "converged"}:
        require(
            publication(operation, child) == record["proof"]["publication"]
            and child["metadata"]["uid"] == record["proof"]["child"]["uid"],
            "changed after reset intent",
        )
        token = record["token"]
        annotations = child["metadata"].get("annotations", {})
        requested = annotations.get(RESET) == annotations.get(REQUEST) == token
        if requested:
            verify_retry_inputs(operation, child, record["proof"])
            require(annotations.get(FORCE) is None, "acquired an unrelated forced retry")
            if child["spec"].get("suspend") is True:
                handled = child.get("status", {}).get("lastHandledResetAt") == token
                require(
                    not handled or not condition(child, "Stalled"), "recorded retry already failed"
                )
                hashes = {record["proof"]["agent"]["afterSha256"]}
                if handled:
                    hashes.add(record["proof"]["agent"]["targetSha256"])
                verify_agent(operation, record["proof"], hashes)
                operation._cas(child, {**child["spec"], "suspend": False})
                return True
            if child.get("status", {}).get("lastHandledResetAt") != token:
                return True
            if condition(child, "Stalled"):
                raise SoperatorSafetyPauseError(
                    "Native metrics materialization retry failed; no second reset is authorized"
                )
            if condition(child, "Ready") and child["status"].get("observedGeneration") == child[
                "metadata"
            ].get("generation"):
                verify_agent(operation, record["proof"], {record["proof"]["agent"]["targetSha256"]})
                save(operation, record, "converged")
                return False
            return True
        require(
            record["phase"] == "reset-requested"
            and control_digest(child) == record["controlsSha256"]
            and child["spec"].get("suspend") is True,
            "lost its reset intent",
        )
    if record is None:
        require(
            child.get("status", {}).get("observedGeneration")
            == child["metadata"].get("generation"),
            "has an unobserved child generation",
        )
        require(
            any(
                c.get("type") == "Released"
                and c.get("status") == "False"
                and c.get("reason") == "UpgradeFailed"
                and all(
                    t in c.get("message", "") for t in ("VMAgent", "remoteWriteSettings", '"null"')
                )
                for c in child.get("status", {}).get("conditions", [])
            ),
            "has a different upgrade failure",
        )
        proof, agent = qualify(operation, child, row)
        require(
            soperator_sha256(agent["spec"]) == proof["agent"]["beforeSha256"],
            "VMAgent differs from rollback preimage",
        )
        require(
            child["metadata"].get("annotations", {}).get(FORCE) is None,
            "has an unrelated forced retry",
        )
        record = {
            "proof": proof,
            "controlsSha256": control_digest(child),
            "token": "cxcli-map-" + soperator_sha256(proof).split(":")[-1],
        }
        save(operation, record, "intent-recorded")
    if child["spec"].get("suspend") is not True:
        require(control_digest(child) == record["controlsSha256"], "retry controls changed")
        operation._cas(child, {**child["spec"], "suspend": True})
        return True
    if child.get("status", {}).get("observedGeneration") != child["metadata"].get("generation"):
        return True
    proof, agent = qualify(operation, child, row)
    require(
        proof == record["proof"] and control_digest(child) == record["controlsSha256"],
        "changed after materialization intent",
    )
    expected = proof["agent"]
    digest = soperator_sha256(agent["spec"])
    allowed = {expected["afterSha256"]}
    if record["phase"] == "intent-recorded":
        allowed.add(expected["beforeSha256"])
    require(digest in allowed, "VMAgent postimage changed")
    if digest == expected["beforeSha256"]:
        # The parent and child are both fenced; the CAS checks the complete
        # live preimage but mutates only this one field.
        verify_fences(operation, child, proof)
        agent = verify_agent(operation, proof, {expected["beforeSha256"]})
        agent = agent_patch(operation, agent, {})
    require(
        soperator_sha256(agent["spec"]) == expected["afterSha256"],
        "VMAgent materialization did not converge",
    )
    save(operation, record, "materialized")
    child = verify_fences(operation, child, proof)
    verify_agent(operation, proof, {expected["afterSha256"]})
    require(
        child["spec"].get("suspend") is True
        and publication(operation, child) == proof["publication"]
        and control_digest(child) == record["controlsSha256"]
        and not condition(child, "Reconciling"),
        "lost its fence before retry",
    )
    failed, rollback = failed_frontier(child)
    require(
        soperator_sha256(failed) == proof["failedHistorySha256"]
        and soperator_sha256(rollback) == proof["rollbackHistorySha256"],
        "revision advanced before retry",
    )
    save(operation, record, "reset-requested")
    metadata = child["metadata"]
    annotations = {
        **metadata.get("annotations", {}),
        REQUEST: record["token"],
        RESET: record["token"],
    }
    patch = [
        {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        {"op": "test", "path": "/spec", "value": child["spec"]},
        {"op": "add", "path": "/metadata/annotations", "value": annotations},
        {"op": "replace", "path": "/spec/suspend", "value": False},
    ]
    operation.authority()
    operation.run(
        [
            "kubectl",
            "-n",
            identity(child)[0],
            "patch",
            HR,
            identity(child)[1],
            "--type=json",
            "--patch-file",
            "/dev/stdin",
        ],
        input_text=json.dumps(patch),
    )
    return True


def recover_stage(
    operation: NativeGraphTransition, contract: Mapping[str, Any], child: Mapping[str, Any]
) -> bool:
    """Return True only while this exact owned repair is still progressing."""
    if operation.state.get(STATE, {}).get("phase") == "converged":
        return False
    matches = [
        r
        for r in contract.get("releases", [])
        if (r["namespace"], r["releaseName"]) == identity(child)
        and r.get("upstreamReleaseName") == "soperator-fluxcd-vm-stack"
    ]
    if not matches or not (
        condition(child, "Stalled", "RetriesExceeded") or operation.state.get(STATE)
    ):
        return False
    require(
        len(matches) == 1
        and soperator_sha256(contract) == operation.admission["desiredGraphSha256"],
        "lost its frozen graph",
    )
    return advance(operation, child, matches[0])


def recover_pending(operation: NativeGraphTransition) -> None:
    """Complete an interrupted repair before publishing another parent revision."""
    record = operation.state.get(STATE)
    if record is None or record["phase"] == "converged":
        return
    deadline = time.monotonic() + 1800
    while True:
        child = operation.get(
            HR, record["proof"]["child"]["namespace"], record["proof"]["child"]["name"]
        )
        if not advance(operation, child, record["proof"]["row"]):
            return
        require(time.monotonic() < deadline, "timed out completing its recorded retry")
        time.sleep(2)
