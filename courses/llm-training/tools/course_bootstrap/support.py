#!/usr/bin/env python3
"""Reusable monitoring, private output preparation and copy-only history tools."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

SCHEMA = "gpu-course-monitoring/v2"
SCRAPE_JOB = "cxcli-pushgateway"
OWNER = "cxcli.nebius.com/app-owner"


def kube_command(connection, *args):
    kubeconfig, context = connection
    return ["kubectl", "--kubeconfig", str(kubeconfig), "--context", context, *args]


def kube(connection, *args):
    return json.loads(
        subprocess.check_output(
            kube_command(connection, *args, "-o", "json"), text=True, timeout=30
        )
    )


def one(items, description):
    if len(items) != 1:
        raise ValueError(f"Require exactly one {description}; found {len(items)}")
    return items[0]


def ready(resource, description):
    if resource["metadata"].get("deletionTimestamp") or resource.get("spec", {}).get(
        "suspend"
    ):
        raise ValueError(f"{description} is suspended or terminating")
    if not any(
        c.get("type") == "Ready"
        and c.get("status") == "True"
        and c.get("observedGeneration") == resource["metadata"]["generation"]
        for c in resource.get("status", {}).get("conditions", [])
    ):
        raise ValueError(f"{description} is not Ready at its current generation")


def artifact(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe generated artifact path")
    result = root / path
    if any(
        p.is_symlink() for p in (result, *result.parents)
    ) or not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe generated artifact path")
    if not result.is_file():
        raise ValueError("Required generated artifact is missing")
    return result.read_bytes()


def accepted_documents(generated, baseline, target):
    import yaml

    documents = []
    prefix = f"flux/targets/{target}/"
    for group in ("protected_files", "ordinary_files"):
        entries = baseline[group]
        if not isinstance(entries, dict):
            raise ValueError("Unsupported accepted artifact inventory")  # noqa: TRY004 -- malformed external document
        for relative, expected in entries.items():
            # Validate the path even when the artifact belongs to another target.
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe accepted artifact path")
            if not relative.startswith(prefix):
                continue
            content = artifact(generated, relative)
            if "sha256:" + hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(
                    "Generated monitoring artifacts differ from the accepted snapshot"
                )
            if path.suffix in (".yaml", ".yml"):
                documents.extend(
                    d for d in yaml.safe_load_all(content) if isinstance(d, dict)
                )
    return documents


def app_row(manifest, target, component):
    rows = [
        r
        for r in manifest["runtime_config"]["apps"]["charts"]
        if r.get("id") == component
        and r.get("enabled") is True
        and r.get("target_ref", r.get("instance_id")) == target
    ]
    row = one(
        rows,
        f"installed {component} selection for the target; run cxcli grafana install --pushgateway first",
    )
    if row.get("instance_id") != target:
        raise ValueError("Conflicting application target identity")
    return row


def release_spec(value):
    """Normalize only the Flux API defaults also recognized by cxcli."""
    result = {"suspend": False, **copy.deepcopy(value)}
    if isinstance(result.get("chart", {}).get("spec"), dict):
        result["chart"]["spec"].setdefault("reconcileStrategy", "ChartVersion")
    if isinstance(result.get("uninstall"), dict):
        result["uninstall"].setdefault("deletionPropagation", "background")
    return result


def installed_release(connection, documents, row):
    expected = one(
        [
            d
            for d in documents
            if d.get("kind") == "HelmRelease"
            and d.get("spec", {}).get("targetNamespace", d["metadata"]["namespace"])
            == row["namespace"]
            and d.get("spec", {}).get("releaseName", d["metadata"]["name"])
            == row["release-name"]
        ],
        f"accepted {row['id']} HelmRelease",
    )
    meta = expected["metadata"]
    live = kube(connection, "get", "helmrelease", meta["name"], "-n", meta["namespace"])
    owner = meta.get("annotations", {}).get(OWNER)
    if (
        not owner
        or live["metadata"].get("annotations", {}).get(OWNER) != owner
        or live["spec"].get("kubeConfig")
        or release_spec(live["spec"]) != release_spec(expected["spec"])
    ):
        raise ValueError(
            f"Live {row['id']} differs from its accepted owner or specification"
        )
    ready(live, row["id"])
    values = live["spec"].get("values", {})
    if values.get("ingress", {}).get("enabled") or any(
        r.get("enabled") for r in values.get("route", {}).values()
    ):
        raise ValueError("Course monitoring requires private access")
    return live


def private_service(service):
    spec = service["spec"]
    if spec.get("type", "ClusterIP") != "ClusterIP" or spec.get("externalIPs"):
        raise ValueError("Monitoring Service must remain private ClusterIP")


def helm_service(connection, row, release):
    namespace = (
        release["spec"].get("values", {}).get("namespaceOverride") or row["namespace"]
    )
    services = kube(connection, "get", "services", "-n", namespace)["items"]
    service = one(
        [
            s
            for s in services
            if s["metadata"].get("annotations", {}).get("meta.helm.sh/release-name")
            == row["release-name"]
            and s["metadata"]
            .get("annotations", {})
            .get("meta.helm.sh/release-namespace")
            == row["namespace"]
            and s["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by")
            == "Helm"
        ],
        f"Helm-owned {row['id']} Service",
    )
    private_service(service)
    port = one(
        [p for p in service["spec"]["ports"] if p.get("protocol", "TCP") == "TCP"],
        "monitoring Service TCP port",
    )["port"]
    if not service["spec"].get("selector"):
        raise ValueError("Monitoring Service must select its installed pods")
    ready_endpoints(connection, service, port)
    return service, port


def service_hosts(service):
    name, namespace = service["metadata"]["name"], service["metadata"]["namespace"]
    return {f"{name}.{namespace}.svc", f"{name}.{namespace}.svc.cluster.local"}


def private_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.port
        or not re.fullmatch(
            r"[a-z0-9-]+\.[a-z0-9-]+\.svc(?:\.cluster\.local)?\.?",
            parsed.hostname or "",
        )
    ):
        raise ValueError("Require a private Kubernetes Service HTTP URL")
    return parsed


def ready_endpoints(connection, service, port):
    meta = service["metadata"]
    port_spec = one(
        [p for p in service["spec"]["ports"] if p["port"] == port], "Service port"
    )
    slices = kube(
        connection,
        "get",
        "endpointslices",
        "-n",
        meta["namespace"],
        "-l",
        "kubernetes.io/service-name=" + meta["name"],
    )["items"]
    addresses = set()
    for item in slices:
        if not any(
            o.get("uid") == meta["uid"]
            for o in item["metadata"].get("ownerReferences", [])
        ):
            raise ValueError("EndpointSlice has a different Service owner")
        endpoint_port = one(
            [
                p["port"]
                for p in item.get("ports", [])
                if p.get("name", "") == port_spec.get("name", "")
                and p.get("protocol", "TCP") == "TCP"
            ],
            "EndpointSlice port",
        )
        for endpoint in item.get("endpoints", []):
            if endpoint.get("conditions", {}).get("ready") and not endpoint.get(
                "conditions", {}
            ).get("terminating", False):
                for address in endpoint["addresses"]:
                    host = f"[{address}]" if ":" in address else address
                    addresses.add(f"{host}:{endpoint_port}")
    if len(addresses) != 1:
        raise ValueError("Require one ready monitoring Service endpoint")
    return addresses


def native_monitoring(connection, documents):
    cm = one(
        [
            d
            for d in documents
            if d.get("kind") == "ConfigMap"
            and d.get("metadata", {}).get("name")
            == "nebius-cxcli-soperator-release-graph"
        ],
        "accepted Soperator release graph",
    )
    live_cm = kube(
        connection,
        "get",
        "configmap",
        cm["metadata"]["name"],
        "-n",
        cm["metadata"]["namespace"],
    )
    graph = json.loads(cm["data"]["graph.json"])
    if (
        graph.get("schema") != "nebius-cxcli.soperator-release-graph.v2"
        or json.loads(live_cm["data"]["graph.json"]) != graph
    ):
        raise ValueError("Live Soperator graph differs from the accepted graph")
    node = one(
        [
            r
            for r in graph["releases"]
            if r.get("upstreamReleaseName") == "soperator-fluxcd-vm-stack"
            and r.get("owner") == "third-party"
            and r.get("sourceKind") == "HelmChart"
        ],
        "native metrics release",
    )
    chart = one(
        [
            d
            for d in documents
            if d.get("kind") == "HelmChart"
            and d["metadata"]["name"] == node["sourceName"]
            and d["metadata"]["namespace"] == node["namespace"]
        ],
        "accepted native metrics chart",
    )
    if (
        chart["spec"]["chart"] != "victoria-metrics-k8s-stack"
        or chart["metadata"]
        .get("annotations", {})
        .get("soperator.nebius.ai/package-sha256")
        != node["revision"]
    ):
        raise ValueError("Unexpected native monitoring chart identity")
    hr = kube(
        connection, "get", "helmrelease", node["releaseName"], "-n", node["namespace"]
    )
    if (
        hr["spec"].get("chartRef")
        != {
            "kind": "HelmChart",
            "name": node["sourceName"],
            "namespace": node["namespace"],
        }
        or hr["metadata"].get("labels", {}).get("soperator.nebius.ai/release-graph")
        != cm["metadata"]["labels"]["soperator.nebius.ai/release-graph"]
    ):
        raise ValueError("Live native monitoring release has a different owner")
    ready(hr, "native monitoring")
    namespace, release = hr["spec"]["targetNamespace"], hr["spec"]["releaseName"]
    agents = kube(connection, "get", "vmagents.operator.victoriametrics.com", "-A")[
        "items"
    ]
    agent = one(
        [
            a
            for a in agents
            if a["metadata"]["namespace"] == namespace
            and a["metadata"].get("labels", {}).get("app.kubernetes.io/instance")
            == release
        ],
        "native VMAgent",
    )
    return namespace, release, agent, agents


def verify_scrape(agent, pushgateway, port):
    import yaml

    spec = agent["spec"]
    if (
        int(spec.get("replicaCount", 1)) != 1
        or spec.get("daemonSetMode")
        or int(spec.get("shardCount", 1)) != 1
        or spec.get("overrideHonorLabels")
    ):
        raise ValueError("Require one native scraper preserving Pushgateway labels")
    jobs = yaml.safe_load(spec.get("inlineScrapeConfig") or "[]")
    job = one(
        [j for j in jobs if j.get("job_name") == SCRAPE_JOB],
        "native cxcli-pushgateway scrape job",
    )
    targets = [
        t for config in job.get("static_configs", []) for t in config.get("targets", [])
    ]
    accepted = {f"{host}:{port}" for host in service_hosts(pushgateway)}
    if (
        len(targets) != 1
        or targets[0] not in accepted
        or job.get("scrape_interval") != "5s"
        or job.get("honor_labels") is not True
        or job.get("scheme", "http") != "http"
        or job.get("metrics_path", "/metrics") != "/metrics"
    ):
        raise ValueError(
            "Native Pushgateway scrape differs from its installed Service or label contract"
        )
    return targets[0]


def verify_metrics_write(agent, service, port):
    aliases = service_hosts(service)
    writes = [
        private_url(r["url"])
        for r in agent["spec"].get("remoteWrite", [])
        if r.get("url", "").startswith("http://") and ".svc" in r.get("url", "")
    ]
    if not any(
        u.hostname.rstrip(".") in aliases
        and u.port == port
        and u.path == "/api/v1/write"
        for u in writes
    ):
        raise ValueError("Native VMAgent does not write to the selected local VMSingle")


def discover(args):
    import yaml

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", args.workspace):
        raise ValueError("Use one persistent lowercase workspace ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.target):
        raise ValueError("Invalid cluster target")
    if not args.kubeconfig.is_file() or not args.context.strip():
        raise ValueError("Explicit kubeconfig and context are required")
    connection = (args.kubeconfig.resolve(), args.context)
    generated = args.config.parent / "generated"
    manifest = json.loads(artifact(generated, "nebius-cxcli-manifest.json"))
    baseline = json.loads(artifact(generated, "reports/ordinary-apps-baseline.json"))
    if (
        manifest.get("schema") != "nebius-cxcli-generated/v2"
        or baseline.get("schema") != "nebius-cxcli-ordinary-apps/v1"
        or not baseline.get("deployment_generation")
    ):
        raise ValueError("Unsupported or unaccepted cxcli monitoring artifacts")
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                yaml.safe_load(args.config.read_text()),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    if manifest["render"]["source_config_sha256"] != digest:
        raise ValueError(
            "Configuration differs from the rendered monitoring installation"
        )
    target = one(
        [
            t
            for t in manifest["deploy"]["targets"]
            if t.get("target_ref") == args.target
        ],
        "generated deployment target",
    )
    flux = Path(target["flux_dir"])
    expected_flux = (generated / "flux/targets" / args.target).resolve()
    candidates = (
        [flux]
        if flux.is_absolute()
        else [base / flux for base in generated.resolve().parents]
    )
    if not any(p.resolve() == expected_flux for p in candidates):
        raise ValueError(
            "Generated target directory differs from the selected deployment"
        )
    identity = baseline["identities"][args.target]
    if (
        kube(connection, "get", "namespace", "kube-system")["metadata"]["uid"]
        != identity["kubernetes_uid"]
    ):
        raise ValueError("Kubernetes context is not the accepted course target")
    documents = accepted_documents(generated, baseline, args.target)
    grafana_row = app_row(manifest, args.target, "grafana")
    push_row = app_row(manifest, args.target, "prometheus-pushgateway")
    grafana = installed_release(connection, documents, grafana_row)
    push = installed_release(connection, documents, push_row)
    grafana_service, grafana_port = helm_service(connection, grafana_row, grafana)
    push_service, push_port = helm_service(connection, push_row, push)
    datasources = grafana["spec"]["values"]["datasources"]["datasources.yaml"][
        "datasources"
    ]
    datasource = one(
        [
            d
            for d in datasources
            if d.get("name") == "metrics-local" and d.get("type") == "prometheus"
        ],
        "installed local metrics datasource",
    )
    if (
        not datasource.get("uid")
        or datasource.get("basicAuth")
        or datasource.get("secureJsonData")
    ):
        raise ValueError(
            "Require an installed unauthenticated local metrics datasource"
        )
    url = datasource["url"]
    destination = private_url(url)
    if destination.path not in ("", "/"):
        raise ValueError("Local metrics datasource requires the root query endpoint")
    namespace, release, agent, _agents = native_monitoring(connection, documents)
    singles = kube(
        connection, "get", "vmsingles.operator.victoriametrics.com", "-n", namespace
    )["items"]
    single = one(
        [
            s
            for s in singles
            if s["metadata"].get("labels", {}).get("app.kubernetes.io/instance")
            == release
        ],
        "native VMSingle",
    )
    services = kube(connection, "get", "services", "-n", namespace)["items"]
    service = one(
        [
            s
            for s in services
            if destination.hostname.rstrip(".") in service_hosts(s)
            and any(
                o.get("uid") == single["metadata"]["uid"]
                for o in s["metadata"].get("ownerReferences", [])
            )
        ],
        "datasource's native VMSingle Service",
    )
    private_service(service)
    ready_endpoints(connection, service, destination.port)
    verify_metrics_write(agent, service, destination.port)
    address = verify_scrape(agent, push_service, push_port)
    return {
        "schema": SCHEMA,
        "target": args.target,
        "cluster_identity": identity,
        "deployment_generation": baseline["deployment_generation"],
        "source_config_sha256": digest,
        "vmagent_uid": agent["metadata"]["uid"],
        "vmagent_name": agent["metadata"]["name"],
        "vmagent_namespace": namespace,
        "vmsingle_uid": single["metadata"]["uid"],
        "service_uid": service["metadata"]["uid"],
        "metrics_url": url.rstrip("/"),
        "metrics_namespace": namespace,
        "metrics_service": service["metadata"]["name"],
        "metrics_port": destination.port,
        "pushgateway_namespace": push_service["metadata"]["namespace"],
        "pushgateway_service": push_service["metadata"]["name"],
        "pushgateway_service_uid": push_service["metadata"]["uid"],
        "pushgateway_port": push_port,
        "pushgateway_url": "http://" + address,
        "scrape_job": SCRAPE_JOB,
        "grafana_namespace": grafana_service["metadata"]["namespace"],
        "grafana_service": grafana_service["metadata"]["name"],
        "grafana_port": grafana_port,
        "datasource_name": datasource["name"],
        "datasource_uid": datasource["uid"],
    }


def environment(values):
    return "".join(
        f"export {key}={shlex.quote(str(value))}\n" for key, value in values.items()
    )


def write_connections(args, receipt):
    # Fresh private directories avoid mixing receipts or following existing output links.
    if (
        args.output_dir.exists()
        or args.output_dir.is_symlink()
        or any(p.is_symlink() for p in args.output_dir.parents)
    ):
        raise ValueError(
            "Setup output already exists or is unsafe; choose a fresh private directory"
        )
    values = {
        "CLUSTER_CONFIG": str(args.config.resolve()),
        "CLUSTER_TARGET": args.target,
        "KUBECONFIG": str(args.kubeconfig.resolve()),
        "CLUSTER_CONTEXT": args.context,
        "COURSE_SETUP_DIR": str(args.output_dir.resolve()),
        "COURSE_WORKSPACE": args.workspace,
        "COURSE_METRICS_URL": receipt["metrics_url"],
        "COURSE_PUSHGATEWAY": receipt["pushgateway_url"],
        "COURSE_DATASOURCE_UID": receipt["datasource_uid"],
        "COURSE_GRAFANA_NAMESPACE": receipt["grafana_namespace"],
        "COURSE_GRAFANA_SERVICE": receipt["grafana_service"],
        "COURSE_GRAFANA_PORT": receipt["grafana_port"],
    }
    outputs = {
        "monitoring.json": json.dumps(receipt, indent=2) + "\n",
        "laptop-environment.sh": environment(values),
        "environment.sh": environment(
            {
                k: values[k]
                for k in (
                    "COURSE_WORKSPACE",
                    "COURSE_METRICS_URL",
                    "COURSE_PUSHGATEWAY",
                )
            }
        ),
    }
    args.output_dir.mkdir(mode=0o700, parents=True)
    for name, content in outputs.items():
        with (args.output_dir / name).open("x") as stream:
            os.chmod(stream.name, 0o600)
            stream.write(content)


def open_directory(path):
    """Open every component without following links, including source ancestors."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_directory(info, label):
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError(f"Use an owned mode-700 directory, without symlinks: {label}")


def course_labs(root):
    """Read the course-owned lab inventory, never glob runtime output paths."""
    course_fd = open_directory(root)
    try:
        reference_fd = os.open(
            "reference",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=course_fd,
        )
        try:
            fd = os.open(
                "course.json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=reference_fd,
            )
            with os.fdopen(fd, "r") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Course metadata must be a regular file")
                metadata = json.load(stream)
        finally:
            os.close(reference_fd)
        if not isinstance(metadata, dict):
            raise TypeError("Course metadata must be an object")
        if metadata.get("profile") in ("text-only", "reference-only"):
            return []
        labs = metadata.get("labs")
        if not isinstance(labs, list) or not labs:
            raise ValueError(f"Missing practical lab inventory: {root}")
        identities = []
        for row in labs:
            source = row.get("path") if isinstance(row, dict) else None
            if not isinstance(source, str) or not re.fullmatch(
                r"labs/[0-9]{2}_[a-z0-9_]+\.(py|cu)", source
            ):
                raise ValueError(f"Unsafe lab source identity in {root}")
            lab = PurePosixPath(source).stem
            if lab in identities:
                raise ValueError(f"Duplicate lab identity: {lab}")
            identities.append(lab)
        return identities
    finally:
        os.close(course_fd)


def prepare(courses_root=None, course_root=None):
    """Validate first, then create private directories with no-follow dir handles."""
    selected = Path(course_root if course_root is not None else courses_root).absolute()
    selected_fd = open_directory(selected)
    os.close(selected_fd)
    roots = (
        [selected]
        if course_root is not None
        else sorted(
            p
            for p in selected.iterdir()
            if (p / "reference/course.json").exists()
            or (p / "reference/course.json").is_symlink()
        )
    )
    inventory = [(root, course_labs(root)) for root in roots]
    inventory = [(root, labs) for root, labs in inventory if labs]
    if not inventory:
        raise ValueError("No practical courses found at the selected root")
    paths = [(selected, ".runtime"), (selected, ".profiling-tools")]
    paths += [
        (root, relative)
        for root, labs in inventory
        for relative in [
            "results",
            *(
                name
                for lab in labs
                for name in (
                    f"results/{lab}",
                    f"results/{lab}/logs",
                    f"results/{lab}/jobs",
                )
            ),
        ]
    ]
    # Do not partially prepare a catalog when a known existing path is unsafe.
    for root, relative in paths:
        path = root / relative
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        private_directory(info, path)
    for root, relative in paths:
        parent_fd = open_directory(root)
        try:
            for part in PurePosixPath(relative).parts:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                )
                os.close(parent_fd)
                parent_fd = child_fd
                private_directory(os.fstat(parent_fd), root / relative)
        finally:
            os.close(parent_fd)
    return {root.name: len(labs) for root, labs in inventory}


def monitoring(args):
    import yaml

    os.umask(0o077)
    try:
        write_connections(args, discover(args))
    except yaml.YAMLError:
        raise SystemExit(
            "Monitoring discovery not ready: invalid YAML in a configuration or monitoring artifact"
        ) from None
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        raise SystemExit(f"Monitoring discovery not ready: {exc}") from exc
    print(
        "Private course connection files created. Source laptop-environment.sh, then verify monitoring and import dashboards."
    )


def history_digest(source, destination=None):
    """Hash a report with bounded memory, optionally writing its verified copy."""
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
    return digest.hexdigest()


def organize_history(course_root):
    """Copy attributable runtime history; never move or modify original evidence."""
    root = Path(course_root).absolute()
    labs = set(course_labs(root))
    results = root / "results"
    if not results.exists():
        return {"copied": [], "unresolved": []}
    os.close(open_directory(results))
    entries, unresolved = [], []
    for source in sorted(results.rglob("*")):
        relative = source.relative_to(results)
        if any(part in {"history", "jobs"} for part in relative.parts):
            continue
        if source.is_symlink():
            raise ValueError("Historical evidence must not contain symlinks")
        if not source.is_file():
            continue
        lab = relative.parts[0] if relative.parts[0] in labs else None
        job = None
        metadata_digest = None
        # Attribution never needs to load a native report into memory. Large
        # JSON files use filename identity or remain unresolved, like binaries.
        metadata_limit = 8 * 1024 * 1024
        if source.suffix == ".json" and source.stat().st_size <= metadata_limit:
            try:
                with source.open("rb") as stream:
                    content = stream.read(metadata_limit + 1)
                if len(content) > metadata_limit:
                    raise ValueError("Historical metadata grew beyond its limit")
                metadata_digest = hashlib.sha256(content).hexdigest()
                data = json.loads(content)
                if isinstance(data, dict):
                    identity = data.get("lab_id", data.get("lab"))
                    if identity in labs:
                        lab = identity
                    job = data.get("experiment", {}).get("slurm_job_id")
            except (ValueError, AttributeError):
                pass
        if job is None:
            match = re.fullmatch(
                r"([1-9][0-9]*)\.(?:out|err)", source.name
            ) or re.match(r"job-([1-9][0-9]*)-", source.name)
            if match:
                job = int(match[1])
        if lab not in labs or type(job) is not int or job < 1:
            unresolved.append(relative.as_posix())
            continue
        destination = results / "history" / lab / str(job) / relative
        current = results
        for part in destination.parent.relative_to(results).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("History destination must not follow symlinks")
            current.mkdir(mode=0o700, exist_ok=True)
            private_directory(current.stat(), current)
        if destination.is_symlink():
            raise ValueError("History destination must not follow symlinks")
        if destination.exists():
            sha = history_digest(source)
            if not destination.is_file() or history_digest(destination) != sha:
                raise ValueError("Historical copy collision; originals were preserved")
        else:
            with os.fdopen(
                os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                sha = history_digest(source, stream)
                stream.flush()
                os.fsync(stream.fileno())
        if (
            (metadata_digest is not None and sha != metadata_digest)
            or history_digest(source) != sha
            or history_digest(destination) != sha
        ):
            raise ValueError(
                "Evidence changed during organization; inspect copies without removing originals"
            )
        entries.append(
            {
                "source": relative.as_posix(),
                "copy": destination.relative_to(results).as_posix(),
                "sha256": sha,
            }
        )
    manifest = {
        "schema": "course-history/v1",
        "copied": entries,
        "unresolved": unresolved,
    }
    if entries:
        content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        target = (
            results
            / "history"
            / ("organization-" + hashlib.sha256(content).hexdigest()[:16] + ".json")
        )
        if target.exists():
            if target.is_symlink() or target.read_bytes() != content:
                raise ValueError("Historical organization manifest collision")
        else:
            with os.fdopen(
                os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as stream:
                stream.write(content)
    return manifest
