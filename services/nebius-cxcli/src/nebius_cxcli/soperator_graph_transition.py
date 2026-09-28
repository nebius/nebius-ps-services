"""Exact native graph retirement; the umbrella and Helm own deletion.

Only fingerprints and resource identities are journaled. A graph label or a
historical accepted render is never sufficient deletion authority.
"""

from __future__ import annotations

import base64
import copy
import json
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from .soperator_failures import SoperatorSafetyPauseError
from .soperator_flux_graph import SOPERATOR_GRAPH_LABEL, SOPERATOR_GRAPH_LABEL_VALUE
from .soperator_observability_scope import OWNED_WRITER, WRITER, normalized_graph
from .soperator_operation import soperator_sha256

SCHEMA = "nebius-cxcli.native-graph-transition.v1"
REPAIR_REASON = "native-token-writer-retirement-v1"
HR = "helmreleases.helm.toolkit.fluxcd.io"
_ALLOWED_RESOURCES = {"ServiceAccount", "Role", "RoleBinding", "Deployment"}


def identity(resource: Mapping[str, Any]) -> tuple[str, str]:
    metadata = resource.get("metadata", {})
    return str(metadata.get("namespace") or "flux-system"), str(metadata.get("name") or "")


def spec_digest(resource: Mapping[str, Any]) -> str:
    return soperator_sha256({k: v for k, v in resource.get("spec", {}).items() if k != "suspend"})


def patch_spec(
    run: Callable[..., Any],
    resource: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    dry_run: bool = False,
    authority: Callable[[], object] | None = None,
) -> Any:
    for attempt in range(3):
        metadata = resource["metadata"]
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": metadata["resourceVersion"],
            },
            {"op": "test", "path": "/spec", "value": resource["spec"]},
            {"op": "replace", "path": "/spec", "value": dict(spec)},
        ]
        args = [
            "kubectl",
            "-n",
            metadata["namespace"],
            "patch",
            HR,
            metadata["name"],
            "--type=json",
        ]
        if dry_run:
            args += ["--dry-run=server", "-o", "json"]
        try:
            if authority is not None:
                authority()
            return run([*args, "--patch-file", "/dev/stdin"], input_text=json.dumps(patch))
        except Exception as exc:
            detail = str(getattr(exc, "stderr", "") or exc).lower()
            if attempt == 2 or not any(
                marker in detail
                for marker in ("request is invalid", "test failed", "test operation", "conflict")
            ):
                raise
            observed = json.loads(
                run(
                    [
                        "kubectl",
                        "-n",
                        metadata["namespace"],
                        "get",
                        HR,
                        metadata["name"],
                        "-o",
                        "json",
                    ]
                ).stdout
            )
            # Retry only a proved status/metadata RV race. Ambiguous successful
            # mutation or changed identity/specification returns to recovery.
            if (
                observed.get("metadata", {}).get("uid") != metadata["uid"]
                or observed.get("metadata", {}).get("deletionTimestamp")
                or observed.get("spec") != resource["spec"]
                or ownership_digest(observed) != ownership_digest(resource)
                or observed["metadata"].get("resourceVersion") == metadata["resourceVersion"]
            ):
                raise
            resource = observed
    raise AssertionError("unreachable native patch retry")


def canonical_spec(
    run: Callable[..., Any], resource: Mapping[str, Any], spec: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Obtain API defaults without changing the resource or weakening equality."""
    result = json.loads(patch_spec(run, resource, spec, dry_run=True).stdout)
    if result.get("metadata", {}).get("uid") != resource["metadata"]["uid"]:
        raise SoperatorSafetyPauseError("Native graph dry-run changed resource identity")
    return result["spec"]


def active_transition_checkpoint(journal: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Completed scheduling history cannot authorize a new graph transition."""
    if journal is None or journal.get("status") == "restored":
        return None
    payload = journal.get("nativeGraphTransition")
    if payload is not None and not isinstance(payload, Mapping):
        raise SoperatorSafetyPauseError("Active native graph checkpoint is invalid")
    return payload


def campaign_transition_checkpoint(evidence: Mapping[str, Any]) -> Mapping[str, Any] | None:
    witnesses = [
        event.get("transition")
        for event in evidence.get("events", [])
        if event.get("action") == "native-graph-admitted"
    ]
    if len(witnesses) > 1 or (witnesses and not isinstance(witnesses[0], Mapping)):
        raise SoperatorSafetyPauseError("Campaign native graph has conflicting admission witnesses")
    payload = evidence.get("nativeGraphTransition")
    if payload is None:
        payload = witnesses[0] if witnesses else None
    if payload is not None and (
        not isinstance(payload, Mapping)
        or not witnesses
        or payload.get("admission") != witnesses[0].get("admission")
        or payload.get("admissionSha256") != witnesses[0].get("admissionSha256")
    ):
        raise SoperatorSafetyPauseError("Native graph campaign checkpoint changed its admission")
    return payload


def witness(resource: Mapping[str, Any]) -> dict[str, Any]:
    metadata = resource.get("metadata", {})
    if not metadata.get("uid") or not metadata.get("resourceVersion"):
        raise SoperatorSafetyPauseError(
            "Native transition requires an exact live resource identity"
        )
    namespace, name = identity(resource)
    return {
        "namespace": namespace,
        "name": name,
        "uid": metadata["uid"],
        "specSha256": spec_digest(resource),
        "ownershipSha256": ownership_digest(resource),
        "suspend": resource.get("spec", {}).get("suspend", False),
    }


def assert_witness(resource: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if (
        identity(resource) != (expected["namespace"], expected["name"])
        or resource.get("metadata", {}).get("uid") != expected["uid"]
        or spec_digest(resource) != expected["specSha256"]
        or ownership_digest(resource) != expected["ownershipSha256"]
    ):
        raise SoperatorSafetyPauseError("Native graph retirement lost its frozen resource identity")


def ownership_digest(resource: Mapping[str, Any]) -> str:
    metadata = resource.get("metadata", {})
    return soperator_sha256(
        {
            "labels": {
                key: metadata.get("labels", {}).get(key)
                for key in (
                    SOPERATOR_GRAPH_LABEL,
                    "helm.toolkit.fluxcd.io/name",
                    "helm.toolkit.fluxcd.io/namespace",
                )
            },
            "annotations": {
                key: metadata.get("annotations", {}).get(key)
                for key in (
                    "meta.helm.sh/release-name",
                    "meta.helm.sh/release-namespace",
                    "helm.sh/resource-policy",
                )
            },
            "owners": metadata.get("ownerReferences", []),
        }
    )


def owned_by(resource: Mapping[str, Any], parent: Mapping[str, Any]) -> bool:
    namespace, name = identity(parent)
    metadata = resource.get("metadata", {})
    labels = metadata.get("labels", {})
    annotations = metadata.get("annotations", {})
    return (
        labels.get(SOPERATOR_GRAPH_LABEL) == SOPERATOR_GRAPH_LABEL_VALUE
        and labels.get("helm.toolkit.fluxcd.io/name") == name
        and labels.get("helm.toolkit.fluxcd.io/namespace") == namespace
        and annotations.get("meta.helm.sh/release-name") == name
        and annotations.get("meta.helm.sh/release-namespace") == namespace
    )


def contains_declared(live: Any, declared: Any) -> bool:
    """Compare declared fields while allowing Kubernetes API defaults."""
    if isinstance(declared, Mapping):
        return isinstance(live, Mapping) and all(
            key in live and contains_declared(live[key], value) for key, value in declared.items()
        )
    if isinstance(declared, list):
        return (
            isinstance(live, list)
            and len(live) == len(declared)
            and all(
                contains_declared(actual, expected)
                for actual, expected in zip(live, declared, strict=True)
            )
        )
    return live == declared


def classify_graph(
    resources: Sequence[Mapping[str, Any]],
    desired: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> dict[str, list[tuple[str, str]]]:
    """Reject unknown removals before any diagnostic or scheduling maintenance."""
    rows = desired.get("releases", [])
    wanted = {(str(row["namespace"]), str(row["releaseName"])) for row in rows}
    names = {name for _, name in wanted}
    if any(not set(row.get("dependencies", [])) <= names for row in rows):
        raise SoperatorSafetyPauseError("Native desired graph has an unresolved dependency")
    observed = set()
    for resource in resources:
        key = identity(resource)
        if key == identity(parent):
            continue
        metadata = resource.get("metadata", {})
        labels = metadata.get("labels", {})
        annotations = metadata.get("annotations", {})
        selected = (
            key in wanted
            or labels.get(SOPERATOR_GRAPH_LABEL) == SOPERATOR_GRAPH_LABEL_VALUE
            or (
                annotations.get("meta.helm.sh/release-name") == identity(parent)[1]
                and annotations.get("meta.helm.sh/release-namespace") == identity(parent)[0]
            )
        )
        if not selected:
            continue
        if key in observed or not owned_by(resource, parent):
            raise SoperatorSafetyPauseError(
                "Native graph has ambiguous or foreign release ownership"
            )
        observed.add(key)
    retiring = observed - wanted
    if retiring - {("flux-system", OWNED_WRITER)}:
        raise SoperatorSafetyPauseError(
            "Native graph removes an unqualified release; admission stopped before maintenance"
        )
    return {
        "retained": sorted(observed & wanted),
        "added": sorted(wanted - observed),
        "retiring": sorted(retiring),
    }


def preflight_transition(
    snapshot: Any,
    *,
    run: Callable[..., Any],
    desired_graph: Mapping[str, Any] | None = None,
) -> bool:
    """Read-only membership admission shared with parent campaign maintenance."""
    from .soperator_flux_graph import target_soperator_release_name

    graph = (
        desired_graph
        if desired_graph is not None
        else {
            "releases": [
                {
                    "namespace": node.namespace,
                    "releaseName": target_soperator_release_name(node.release_name),
                    "dependencies": [
                        target_soperator_release_name(name) for name in node.dependencies
                    ],
                }
                for node in snapshot.release_graph
            ]
        }
    )
    resources = json.loads(run(["kubectl", "get", HR, "-A", "-o", "json"]).stdout)["items"]
    children = [
        r
        for r in resources
        if r.get("metadata", {}).get("labels", {}).get(SOPERATOR_GRAPH_LABEL)
        == SOPERATOR_GRAPH_LABEL_VALUE
    ]
    if not children:
        return False
    parents = {
        (
            r.get("metadata", {}).get("annotations", {}).get("meta.helm.sh/release-namespace"),
            r.get("metadata", {}).get("annotations", {}).get("meta.helm.sh/release-name"),
        )
        for r in children
    }
    if len(parents) != 1 or any(not value for pair in parents for value in pair):
        raise SoperatorSafetyPauseError(
            "Native graph has ambiguous parent ownership before maintenance"
        )
    parent = [r for r in resources if identity(r) in parents]
    if len(parent) != 1:
        raise SoperatorSafetyPauseError("Native graph parent is unavailable before maintenance")
    return bool(classify_graph(resources, graph, parent[0])["retiring"])


def qualify_inventory(documents: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """No credentials, persistent objects, hooks or retained resources may be uninstalled."""
    result = []
    for document in documents:
        metadata = document.get("metadata", {})
        annotations = metadata.get("annotations", {})
        if (
            document.get("kind") not in _ALLOWED_RESOURCES
            or "helm.sh/hook" in annotations
            or "helm.sh/resource-policy" in annotations
            or not metadata.get("name")
            or not metadata.get("namespace")
            or metadata.get("name") != WRITER
        ):
            raise SoperatorSafetyPauseError("Token-writer uninstall inventory is not qualified")
        row = {
            "kind": document["kind"],
            "namespace": metadata["namespace"],
            "name": metadata["name"],
        }
        if document["kind"] == "Deployment":
            selector = document.get("spec", {}).get("selector", {})
            labels = selector.get("matchLabels")
            if not labels or selector.get("matchExpressions"):
                raise SoperatorSafetyPauseError("Token-writer Pod inventory is ambiguous")
            row["selector"] = labels
        result.append(row)
    keys = [(r["kind"], r["namespace"], r["name"]) for r in result]
    if len(set(keys)) != len(keys) or sum(r["kind"] == "Deployment" for r in result) != 1:
        raise SoperatorSafetyPauseError("Token-writer uninstall requires one exact Deployment")
    return result


class NativeGraphTransition:
    """Own a frozen retirement witness and fenced, checkpointed progress."""

    def __init__(
        self,
        state: Mapping[str, Any],
        *,
        run: Callable[..., Any],
        persist: Callable[[Mapping[str, Any]], None],
        authority: Callable[[], object],
    ):
        self.state = copy.deepcopy(dict(state))
        self.run, self.persist, self.authority = run, persist, authority
        if self.state.get("schema") != SCHEMA:
            raise SoperatorSafetyPauseError("Native graph transition schema is invalid")
        if soperator_sha256(self.state["admission"]) != self.state.get("admissionSha256"):
            raise SoperatorSafetyPauseError("Native graph transition witness is invalid")
        if self.state.get("phase") not in {
            "intent-recorded",
            "uninstall-enabled",
            "deletion-pending",
            "cleanup-pending",
            "verified-absent",
        }:
            raise SoperatorSafetyPauseError("Native graph transition phase is invalid")

    @property
    def admission(self) -> Mapping[str, Any]:
        return self.state["admission"]

    def save(self, phase: str) -> None:
        self.authority()
        self.state["phase"] = phase
        self.persist(copy.deepcopy(self.state))

    def get(self, kind: str, namespace: str, name: str) -> Mapping[str, Any]:
        result = self.run(
            ["kubectl", "-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"]
        )
        # Only successful --ignore-not-found with empty output is absence.
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def _resource(self, which: str) -> Mapping[str, Any]:
        row = self.admission[which]
        return self.get(HR, row["namespace"], row["name"])

    def _cas(self, resource: Mapping[str, Any], spec: Mapping[str, Any]) -> None:
        self.authority()
        patch_spec(self.run, resource, spec, authority=self.authority)

    def _assert_parent_identity(self, parent: Mapping[str, Any]) -> None:
        expected = self.admission["parent"]
        if not parent or parent["metadata"].get("uid") != expected["uid"]:
            raise SoperatorSafetyPauseError("Native retirement parent was replaced")
        if ownership_digest(parent) != expected["ownershipSha256"]:
            raise SoperatorSafetyPauseError("Native retirement parent ownership changed")

    def _published_parent(self) -> Mapping[str, Any]:
        parent = self._resource("parent")
        self._assert_parent_identity(parent)
        intent = self.state.get("parentPublication", {})
        allowed = (
            {intent.get("beforeSha256"), intent.get("afterSha256")}
            if intent
            else {self.state.get("publishedSpecSha256")}
        )
        if spec_digest(parent) not in allowed:
            raise SoperatorSafetyPauseError("Native retirement parent publication changed")
        return parent

    def suspend_parent(self, suspend: bool) -> None:
        parent = self._published_parent()
        if parent["spec"].get("suspend", False) != suspend:
            if suspend:
                status = parent.get("status", {})
                if status.get("observedGeneration") == parent["metadata"].get("generation") and any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in status.get("conditions", [])
                ):
                    self._record_reconciled_parent(parent)
            self._cas(parent, {**parent["spec"], "suspend": suspend})

    def _record_reconciled_parent(self, parent: Mapping[str, Any]) -> None:
        manifest = self.parent_manifest(parent)
        self.state["parentReconciliation"] = {
            "specSha256": spec_digest(parent),
            "revision": str(parent["status"]["history"][0]["version"]),
            "manifestSha256": soperator_sha256(manifest),
        }
        self.save(self.state["phase"])

    def parent_manifest(self, parent: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        history = parent.get("status", {}).get("history", [])
        if not history or history[0].get("status") != "deployed":
            raise SoperatorSafetyPauseError("Native parent has no deployed Helm revision")
        spec = parent["spec"]
        result = self.run(
            [
                "helm",
                "get",
                "manifest",
                spec.get("releaseName") or identity(parent)[1],
                "-n",
                spec.get("storageNamespace") or identity(parent)[0],
                "--revision",
                str(history[0]["version"]),
            ]
        )
        return [row for row in yaml.safe_load_all(result.stdout) if isinstance(row, Mapping)]

    def resume_child(self, namespace: str, name: str) -> None:
        parent = self._published_parent()
        child = self.get(HR, namespace, name)
        if not owned_by(child, parent):
            raise SoperatorSafetyPauseError("Native stage child ownership changed")
        manifest = self.parent_manifest(parent)
        matches = [
            document
            for document in manifest
            if document.get("kind") == "HelmRelease" and identity(document) == (namespace, name)
        ]
        if len(matches) != 1:
            raise SoperatorSafetyPauseError(
                "Native stage child differs from its published Helm contract"
            )
        expected = canonical_spec(self.run, child, matches[0]["spec"])
        if spec_digest({"spec": expected}) != spec_digest(child):
            from .soperator_child_publication import materialize_child

            child = materialize_child(self, parent, child, manifest, expected)
        row = witness(child)
        row["publicationSha256"] = spec_digest(parent)
        opened = self.state.setdefault("openedChildren", {})
        key = namespace + "/" + name
        previous = opened.get(key)
        if previous is not None and (
            previous["uid"] != row["uid"]
            or (
                previous["publicationSha256"] == row["publicationSha256"]
                and previous["specSha256"] != row["specSha256"]
            )
        ):
            raise SoperatorSafetyPauseError("Native stage child changed after opening intent")
        opened[key] = row
        self.save(self.state["phase"])
        if child["spec"].get("suspend") is not False:
            self._cas(child, {**child["spec"], "suspend": False})

    def publish_parent(self, desired: Mapping[str, Any]) -> None:
        """Keep subsequent staged/stable parent writes fenced after retirement."""
        if self.state["phase"] != "verified-absent":
            raise SoperatorSafetyPauseError("Native retirement cleanup is not complete")
        from .soperator_child_publication import recover_pending_materializations
        from .soperator_vmagent_recovery import recover_pending

        recover_pending_materializations(self)
        recover_pending(self)
        self.assert_cleanup()
        parent = self._published_parent()
        target_spec = canonical_spec(self.run, parent, desired["spec"])
        self.state["parentPublication"] = {
            "beforeSha256": spec_digest(parent),
            "afterSha256": spec_digest({"spec": target_spec}),
        }
        self.save("verified-absent")
        self._cas(parent, target_spec)

    def frontier(self, resources: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """Remove only the authenticated retiring identity from the desired frontier."""
        child = self.admission["child"]
        result = []
        for row in resources:
            if identity(row) == (child["namespace"], child["name"]):
                if self.state["phase"] == "verified-absent":
                    raise SoperatorSafetyPauseError("Retired token writer reappeared")
                assert_witness(row, child)
                if row.get("metadata", {}).get("deletionTimestamp") and self.state["phase"] not in {
                    "deletion-pending",
                    "cleanup-pending",
                    "verified-absent",
                }:
                    raise SoperatorSafetyPauseError(
                        "Token writer began deletion outside retirement"
                    )
            else:
                result.append(row)
        return result

    def assert_cleanup(self) -> None:
        """A completed receipt does not permit a writer or workload to reappear."""
        if self._resource("child"):
            raise SoperatorSafetyPauseError("Retired token writer reappeared")
        for row in self.admission["inventory"]:
            if self.get(row["kind"], row["namespace"], row["name"]):
                raise SoperatorSafetyPauseError("Retired token-writer workload reappeared")
            if row["kind"] == "Deployment":
                selector = ",".join(f"{k}={v}" for k, v in sorted(row["selector"].items()))
                pods = json.loads(
                    self.run(
                        [
                            "kubectl",
                            "-n",
                            row["namespace"],
                            "get",
                            "pods",
                            "-l",
                            selector,
                            "-o",
                            "json",
                        ]
                    ).stdout
                )
                if pods.get("items") != []:
                    raise SoperatorSafetyPauseError("Retired token-writer Pods remain")

    def verify_readonly(self) -> None:
        parent = self._resource("parent")
        self._assert_parent_identity(parent)
        if self.state["phase"] == "verified-absent":
            self._published_parent()
            self.assert_cleanup()
            return
        if spec_digest(parent) not in {
            self.admission["parent"]["specSha256"],
            self.state.get("publishedSpecSha256"),
        }:
            raise SoperatorSafetyPauseError("Native retirement parent specification changed")
        child = self._resource("child")
        if child:
            self.frontier([child])
        elif self.state["phase"] not in {"deletion-pending", "cleanup-pending"}:
            raise SoperatorSafetyPauseError(
                "Token writer disappeared outside retirement publication"
            )

    def fence(self) -> set[tuple[str, str]]:
        parent = self._resource("parent")
        self._assert_parent_identity(parent)
        if self.state.get("publishedSpecSha256") == spec_digest(parent):
            child = self._resource("child")
            if child:
                assert_witness(child, self.admission["child"])
                if child["spec"].get("suspend") is not False:
                    raise SoperatorSafetyPauseError("Retiring child lost its uninstall authority")
            return set()
        pending = set()
        for which in ("parent", "child"):
            row = self._resource(which)
            assert_witness(row, self.admission[which])
            if row["metadata"].get("deletionTimestamp"):
                raise SoperatorSafetyPauseError(
                    "Native retirement cannot fence a terminating source"
                )
            if row["spec"].get("suspend") is not True:
                self._cas(row, {**row["spec"], "suspend": True})
            pending.add(identity(row))
        return pending

    def publish(self, desired_parent: Mapping[str, Any]) -> None:
        """Enable uninstall before letting the owning parent prune its child."""
        parent = self._resource("parent")
        expected = self.admission["parent"]
        self._assert_parent_identity(parent)
        target_spec = canonical_spec(self.run, parent, desired_parent["spec"])
        desired_sha = spec_digest({"spec": target_spec})
        published = self.state.get("publishedSpecSha256")
        if published and published != desired_sha:
            raise SoperatorSafetyPauseError("Native retirement target publication changed")
        current_sha = spec_digest(parent)
        if published and current_sha == published:
            self.fence()
            if parent.get("spec", {}).get("suspend"):
                self._cas(parent, {**parent["spec"], "suspend": False})
            return
        assert_witness(parent, expected)
        if parent["spec"].get("suspend") is not True:
            raise SoperatorSafetyPauseError("Native retirement parent is not fenced")
        child = self._resource("child")
        if not child:
            raise SoperatorSafetyPauseError("Token writer disappeared before parent publication")
        assert_witness(child, self.admission["child"])
        if child["metadata"].get("deletionTimestamp"):
            raise SoperatorSafetyPauseError("Token writer deletion preceded parent publication")
        self.save("intent-recorded")
        if child["spec"].get("suspend") is not False:
            self._cas(child, {**child["spec"], "suspend": False})
        self.save("uninstall-enabled")
        # Resolve RV changes caused by controller status before constructing CAS.
        parent = self._resource("parent")
        assert_witness(parent, expected)
        if parent["spec"].get("suspend") is not True:
            raise SoperatorSafetyPauseError("Native retirement parent escaped its fence")
        self.state["publishedSpecSha256"] = desired_sha
        self.save("deletion-pending")
        self._cas(parent, target_spec)

    def wait_absent(self, *, timeout: float, interval: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            self.authority()
            parent = self._resource("parent")
            self._assert_parent_identity(parent)
            if (
                not parent
                or parent["metadata"].get("uid") != self.admission["parent"]["uid"]
                or spec_digest(parent) != self.state.get("publishedSpecSha256")
            ):
                raise SoperatorSafetyPauseError(
                    "Native retirement parent publication lost authority"
                )
            child = self._resource("child")
            if child:
                assert_witness(child, self.admission["child"])
                if child["spec"].get("suspend") is not False:
                    raise SoperatorSafetyPauseError(
                        "Retiring token writer was suspended before uninstall"
                    )
            clear = not child
            if clear:
                self.save("cleanup-pending")
                for row in self.admission["inventory"]:
                    live = self.get(row["kind"], row["namespace"], row["name"])
                    if live:
                        if live["metadata"].get("uid") != row["uid"]:
                            raise SoperatorSafetyPauseError(
                                "Retiring workload identity was replaced"
                            )
                        clear = False
                    if row["kind"] == "Deployment":
                        selector = ",".join(f"{k}={v}" for k, v in sorted(row["selector"].items()))
                        pods = json.loads(
                            self.run(
                                [
                                    "kubectl",
                                    "-n",
                                    row["namespace"],
                                    "get",
                                    "pods",
                                    "-l",
                                    selector,
                                    "-o",
                                    "json",
                                ]
                            ).stdout
                        )
                        clear = clear and pods.get("items") == []
            status = parent.get("status", {})
            ready = status.get("observedGeneration") == parent["metadata"].get(
                "generation"
            ) and any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in status.get("conditions", [])
            )
            if clear and ready:
                self._record_reconciled_parent(parent)
                self.save("verified-absent")
                return
            if time.monotonic() >= deadline:
                raise SoperatorSafetyPauseError(
                    "Native token-writer uninstall has not completed; resume the same deployment"
                )
            time.sleep(interval)


def capture_transition(
    *,
    resources: Sequence[Mapping[str, Any]],
    desired: Mapping[str, Any],
    outer: Mapping[str, Any],
    target: Mapping[str, str],
    run: Callable[..., Any],
    predecessor_graph: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    classification = classify_graph(resources, desired, outer)
    if not classification["retiring"]:
        return None
    if normalized_graph(predecessor_graph) != normalized_graph(desired):
        raise SoperatorSafetyPauseError(
            "Token writer retirement changed unrelated native graph contracts"
        )
    writer_rows = [r for r in predecessor_graph["releases"] if r["releaseName"] == OWNED_WRITER]
    if len(writer_rows) != 1:
        raise SoperatorSafetyPauseError(
            "Token writer is absent from the verified predecessor graph"
        )
    writer_row = writer_rows[0]
    parent = next((r for r in resources if identity(r) == identity(outer)), None)
    child = next(r for r in resources if identity(r) in classification["retiring"])
    if parent is None or any(
        r.get("metadata", {}).get("deletionTimestamp") for r in (parent, child)
    ):
        raise SoperatorSafetyPauseError(
            "Native retirement requires a non-terminating source parent and child"
        )
    if (
        outer.get("spec", {})
        .get("values", {})
        .get("observability", {})
        .get("publicEndpointEnabled")
        is not False
    ):
        raise SoperatorSafetyPauseError("Token writer removal is not native telemetry routing")
    for row in (parent, child):
        if row.get("metadata", {}).get("annotations", {}).get("helm.sh/resource-policy"):
            raise SoperatorSafetyPauseError("Native retirement does not support retained releases")
    history = parent.get("status", {}).get("history", [])
    if not history or history[0].get("status") != "deployed":
        raise SoperatorSafetyPauseError(
            "Native retirement source parent has no deployed Helm revision"
        )
    revision = str(history[0]["version"])

    def manifest(row: Mapping[str, Any], revision: str | None = None) -> list[Mapping[str, Any]]:
        spec = row["spec"]
        namespace = spec.get("storageNamespace") or identity(row)[0]
        name = spec.get("releaseName") or identity(row)[1]
        args = ["helm", "get", "manifest", name, "-n", namespace]
        if revision is not None:
            args += ["--revision", revision]
        return [d for d in yaml.safe_load_all(run(args).stdout) if isinstance(d, Mapping)]

    parent_manifest = manifest(parent, revision)
    source_child = [
        r
        for r in parent_manifest
        if r.get("kind") == "HelmRelease" and identity(r) == identity(child)
    ]
    if len(source_child) != 1 or spec_digest(
        {"spec": canonical_spec(run, child, source_child[0]["spec"])}
    ) != spec_digest(child):
        raise SoperatorSafetyPauseError(
            "Token writer does not match the deployed parent Helm content"
        )
    source = child["spec"].get("chartRef", {})
    if (
        source.get("kind") != writer_row["sourceKind"]
        or source.get("name") != writer_row["sourceName"]
    ):
        raise SoperatorSafetyPauseError("Token writer lost its frozen chart source")
    source_ns = source.get("namespace") or identity(child)[0]
    source_live = json.loads(
        run(
            ["kubectl", "-n", source_ns, "get", source["kind"], source["name"], "-o", "json"]
        ).stdout
    )
    expected_source = [
        r
        for r in sources
        if r.get("kind") == source["kind"] and identity(r) == (source_ns, source["name"])
    ]
    if len(expected_source) != 1 or not contains_declared(
        {k: v for k, v in source_live.get("spec", {}).items() if k != "suspend"},
        {k: v for k, v in expected_source[0].get("spec", {}).items() if k != "suspend"},
    ):
        raise SoperatorSafetyPauseError(
            "Token writer chart source differs from verified release defaults"
        )
    digest = source_live.get("status", {}).get("artifact", {}).get("digest", "")
    if digest.removeprefix("sha256:") != str(writer_row["revision"]).removeprefix("sha256:"):
        raise SoperatorSafetyPauseError(
            "Token writer chart artifact differs from the frozen package"
        )
    if source["kind"] == "HelmChart":
        ref = source_live["spec"]["sourceRef"]
        repo = json.loads(
            run(["kubectl", "-n", source_ns, "get", ref["kind"], ref["name"], "-o", "json"]).stdout
        )
        expected_repo = [
            r
            for r in sources
            if r.get("kind") == ref["kind"] and identity(r) == (source_ns, ref["name"])
        ]
        if len(expected_repo) != 1 or not contains_declared(
            repo.get("spec"), expected_repo[0].get("spec")
        ):
            raise SoperatorSafetyPauseError("Token writer chart repository changed")
    child_history = child.get("status", {}).get("history", [])
    if not child_history or child_history[0].get("status") != "deployed":
        raise SoperatorSafetyPauseError("Token writer has no deployed revision")
    child_revision = str(child_history[0]["version"])
    child_manifest = manifest(child, child_revision)
    declared = child["spec"].get("values", {}).get("resources")
    if not isinstance(declared, list) or len(declared) != len(child_manifest):
        raise SoperatorSafetyPauseError(
            "Token writer installed inventory differs from its raw chart inputs"
        )
    for document in declared:
        matches = [
            d
            for d in child_manifest
            if d.get("kind") == document.get("kind") and identity(d) == identity(document)
        ]
        if len(matches) != 1 or not contains_declared(matches[0], document):
            raise SoperatorSafetyPauseError(
                "Token writer manifest differs from the admitted raw resources"
            )
    inventory = qualify_inventory(child_manifest)
    for row in inventory:
        live = json.loads(
            run(
                ["kubectl", "-n", row["namespace"], "get", row["kind"], row["name"], "-o", "json"]
            ).stdout
        )
        metadata = live.get("metadata", {})
        annotations = metadata.get("annotations", {})
        if (
            not metadata.get("uid")
            or annotations.get("meta.helm.sh/release-name") != child["spec"].get("releaseName")
            or annotations.get("meta.helm.sh/release-namespace")
            != child["spec"].get("targetNamespace", identity(child)[0])
            or metadata.get("annotations", {}).get("helm.sh/resource-policy")
            or metadata.get("annotations", {}).get("helm.sh/hook")
        ):
            raise SoperatorSafetyPauseError("Token writer workload ownership is unproven")
        document = next(
            d
            for d in child_manifest
            if d["kind"] == row["kind"] and identity(d) == (row["namespace"], row["name"])
        )
        normalized = copy.deepcopy(dict(document))
        normalized["metadata"] = {
            **normalized["metadata"],
            "uid": metadata["uid"],
            "resourceVersion": metadata["resourceVersion"],
        }
        # Built-in APIs normalize empty lists and add defaults too. Compare
        # against server-normalized declarative content, never a name allowlist.
        normalized = json.loads(
            run(
                ["kubectl", "replace", "--dry-run=server", "-f", "-", "-o", "json"],
                input_text=json.dumps(normalized),
            ).stdout
        )
        normalized.pop("status", None)
        normalized["metadata"] = document["metadata"]
        if not contains_declared(live, normalized):
            raise SoperatorSafetyPauseError(
                "Token writer live workload changed from its Helm manifest"
            )
        row["uid"] = metadata["uid"]
    admission = {
        "target": dict(target),
        "desiredGraphSha256": soperator_sha256(desired),
        "predecessorGraphSha256": soperator_sha256(predecessor_graph),
        "classification": classification,
        "parent": witness(parent),
        "child": witness(child),
        "parentRevision": revision,
        "parentManifestSha256": soperator_sha256(parent_manifest),
        "chartSha256": digest,
        "childRevision": child_revision,
        "childManifestSha256": soperator_sha256(child_manifest),
        "inventory": inventory,
    }
    return {
        "schema": SCHEMA,
        "admission": admission,
        "admissionSha256": soperator_sha256(admission),
        "phase": "intent-recorded",
    }


def prepare_transition(
    paths: Any,
    *,
    target: Mapping[str, str],
    run: Callable[..., Any],
    stored: Mapping[str, Any] | None,
    persist: Callable[[Mapping[str, Any]], None],
    authority: Callable[[], object],
    snapshot: Any,
    source_dir: Any,
) -> NativeGraphTransition | None:
    from .flux_ops import _rendered_soperator_graph_contract, _staged_soperator_outer_release
    from .soperator_release_order import execution_release_graph

    graph = _rendered_soperator_graph_contract(paths.flux_dir)
    if graph is None:
        return None
    graph = execution_release_graph(graph)
    if stored is not None:
        transition = NativeGraphTransition(stored, run=run, persist=persist, authority=authority)
        if transition.admission["target"] != target or transition.admission[
            "desiredGraphSha256"
        ] != soperator_sha256(graph):
            raise SoperatorSafetyPauseError(
                "Frozen native transition belongs to different deployment inputs"
            )
        transition.verify_readonly()
        return transition
    documents = [
        d
        for d in yaml.safe_load_all(run(["kubectl", "kustomize", str(paths.flux_dir)]).stdout)
        if isinstance(d, dict)
    ]
    outer = _staged_soperator_outer_release(documents, graph["releases"])
    resources = json.loads(run(["kubectl", "get", HR, "-A", "-o", "json"]).stdout)["items"]
    classification = classify_graph(resources, graph, outer)
    if not classification["retiring"]:
        return None
    from .soperator_adapter import load_soperator_adapter_documents
    from .soperator_flux_graph import (
        SOPERATOR_GRAPH_CONFIGMAP,
        render_soperator_flux_graph_documents,
    )
    from .soperator_release_graph import render_soperator_release_graph

    parent = next(r for r in resources if identity(r) == identity(outer))
    values = parent["spec"].get("values", {})
    source_graph = render_soperator_release_graph(snapshot, source_dir, values)
    sources = render_soperator_flux_graph_documents(
        snapshot,
        values,
        release_graph=source_graph,
        adapter_documents=load_soperator_adapter_documents(paths.flux_dir),
    )
    qualify_parent_source(snapshot, parent, sources=documents, run=run)
    predecessor = next(
        json.loads(r["data"]["graph.json"])
        for r in sources
        if r.get("metadata", {}).get("name") == SOPERATOR_GRAPH_CONFIGMAP
    )
    state = capture_transition(
        resources=resources,
        desired=graph,
        outer=outer,
        target=target,
        run=run,
        predecessor_graph=execution_release_graph(predecessor),
        sources=sources,
    )
    if state is None:
        return None
    transition = NativeGraphTransition(state, run=run, persist=persist, authority=authority)
    return transition


def qualify_parent_source(
    snapshot: Any,
    parent: Mapping[str, Any],
    *,
    sources: Sequence[Mapping[str, Any]],
    run: Callable[..., Any],
) -> None:
    ref = parent["spec"].get("chartRef", {})
    namespace = ref.get("namespace") or identity(parent)[0]
    expected = [
        row
        for row in sources
        if row.get("kind") == "OCIRepository" and identity(row) == (namespace, ref.get("name"))
    ]
    if ref.get("kind") != "OCIRepository" or len(expected) != 1:
        raise SoperatorSafetyPauseError("Native retirement requires the frozen umbrella OCI source")
    source = json.loads(
        run(["kubectl", "-n", namespace, "get", "OCIRepository", ref["name"], "-o", "json"]).stdout
    )
    digest = snapshot.umbrella.digest
    history = parent.get("status", {}).get("history", [])
    version = history[0].get("chartVersion") if history else None
    if (
        not contains_declared(source.get("spec", {}), expected[0]["spec"])
        or source.get("spec", {}).get("ref", {}).get("digest") != digest
        or str(source.get("status", {}).get("artifact", {}).get("revision", "")).rsplit("@", 1)[-1]
        != digest
        or version
        not in {
            snapshot.umbrella.version + "+" + digest.removeprefix("sha256:")[:12],
            snapshot.umbrella.version,
        }
        or (
            version == snapshot.umbrella.version
            and parent.get("status", {}).get("lastAttemptedRevisionDigest") != digest
        )
    ):
        raise SoperatorSafetyPauseError(
            "Native token writer retirement requires the same pinned umbrella release"
        )


def prepare_generation_transition(
    generation: Any, target_ref: str, **kwargs: Any
) -> NativeGraphTransition | None:
    """Inspect the exact desired application bytes without rerendering or publishing."""
    from .deployment_state import _safe_relative

    prefix = f"flux/targets/{target_ref}/"
    with tempfile.TemporaryDirectory(prefix="cxcli-native-admission-") as directory:
        root = Path(directory)
        for name, encoded in generation.files.items():
            if name.startswith(prefix):
                path = root / _safe_relative(name.removeprefix(prefix))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(encoded, validate=True))
                path.chmod(0o600)
        if not (root / "kustomization.yaml").is_file():
            raise SoperatorSafetyPauseError("Campaign native admission has no frozen target bundle")
        from .flux_ops import _rendered_soperator_graph_contract

        if _rendered_soperator_graph_contract(root) is None:
            raise SoperatorSafetyPauseError("Campaign native admission has no frozen desired graph")
        return prepare_transition(SimpleNamespace(flux_dir=root), **kwargs)
