"""Bind owned web images to the clean checkout actually used by Compose build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


class RuntimeEvidenceError(RuntimeError):
    """A deployment cannot certify the selected source revision."""


def source_identity(target: dict) -> dict:
    return {key: target[key] for key in ("path", "head")}


def command_json(command, argv):
    result = command(argv)
    if result.returncode:
        raise RuntimeEvidenceError("Owned deployment inspection failed.")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError) as error:
        raise RuntimeEvidenceError(
            "Owned deployment inspection returned invalid JSON."
        ) from error


def compose_build_model(state, target, command):
    """Read model privately: resolved environment values must never be emitted."""
    model = command_json(
        command,
        [
            "docker",
            "compose",
            "--project-name",
            state["compose_project"],
            "--project-directory",
            target["path"],
            "config",
            "--format",
            "json",
        ],
    )
    web = model.get("services", {}).get("web", {})
    build = web.get("build")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict):
        raise RuntimeEvidenceError("Owned deployment requires a local web build.")
    root = Path(target["path"]).resolve()
    context = Path(build.get("context", ""))
    context = context if context.is_absolute() else root / context
    dockerfile = Path(build.get("dockerfile", "Dockerfile"))
    dockerfile = dockerfile if dockerfile.is_absolute() else root / dockerfile
    if (
        context.resolve() != root
        or not dockerfile.resolve().is_relative_to(root)
        or dockerfile.is_symlink()
        or not dockerfile.is_file()
        or build.get("additional_contexts")
        or build.get("dockerfile_inline")
        or web.get("volumes")
    ):
        raise RuntimeEvidenceError(
            "Owned deployment build must use the exact checkout without web mounts."
        )
    image = web.get("image") or state["compose_project"] + "-web"
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise RuntimeEvidenceError("Owned deployment image name is invalid.")
    return image, hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()


def load_build(state, binding, target):
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise RuntimeEvidenceError("Owned deployment build receipt is missing.")
    relative = Path(binding["path"])
    root = Path(state["run_root"])
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not str(relative).startswith("evidence/runtime/")
    ):
        raise RuntimeEvidenceError("Owned deployment receipt path is unsafe.")
    path = root / relative
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise RuntimeEvidenceError("Owned deployment receipt path is symlinked.")
    if (
        not path.is_file()
        or path.stat().st_nlink != 1
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 65536
    ):
        raise RuntimeEvidenceError("Owned deployment receipt is unavailable or unsafe.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise RuntimeEvidenceError("Owned deployment receipt changed.")
    value = json.loads(raw)
    if (
        value.get("schema") != "agentic-sdlc/runtime-build-v1"
        or value.get("verification_id") != state["verification_id"]
        or value.get("target") != source_identity(target)
        or not str(value.get("image_id", "")).startswith("sha256:")
    ):
        raise RuntimeEvidenceError(
            "Owned deployment does not match the selected checkout revision."
        )
    return value


def loopback_binding(ports: object) -> dict:
    """Return the sole observed published binding, independent of container port."""
    if not isinstance(ports, dict):
        raise RuntimeEvidenceError("Owned deployment port inspection is invalid.")
    bindings = []
    for values in ports.values():
        if values is None:
            continue
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise RuntimeEvidenceError("Owned deployment port inspection is invalid.")
        bindings.extend(values)
    if len(bindings) != 1 or bindings[0].get("HostIp") not in {"127.0.0.1", "::1"}:
        raise RuntimeEvidenceError("Owned deployment requires one loopback web binding.")
    port = bindings[0].get("HostPort")
    if not isinstance(port, str) or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RuntimeEvidenceError("Owned deployment web binding has an invalid port.")
    return bindings[0]


def validate_deployment(state, target, command, assert_owned):
    binding = state.get("runtime_build")
    build = load_build(state, binding, target)
    containers = state["resources"].get("containers", [])
    if len(containers) != 2 or state["resources"].get("images") != [build["image_id"]]:
        raise RuntimeEvidenceError(
            "Owned deployment inventory does not match its build."
        )
    web_id, db_id = containers
    web_owner = assert_owned("containers", web_id, state)
    assert_owned("containers", db_id, state)
    assert_owned("images", build["image_id"], state)
    if web_owner["labels"].get("com.docker.compose.service") != "web":
        raise RuntimeEvidenceError("Owned deployment container is not the web service.")
    inspected = command_json(
        command, ["docker", "container", "inspect", web_id, "--format", "{{json .}}"]
    )
    if (
        inspected.get("Id") != web_id
        or inspected.get("Image") != build["image_id"]
        or inspected.get("Mounts")
        or inspected.get("State", {}).get("Running") is not True
    ):
        raise RuntimeEvidenceError(
            "Owned deployment image changed, is mounted over, or is not running."
        )
    published = loopback_binding(inspected.get("NetworkSettings", {}).get("Ports"))
    for name in ("web", "api", "health"):
        value = state["endpoints"].get(name, "")
        if not isinstance(value, str):
            raise RuntimeEvidenceError("Owned deployment endpoint is invalid.")
        try:
            endpoint = urlsplit(value)
            valid = (
                endpoint.scheme == "http"
                and endpoint.hostname in {published["HostIp"], "localhost"}
                and endpoint.username is None
                and endpoint.password is None
                and str(endpoint.port) == published["HostPort"]
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise RuntimeEvidenceError(
                "Owned deployment endpoint does not reach its web container."
            )
    return {"build": binding, "web_container": web_id, "image_id": build["image_id"]}


# Inventory diagnostics never interpolate inspected strings, stderr or exceptions.
# Raw Docker data lives only in the observer's stack; receipts use fixed names,
# booleans and counts. Resource identities appear only in a passing inventory.
OBSERVATION_SCHEMA = "agentic-sdlc/runtime-observation-v1"
MAX_INSPECTION_BYTES = 1024 * 1024
MAX_CANDIDATES = 16
OWNERSHIP_LABEL = "sdlc-workflow-test.verification-id"
COMPOSE_LABEL = "com.docker.compose.project"


def validate_route_path(value):
    """Accept only a bounded plain route; encoded origin/control escapes fail."""
    import re

    if (
        not isinstance(value, str)
        or len(value) > 256
        or not re.fullmatch(r"/[A-Za-z0-9_./~-]*", value)
        or "//" in value
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise RuntimeEvidenceError(
            "Runtime route must be a path without origin, escapes, query or fragment."
        )
    return value


class ObservationFailure(Exception):
    def __init__(self, category):
        self.category = category


class InventoryObserver:
    """One bounded observation; predicate values are intentionally non-textual."""

    def __init__(self, state, command):
        self.state, self.command = state, command
        self.predicates = []
        self.operation = "discover_containers"

    def check(self, name, expected, observed, *, category="product_predicate"):
        passed = type(expected) is type(observed) and expected == observed
        self.predicates.append(
            {"name": name, "expected": expected, "observed": observed, "passed": passed}
        )
        if not passed:
            raise ObservationFailure(category)

    def shape(self, name, valid):
        self.check(name, True, bool(valid), category="inspection_failure")

    def execute(self, argv):
        import subprocess

        try:
            result = self.command(argv)
        except Exception as error:
            if isinstance(error, (OSError, subprocess.TimeoutExpired)) or isinstance(
                error.__cause__, (OSError, subprocess.TimeoutExpired)
            ):
                self.shape("inspection.available", False)
            raise
        self.shape("inspection.command_ok", result.returncode == 0)
        self.shape(
            "inspection.output_bounded",
            isinstance(result.stdout, str)
            and len(result.stdout.encode("utf-8")) <= MAX_INSPECTION_BYTES,
        )
        return result.stdout

    def inspect(self, kind, identifier):
        import re

        self.shape(
            "inspection.identifier_valid",
            isinstance(identifier, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", identifier)
            is not None,
        )
        raw = self.execute(["docker", kind, "inspect", identifier])
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            self.shape("inspection.json_valid", False)
        self.shape(
            "inspection.object_valid",
            isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict),
        )
        item = data[0]
        self.shape(
            "inspection.identity_match",
            item.get("Name" if kind == "volume" else "Id") == identifier,
        )
        if kind in {"container", "image"}:
            self.shape("inspection.config_valid", isinstance(item.get("Config"), dict))
            labels = item["Config"].get("Labels")
        else:
            labels = item.get("Labels")
        if labels is None:
            labels = {}
        self.shape(
            "inspection.labels_valid",
            isinstance(labels, dict)
            and all(
                isinstance(k, str) and isinstance(v, str) for k, v in labels.items()
            ),
        )
        self.check(
            kind + ".ownership",
            True,
            labels.get(OWNERSHIP_LABEL) == self.state["verification_id"]
            and labels.get(COMPOSE_LABEL) == self.state["compose_project"],
        )
        return item

    def ports(self, container, role):
        settings = container.get("NetworkSettings")
        self.shape(role + ".network_settings_valid", isinstance(settings, dict))
        ports = settings.get("Ports")
        self.shape(role + ".ports_valid", isinstance(ports, dict))
        self.shape(
            role + ".bindings_valid",
            all(
                values is None
                or (
                    isinstance(values, list)
                    and all(isinstance(binding, dict) for binding in values)
                )
                for values in ports.values()
            ),
        )
        return ports

    def collect(self, target, web_path, health_path, mode):
        # Two separate filters find partial ownership as well as exact ownership.
        # A candidate with either matching label must pass both before selection.
        candidates = set()
        for label, value in (
            (OWNERSHIP_LABEL, self.state["verification_id"]),
            (COMPOSE_LABEL, self.state["compose_project"]),
        ):
            output = self.execute(
                [
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    f"label={label}={value}",
                ]
            )
            identifiers = output.splitlines()
            self.shape(
                "discovery.candidates_bounded", len(identifiers) <= MAX_CANDIDATES
            )
            candidates.update(identifiers)
        self.shape("discovery.candidates_bounded", len(candidates) <= MAX_CANDIDATES)
        services = {"web": [], "db": []}
        for identifier in sorted(candidates):
            self.operation = "inspect_container"
            item = self.inspect("container", identifier)
            role = item["Config"]["Labels"].get("com.docker.compose.service")
            self.check("container.service_known", True, role in services)
            services[role].append(item)
        for role in services:
            self.check(role + ".candidate_count", 1, len(services[role]))
        web, db = services["web"][0], services["db"][0]
        network_ids = []
        for role, item in (("web", web), ("db", db)):
            self.operation = "inspect_" + role
            self.shape(role + ".state_valid", isinstance(item.get("State"), dict))
            self.check(role + ".running", True, item["State"].get("Running") is True)
            self.shape(
                role + ".mounts_valid",
                isinstance(item.get("Mounts"), list)
                and all(isinstance(mount, dict) for mount in item["Mounts"]),
            )
            self.ports(item, role)
            networks = item["NetworkSettings"].get("Networks")
            self.shape(
                role + ".networks_valid",
                isinstance(networks, dict)
                and all(isinstance(network, dict) for network in networks.values()),
            )
            self.check(role + ".network_count", 1, len(networks))
            network_ids.append(next(iter(networks.values())).get("NetworkID"))
        self.check("shared.network", True, network_ids[0] == network_ids[1])
        self.check("web.mounts_absent", 0, len(web["Mounts"]))
        mounts = db["Mounts"]
        self.check(
            "db.named_volume",
            True,
            len(mounts) == 1
            and mounts[0].get("Type") == "volume"
            and isinstance(mounts[0].get("Name"), str)
            and bool(mounts[0]["Name"])
            and mounts[0].get("Destination")
            in {"/var/lib/postgresql/data", "/var/lib/postgresql"},
        )
        self.operation = "inspect_web_binding"
        try:
            binding = loopback_binding(self.ports(web, "web"))
        except RuntimeEvidenceError:
            self.check("web.loopback_binding", True, False)
        self.check("web.loopback_binding", True, True)
        self.check(
            "db.ports_unpublished",
            0,
            sum(len(v or []) for v in self.ports(db, "db").values()),
        )
        if mode == "acceptance":
            self.operation = "validate_build"
            try:
                build = load_build(self.state, self.state.get("runtime_build"), target)
            except (RuntimeEvidenceError, OSError, ValueError, TypeError):
                self.check(
                    "build.selected_revision", True, False, category="admission_failure"
                )
            self.check("web.build_match", True, web.get("Image") == build["image_id"])
        self.operation = "inspect_image"
        image = self.inspect("image", web.get("Image"))
        self.operation = "inspect_network"
        network = self.inspect("network", network_ids[0])
        self.operation = "inspect_volume"
        volume = self.inspect("volume", mounts[0]["Name"])
        host = "[::1]" if binding["HostIp"] == "::1" else binding["HostIp"]
        origin = f"http://{host}:{binding['HostPort']}"
        return {
            "resources": {
                "containers": [web["Id"], db["Id"]],
                "networks": [network["Id"]],
                "volumes": [volume["Name"]],
                "images": [image["Id"]],
            },
            "endpoints": {
                "web": origin + web_path,
                "health": origin + health_path,
                "api": origin + "/api/v1/tasks",
                "database": "db:5432",
            },
        }


def observe_inventory(state, target, command, *, web_path, health_path, mode):
    validate_route_path(web_path)
    validate_route_path(health_path)
    if mode not in {"acceptance", "development"}:
        raise RuntimeEvidenceError("Invalid observation mode.")
    observer = InventoryObserver(state, command)
    result = {}
    try:
        result = observer.collect(target, web_path, health_path, mode)
        category, outcome = None, "PASS"
    except ObservationFailure as error:
        category, outcome = error.category, "FAIL"
    except Exception:
        # Deliberately discard arbitrary exception messages and tracebacks.
        category, outcome = "observer_exception", "FAIL"
        observer.predicates.append(
            {
                "name": "observer.completed",
                "expected": True,
                "observed": False,
                "passed": False,
            }
        )
    return {
        "outcome": outcome,
        "category": category,
        "operation": observer.operation,
        "predicates": observer.predicates,
        **result,
    }


def observation_summary(value, binding):
    """The retained report subset excludes paths, resource IDs and raw artifacts."""
    return {
        **binding,
        **{
            key: value[key]
            for key in (
                "mode",
                "outcome",
                "category",
                "operation",
                "observed_at",
                "predicates",
            )
        },
        "target_head": (value.get("target") or {}).get("head"),
        "disposition": "blocked_missing_evidence"
        if value["outcome"] == "FAIL"
        else "observed",
    }


def observation_paths(state):
    root = Path(state["run_root"]) / "evidence/runtime"
    if not root.exists() and not root.is_symlink():
        return []
    for item in (root, root.parent):
        if item.is_symlink() or not item.is_dir():
            raise RuntimeEvidenceError("Runtime observation directory is unsafe.")
    paths = sorted(root.glob("observation-*.json"))
    if len(paths) > 128:
        raise RuntimeEvidenceError("Runtime observation ledger exceeds its bound.")
    return paths


def observation_bytes(state, relative):
    import os
    import re
    import stat

    if (
        not isinstance(relative, str)
        or re.fullmatch(r"evidence/runtime/observation-[0-9a-f]{32}\.json", relative)
        is None
    ):
        raise RuntimeEvidenceError("Runtime observation path is invalid.")
    root = Path(state["run_root"])
    path = root / relative
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink():
            raise RuntimeEvidenceError("Runtime observation path is unsafe.")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or metadata.st_size > 65536
            ):
                raise RuntimeEvidenceError("Runtime observation file is unsafe.")
            raw = handle.read(65537)
            if len(raw) > 65536:
                raise RuntimeEvidenceError(
                    "Runtime observation file exceeds its bound."
                )
            return raw
    except OSError as error:
        raise RuntimeEvidenceError(
            "Runtime observation file is unavailable."
        ) from error


def observation_file_digest(state, relative):
    return hashlib.sha256(observation_bytes(state, relative)).hexdigest()


def read_observation_file(state, binding):
    raw = observation_bytes(state, binding.get("path"))
    if hashlib.sha256(raw).hexdigest() != binding.get("sha256"):
        raise RuntimeEvidenceError("Runtime observation digest changed.")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, TypeError) as error:
        raise RuntimeEvidenceError("Runtime observation JSON is invalid.") from error


def read_observation(state, summary):
    import re

    try:
        value = read_observation_file(state, summary)
        required = {
            "schema",
            "verification_id",
            "mode",
            "target",
            "worker_task",
            "assignment_digest",
            "observed_at",
            "source_operation",
            "outcome",
            "category",
            "operation",
            "predicates",
        }
        if value.get("outcome") == "PASS":
            required |= {"resources", "endpoints"}
        predicates = value.get("predicates")
        if (
            set(value) != required
            or value.get("schema") != OBSERVATION_SCHEMA
            or value.get("verification_id") != state["verification_id"]
            or value.get("source_operation") != "record-runtime"
            or value.get("mode") not in {"acceptance", "development"}
            or value.get("outcome") not in {"PASS", "FAIL"}
            or value.get("category")
            not in {
                None,
                "product_predicate",
                "inspection_failure",
                "observer_exception",
                "admission_failure",
            }
            or not isinstance(predicates, list)
            or not 1 <= len(predicates) <= 256
        ):
            raise RuntimeEvidenceError("Runtime observation contract is invalid.")
        if (
            value["operation"]
            not in {
                "discover_containers",
                "inspect_container",
                "inspect_web",
                "inspect_db",
                "inspect_web_binding",
                "validate_build",
                "inspect_image",
                "inspect_network",
                "inspect_volume",
                "select_target",
            }
            or not isinstance(value["observed_at"], str)
            or re.fullmatch(r"[0-9T:+.Z-]{20,40}", value["observed_at"]) is None
        ):
            raise RuntimeEvidenceError("Runtime observation metadata is invalid.")
        target = value["target"]
        if target is not None:
            if (
                not isinstance(target, dict)
                or not {"path", "head"}.issubset(target)
                or not set(target).issubset(
                    {
                        "path",
                        "head",
                        "branch",
                        "run_id",
                        "execution_id",
                        "phase",
                        "basis_sha256",
                    }
                )
                or any(
                    not isinstance(item, str) or len(item) > 4096
                    for item in target.values()
                )
                or re.fullmatch(r"[0-9a-f]{40,64}", target["head"]) is None
                or not Path(target["path"]).is_relative_to(Path(state["run_root"]))
            ):
                raise RuntimeEvidenceError("Runtime observation target is invalid.")
        elif value["outcome"] == "PASS":
            raise RuntimeEvidenceError("Passing runtime observation lacks its target.")
        if value["mode"] == "acceptance":
            if (
                value["worker_task"] is not None
                or value["assignment_digest"] is not None
            ):
                raise RuntimeEvidenceError(
                    "Acceptance observation contains worker authority."
                )
        elif (
            not isinstance(value["worker_task"], str)
            or re.fullmatch(r"TASK-[0-9]{3,}", value["worker_task"]) is None
            or not isinstance(value["assignment_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["assignment_digest"]) is None
        ):
            raise RuntimeEvidenceError(
                "Development observation lacks assignment authority."
            )
        for item in predicates:
            if (
                not isinstance(item, dict)
                or set(item) != {"name", "expected", "observed", "passed"}
                or not isinstance(item["name"], str)
                or re.fullmatch(r"[a-z_]+\.[a-z_]+", item["name"]) is None
                or len(item["name"]) > 64
                or any(
                    type(item[key]) not in {bool, int} or not 0 <= item[key] <= 1048576
                    for key in ("expected", "observed")
                )
                or type(item["passed"]) is not bool
                or item["passed"]
                != (
                    type(item["expected"]) is type(item["observed"])
                    and item["expected"] == item["observed"]
                )
            ):
                raise RuntimeEvidenceError("Runtime observation predicate is invalid.")
        passed = all(item["passed"] for item in predicates)
        if (
            passed != (value["outcome"] == "PASS")
            or passed != (value["category"] is None)
            or observation_summary(
                value, {key: summary[key] for key in ("path", "sha256")}
            )
            != summary
        ):
            raise RuntimeEvidenceError("Runtime observation summary changed.")
        return value
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise RuntimeEvidenceError(
            "Runtime observation is unavailable or invalid."
        ) from error


def require_observation_capacity(state, addition=None):
    entries = state.get("runtime_observations", [])
    if len(entries) >= 128:
        raise RuntimeEvidenceError(
            "Runtime observation capacity reached; retain evidence and clean up."
        )
    candidate = (
        {**state, "runtime_observations": [*entries, addition]} if addition else state
    )
    # Reserve the largest accepted receipt before inspecting. Canonical active
    # state itself has a 1 MiB reader limit; never write a state it cannot read.
    reserve = 0 if addition else 65536
    if (
        len((json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode()) + reserve
        > 1000000
    ):
        raise RuntimeEvidenceError(
            "Runtime observation storage capacity reached; retain evidence and clean up."
        )


def validate_observations(state, *, require_complete=True):
    entries = state.get("runtime_observations")
    if (
        not isinstance(entries, list)
        or any(not isinstance(entry, dict) for entry in entries)
        or len(entries) > 128
        or len({entry.get("path") for entry in entries}) != len(entries)
    ):
        raise RuntimeEvidenceError("Runtime observation ledger is invalid.")
    if require_complete:
        known = {entry["path"] for entry in entries}
        observed = {
            str(path.relative_to(Path(state["run_root"])))
            for path in observation_paths(state)
        }
        if known != observed:
            raise RuntimeEvidenceError(
                "Runtime observation ledger requires owner reconciliation."
            )
    return [read_observation(state, entry) for entry in entries]


def validate_acceptance_inventory(state, target=None):
    observations = validate_observations(state)
    if any(
        item["mode"] == "acceptance" and item["outcome"] == "FAIL"
        for item in observations
    ):
        raise RuntimeEvidenceError(
            "An earlier acceptance inventory failure ends this trial."
        )
    binding = state.get("runtime_inventory")
    matching = [
        item
        for item in state["runtime_observations"]
        if {key: item[key] for key in ("path", "sha256")} == binding
    ]
    if len(matching) != 1:
        raise RuntimeEvidenceError(
            "Canonical acceptance inventory observation is missing."
        )
    value = read_observation(state, matching[0])
    if (
        value["mode"] != "acceptance"
        or value["outcome"] != "PASS"
        or value["worker_task"] is not None
        or value["assignment_digest"] is not None
        or value["resources"] != state["resources"]
        or value["endpoints"] != state["endpoints"]
        or (
            target is not None
            and source_identity(value["target"]) != source_identity(target)
        )
    ):
        raise RuntimeEvidenceError(
            "Runtime inventory does not certify the selected acceptance target."
        )
    return value
