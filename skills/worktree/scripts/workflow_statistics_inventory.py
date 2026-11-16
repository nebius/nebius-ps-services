"""Source-bound architectural observations, independent of task decomposition."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from workflow_security import contains_sensitive
from workflow_state import WorkflowError, digest, stable

SCHEMA = "workflow/architecture-inventory-v1"
REFERENCE = re.compile(r"(?:COMP|LAYER)-[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}\Z")


def text(value, *, limit=500):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or contains_sensitive(value) or re.search(r"https?://|(?:^|\s)/(?:Users|home|private|tmp)/", value)):
        raise WorkflowError("STATISTICS_INVALID", "use bounded public-safe labels, never secrets or private URLs")
    return value.strip()


def _source_relative(source):
    if (not isinstance(source, dict) or set(source) != {"kind", "path", "sha256", "anchor"}
            or source["kind"] not in {"project", "objective"} or not isinstance(source["path"], str)):
        raise WorkflowError("STATISTICS_INVALID", "source reference is invalid")
    relative = source["path"]
    path = Path(relative)
    if (path.is_absolute() or not path.parts or ".." in path.parts
            or path.as_posix() != relative or any(part.startswith(".") for part in path.parts)):
        raise WorkflowError("STATISTICS_INVALID", "source must be a portable relative file")
    return relative


class _SourceObservation:
    """One read segment only; recheck unique sources before accepting its facts."""

    def __init__(self, project, run_dir):
        self.project, self.run_dir = project, run_dir
        self.objective = None
        self.sources = {}

    @staticmethod
    def _read(root, relative):
        from project_specs_lib.source_bundle import read_source
        return hashlib.sha256(read_source(root, relative)).hexdigest()

    def digest(self, source):
        relative = _source_relative(source)  # Validate even a repeated reference.
        root = self.project
        if source["kind"] == "objective":
            if self.objective is None:
                from workflow_objectives import validate_run
                _, self.objective = validate_run(self.run_dir)
            if relative not in {r["snapshot"] for r in self.objective["revisions"]}:
                raise WorkflowError("STATISTICS_INVALID", "source is not an accepted objective revision")
            root = self.run_dir
        key = (source["kind"], relative)
        if key not in self.sources:
            self.sources[key] = (root, self._read(root, relative))
        return self.sources[key][1]

    def finish(self):
        # A workflow lock does not freeze independently edited project files.
        for (_kind, relative), (root, expected) in self.sources.items():
            if self._read(root, relative) != expected:
                raise WorkflowError("STATISTICS_STALE", "architecture source changed during observation")
        if self.objective is not None:
            from workflow_objectives import validate_run
            if validate_run(self.run_dir)[1] != self.objective:
                raise WorkflowError("STATISTICS_STALE", "accepted objective changed during observation")


def source_digest(source: dict, project: Path, run_dir: Path) -> str:
    """Independent reads never reuse an earlier observation's validation."""
    return _SourceObservation(project, run_dir).digest(source)


def validate(candidate: dict, project: Path, run_dir: Path) -> dict:
    """Validate facts and bind exact source bytes; never infer architecture from names."""
    if (not isinstance(candidate, dict) or set(candidate) != {"schema", "layers", "components", "gaps"}
            or candidate["schema"] != SCHEMA or len(stable(candidate)) > 512 * 1024):
        raise WorkflowError("STATISTICS_INVALID", "architecture inventory schema is invalid")
    result = {"schema": SCHEMA, "layers": [], "components": [], "gaps": []}
    sources = {}
    observation = _SourceObservation(project, run_dir)
    for kind, prefix in (("layers", "LAYER-"), ("components", "COMP-")):
        rows = candidate[kind]
        if not isinstance(rows, list) or len(rows) > 512:
            raise WorkflowError("STATISTICS_INVALID", "inventory facts must be bounded lists")
        seen = set()
        for row in rows:
            keys = {"id", "name", "origin", "sources"} | ({"layer", "features"} if kind == "components" else set())
            if not isinstance(row, dict) or set(row) != keys:
                raise WorkflowError("STATISTICS_INVALID", "inventory fact fields are invalid")
            identity = row["id"]
            if (not isinstance(identity, str) or not REFERENCE.fullmatch(identity)
                    or not identity.startswith(prefix) or row["origin"] not in {"declared", "observed"}
                    or (identity, row["origin"]) in seen):
                raise WorkflowError("STATISTICS_INVALID", "fact identity or origin is invalid")
            seen.add((identity, row["origin"]))
            fact = {"id": identity, "name": text(row["name"]), "origin": row["origin"], "sources": []}
            if not isinstance(row["sources"], list) or not 1 <= len(row["sources"]) <= 32:
                raise WorkflowError("STATISTICS_INVALID", "every fact needs source references")
            if row["origin"] == "observed" and not any(s.get("kind") == "project" for s in row["sources"] if isinstance(s, dict)):
                raise WorkflowError("STATISTICS_INVALID", "existing architecture needs project source evidence")
            for source in row["sources"]:
                actual = observation.digest(source)
                relative = source["path"]
                if source["sha256"] != actual:
                    raise WorkflowError("STATISTICS_STALE", "architecture source changed; refresh through its owner")
                if source["kind"] == "objective":
                    if relative != observation.objective["revisions"][-1]["snapshot"]:
                        raise WorkflowError("STATISTICS_STALE", "new facts must reference the current accepted objective")
                sources[source["kind"] + ":" + relative] = actual
                fact["sources"].append({"kind": source["kind"], "path": relative, "sha256": actual, "anchor": text(source["anchor"])})
            if kind == "components":
                if row["layer"] not in {r["id"] for r in result["layers"]}:
                    raise WorkflowError("STATISTICS_INVALID", "component layer is unknown")
                if (not isinstance(row["features"], list) or len(row["features"]) > 512
                        or any(not isinstance(f, str) or not re.fullmatch(r"FEAT-[0-9]{3,}", f) for f in row["features"])):
                    raise WorkflowError("STATISTICS_INVALID", "feature references are invalid")
                fact.update(layer=row["layer"], features=sorted(set(row["features"])))
            result[kind].append(fact)
    if not isinstance(candidate["gaps"], list) or len(candidate["gaps"]) > 128:
        raise WorkflowError("STATISTICS_INVALID", "inventory gaps must be bounded")
    result["gaps"] = [text(gap) for gap in candidate["gaps"]]
    observation.finish()
    result["source_sha256"] = digest(sources)
    return result


def summarize(inventory: dict | None) -> dict:
    if inventory is None:
        return {"layers": [], "components": [], "layer_count": None, "component_count": None,
                "gaps": ["Architecture inventory has not been recorded."], "disagreements": []}
    result = dict(inventory)
    result["layer_count"] = len({r["id"] for r in inventory["layers"]})
    result["component_count"] = len({r["id"] for r in inventory["components"]})
    result["existing_components"] = sorted({r["id"] for r in inventory["components"] if r["origin"] == "observed"})
    result["planned_components"] = sorted({r["id"] for r in inventory["components"] if r["origin"] == "declared"})
    result["new_planned_components"] = sorted(set(result["planned_components"]) - set(result["existing_components"]))
    result["component_count_by_origin"] = {origin: len({r["id"] for r in inventory["components"] if r["origin"] == origin})
                                           for origin in ("declared", "observed")}
    result["disagreements"] = []
    for kind in ("layers", "components"):
        for identity in sorted({r["id"] for r in inventory[kind]}):
            facts = [r for r in inventory[kind] if r["id"] == identity]
            if len({(r["name"], r.get("layer")) for r in facts}) > 1:
                result["disagreements"].append(identity)
    return result
