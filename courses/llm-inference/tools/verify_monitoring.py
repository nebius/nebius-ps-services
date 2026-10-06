#!/usr/bin/env python3
"""Verify the deployed results scrape and GPU telemetry through private forwards."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from course_bootstrap.support import (
    SCHEMA,
    SCRAPE_JOB,
    kube,
    kube_command,
    one,
    private_service,
    ready_endpoints,
    service_hosts,
    verify_metrics_write,
    verify_scrape,
)


@contextmanager
def forwarded(context, namespace, service, port):
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            kube_command(
                context,
                "-n",
                namespace,
                "port-forward",
                "--address",
                "127.0.0.1",
                "service/" + service,
                ":" + str(port),
            ),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Private monitoring forward exited")
                output.seek(0)
                match = re.search(
                    rb"Forwarding from 127\.0\.0\.1:(\d+) ->", output.read(65536)
                )
                if match:
                    yield "http://127.0.0.1:" + match[1].decode()
                    return
                time.sleep(0.1)
            raise RuntimeError("Private monitoring forward timed out")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read(4 * 1024 * 1024))


def verify_targets(targets, endpoints, now, *, receipt, service):
    hosts = service_hosts(service)
    addresses = endpoints | {f"{h}:{receipt['pushgateway_port']}" for h in hosts}
    for ip in service["spec"].get("clusterIPs", []):
        if ip != "None":
            addresses.add(
                f"[{ip}]:{receipt['pushgateway_port']}"
                if ":" in ip
                else f"{ip}:{receipt['pushgateway_port']}"
            )
    matches = []
    for target in targets:
        labels = target.get("discoveredLabels", {})
        url = urllib.parse.urlsplit(target.get("scrapeUrl", ""))
        if (
            url.netloc in addresses
            or target.get("scrapePool") == receipt["scrape_job"]
            or (
                labels.get("__meta_kubernetes_namespace")
                == receipt["pushgateway_namespace"]
                and labels.get("__meta_kubernetes_service_name")
                == receipt["pushgateway_service"]
            )
        ):
            matches.append(target)
    target = one(matches, "active results scrape route across all agents")
    destination = urllib.parse.urlsplit(target.get("scrapeUrl", ""))
    if (
        destination.netloc not in addresses
        or destination.path != "/metrics"
        or destination.scheme != "http"
        or destination.query
        or destination.fragment
        or destination.username
        or destination.password
    ):
        raise ValueError(
            "Results scrape destination differs from the ready Pushgateway Service"
        )
    if target.get("_course_agent_uid") != receipt["vmagent_uid"]:
        raise ValueError("Results scrape is owned by another VMAgent")
    if (
        target.get("scrapePool") != receipt["scrape_job"]
        or target.get("health") != "up"
        or target.get("lastError")
    ):
        raise ValueError(
            "Results scrape must be healthy under the native cxcli scrape pool"
        )
    stamp = datetime.fromisoformat(
        target["lastScrape"].replace("Z", "+00:00")
    ).timestamp()
    if not 0 <= now - stamp <= 30:
        raise ValueError("Results scrape is stale")


def verify_backend(receipt, services, singles):
    one(
        [s for s in singles if s["metadata"]["uid"] == receipt["vmsingle_uid"]],
        "accepted VMSingle identity",
    )
    service = one(
        [s for s in services if s["metadata"]["name"] == receipt["metrics_service"]],
        "accepted metrics Service",
    )
    if service["metadata"]["uid"] != receipt["service_uid"] or not any(
        owner.get("uid") == receipt["vmsingle_uid"]
        for owner in service["metadata"].get("ownerReferences", [])
    ):
        raise ValueError("Metrics backend Service identity or ownership changed")
    private_service(service)
    if not any(
        p.get("name") == "http" and p.get("port") == receipt["metrics_port"]
        for p in service["spec"].get("ports", [])
    ):
        raise ValueError("Metrics backend must retain its private HTTP port")
    return service


def expected_gpu_inventory(metadata, nodes):
    if (
        metadata.get("slug") == "advanced-gpu-communication"
        and metadata.get("profile") == "labs-only"
    ):
        capacities = {8}
    elif metadata.get("slug") in {
        "gpu-fundamentals",
        "gpu-optimizations",
        "llm-training",
        "llm-inference",
        "custom-cuda-kernels",
    }:
        capacities = {1, 8}
    else:
        raise ValueError("Unknown course hardware contract")
    inventory = {}
    for node in nodes:
        capacity = node.get("status", {}).get("capacity", {})
        count = int(capacity.get("nvidia.com/gpu", "0"))
        if not count:
            continue
        if count not in capacities or not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in node["status"].get("conditions", [])
        ):
            raise ValueError("Require ready GPU workers with the course GPU capacity")
        inventory[node["metadata"]["name"]] = count
    if len(inventory) != 2 or len(set(inventory.values())) != 1:
        raise ValueError(
            "Require two equally sized GPU workers in the accepted cluster"
        )
    return inventory


def verify_gpu_inventory(payload, inventory):
    values = payload.get("data", {}).get("result", [])
    expected = {
        (node, str(index))
        for node, count in inventory.items()
        for index in range(count)
    }
    observed = {
        (row.get("metric", {}).get("Hostname"), row.get("metric", {}).get("gpu"))
        for row in values
    }
    if (
        payload.get("status") != "success"
        or observed != expected
        or len(values) != len(expected)
    ):
        raise ValueError(
            f"Require fresh DCGM telemetry for exactly {len(expected)} GPUs matching the accepted cluster worker/index inventory"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--context", required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    if receipt.get("schema") != SCHEMA or receipt.get("scrape_job") != SCRAPE_JOB:
        parser.error("Unsupported monitoring receipt; rerun course discovery")
    context = (args.kubeconfig.resolve(), args.context)
    if (
        kube(context, "get", "namespace", "kube-system")["metadata"]["uid"]
        != receipt["cluster_identity"]["kubernetes_uid"]
    ):
        parser.error("Explicit context differs from the accepted course cluster")
    pushgateway = kube(
        context,
        "get",
        "service",
        receipt["pushgateway_service"],
        "-n",
        receipt["pushgateway_namespace"],
    )
    if pushgateway["metadata"]["uid"] != receipt["pushgateway_service_uid"]:
        parser.error("Discovered Pushgateway Service was replaced; rerun discovery")
    private_service(pushgateway)
    addresses = ready_endpoints(context, pushgateway, receipt["pushgateway_port"])
    agents = kube(context, "get", "vmagents.operator.victoriametrics.com", "-A")[
        "items"
    ]
    selected = one(
        [a for a in agents if a["metadata"]["uid"] == receipt["vmagent_uid"]],
        "discovered native VMAgent",
    )
    verify_scrape(selected, pushgateway, receipt["pushgateway_port"])
    targets = []
    for agent in agents:
        namespace = agent["metadata"]["namespace"]
        services = kube(context, "get", "services", "-n", namespace)["items"]
        service = one(
            [
                s
                for s in services
                if any(
                    o.get("uid") == agent["metadata"]["uid"]
                    for o in s["metadata"].get("ownerReferences", [])
                )
            ],
            "VMAgent-owned Service",
        )
        slices = kube(
            context,
            "get",
            "endpointslices",
            "-n",
            namespace,
            "-l",
            "kubernetes.io/service-name=" + service["metadata"]["name"],
        )["items"]
        ready = {
            address
            for item in slices
            for endpoint in item["endpoints"]
            if endpoint.get("conditions", {}).get("ready")
            for address in endpoint["addresses"]
        }
        if len(ready) != 1:
            parser.error("Require one actual ready scraper replica per agent")
        port = one(
            [p["port"] for p in service["spec"]["ports"] if p["name"] == "http"],
            "VMAgent HTTP port",
        )
        with forwarded(context, namespace, service["metadata"]["name"], port) as url:
            targets.extend(
                {**target, "_course_agent_uid": agent["metadata"]["uid"]}
                for target in get(url + "/api/v1/targets")["data"]["activeTargets"]
            )
    verify_targets(
        targets, addresses, time.time(), receipt=receipt, service=pushgateway
    )
    backend = verify_backend(
        receipt,
        kube(context, "get", "services", "-n", receipt["metrics_namespace"])["items"],
        kube(
            context,
            "get",
            "vmsingles.operator.victoriametrics.com",
            "-n",
            receipt["metrics_namespace"],
        )["items"],
    )
    verify_metrics_write(selected, backend, receipt["metrics_port"])
    with forwarded(
        context,
        receipt["metrics_namespace"],
        receipt["metrics_service"],
        receipt["metrics_port"],
    ) as url:
        metric = 'DCGM_FI_DEV_GPU_UTIL{Hostname!="",gpu!=""}'
        expression = (
            f"count by(Hostname,gpu) ({metric} and (timestamp({metric}) > time() - 60))"
        )
        payload = get(
            url + "/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        )
        metadata = json.loads(
            (Path(__file__).resolve().parents[1] / "reference/course.json").read_text()
        )
        inventory = expected_gpu_inventory(
            metadata, kube(context, "get", "nodes")["items"]
        )
        verify_gpu_inventory(payload, inventory)
        expected = sum(inventory.values())
    print(
        f"Monitoring readiness: one healthy results scrape and {expected} fresh GPU series. Publish both worker canaries next."
    )


if __name__ == "__main__":
    main()
