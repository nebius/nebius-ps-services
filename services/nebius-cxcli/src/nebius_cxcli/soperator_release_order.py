"""Order runtime prerequisites in addition to frozen upstream release dependencies."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def execution_release_graph(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source evidence intact while enforcing prerequisite readiness in execution."""
    result = copy.deepcopy(dict(contract))
    releases = result.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("rendered Soperator release graph contract is empty")
    nodes = {row["releaseName"]: row for row in releases}
    if len(nodes) != len(releases):
        raise ValueError("Soperator execution graph has duplicate release identities")
    upstream = {row.get("upstreamReleaseName"): row for row in releases}
    profile = upstream.get("soperator-fluxcd-security-profiles-operator")
    certificates = upstream.get("soperator-fluxcd-cert-manager")
    if profile is not None and certificates is not None:
        # The profile controller creates cert-manager Issuers and Certificates.
        # Its upstream HelmRelease does not declare that runtime dependency.
        required = profile.setdefault("dependencies", [])
        if certificates["releaseName"] not in required:
            required.append(certificates["releaseName"])

    pending = dict(nodes)
    stages: dict[str, int] = {}
    while pending:
        progressed = False
        for name, row in tuple(pending.items()):
            dependencies = row.get("dependencies", [])
            if set(dependencies) - nodes.keys():
                raise ValueError(f"Soperator execution release {name} has unknown dependencies")
            if all(dependency in stages for dependency in dependencies):
                # Never advance a release earlier than its frozen source stage.
                stage = max(
                    int(row.get("stage") or 0),
                    max((stages[dependency] + 1 for dependency in dependencies), default=0),
                )
                row["stage"] = stages[name] = stage
                pending.pop(name)
                progressed = True
        if not progressed:
            raise ValueError("Soperator execution release graph contains a dependency cycle")
    return result
