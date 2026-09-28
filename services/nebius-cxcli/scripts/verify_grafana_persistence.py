#!/usr/bin/env python3
"""Disposable Docker proof of shared DB, session and dashboard persistence.

This is a database/API trial, not Kubernetes PVC/NetworkPolicy or browser UI proof.
Credentials stay in memory and container environments; no replay occurs during
restart observations. All resources use an isolated randomly named fixture.
"""

from __future__ import annotations

import http.cookiejar
import json
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from verify_grafana_api import IMAGE

from nebius_cxcli.grafana_api import GrafanaClient, basic_auth
from nebius_cxcli.grafana_import import execute_imports, prepare_imports, require_complete

POSTGRES = "postgres:18.6@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae"


def docker(*args: str, stdin: str | None = None, timeout: float = 180) -> str:
    result = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(f"Disposable Docker {args[0]} failed (output withheld)")
    return result.stdout.strip()


def postgres_ready(name: str, password: str) -> None:
    """Wait for authenticated TCP queries, independent of Grafana's health cache."""
    deadline = time.monotonic() + 90
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            result = docker(
                "exec",
                "--env-file",
                "/dev/stdin",
                name,
                "psql",
                "-w",
                "-h",
                "127.0.0.1",
                "-U",
                "grafana",
                "-d",
                "grafana",
                "-Atqc",
                "SELECT 1",
                stdin=f"PGPASSWORD={password}\n",
                timeout=min(5, remaining),
            )
            if result == "1":
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    raise RuntimeError("PostgreSQL authenticated readiness timed out")


def ready(url: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                if json.load(response).get("database") == "ok":
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Disposable Grafana database readiness timed out")


def main() -> None:
    prefix = "cxcli-grafana-db-" + uuid.uuid4().hex[:12]
    network, volume, pg = prefix + "-net", prefix + "-data", prefix + "-pg"
    gf = [prefix + "-a", prefix + "-b"]
    admin, password, encryption, pg_admin = (secrets.token_urlsafe(32) for _ in range(4))
    containers: list[str] = []
    network_created = volume_created = False
    with tempfile.TemporaryDirectory(prefix="cxcli-grafana-persistence-") as directory:
        script = Path(directory) / "init.sql"
        script.write_text(
            "\\getenv app_password CUSTOM_PASSWORD\n"
            "CREATE ROLE grafana LOGIN PASSWORD :'app_password';\n"
            "CREATE DATABASE grafana OWNER grafana;\n"
        )
        try:
            docker("network", "create", network)
            network_created = True
            docker("volume", "create", volume)
            volume_created = True
            docker(
                "run",
                "--detach",
                "--name",
                pg,
                "--network",
                network,
                "--volume",
                f"{volume}:/var/lib/postgresql",
                "--volume",
                f"{script}:/docker-entrypoint-initdb.d/01-app.sql:ro",
                "--env-file",
                "/dev/stdin",
                POSTGRES,
                "-c",
                "password_encryption=scram-sha-256",
                stdin=f"POSTGRES_USER=postgres\nPOSTGRES_PASSWORD={pg_admin}\nCUSTOM_PASSWORD={password}\nPOSTGRES_INITDB_ARGS=--auth-host=scram-sha-256\n",
            )
            containers.append(pg)
            postgres_ready(pg, password)
            urls = []
            for name in gf:
                docker(
                    "run",
                    "--detach",
                    "--name",
                    name,
                    "--network",
                    network,
                    "--publish",
                    "127.0.0.1::3000",
                    "--env-file",
                    "/dev/stdin",
                    IMAGE,
                    stdin=f"GF_DATABASE_TYPE=postgres\nGF_DATABASE_HOST={pg}:5432\nGF_DATABASE_NAME=grafana\nGF_DATABASE_USER=grafana\nGF_DATABASE_PASSWORD={password}\nGF_DATABASE_SSL_MODE=disable\nGF_SECURITY_ADMIN_PASSWORD={admin}\nGF_SECURITY_SECRET_KEY={encryption}\nGF_ANALYTICS_REPORTING_ENABLED=false\nGF_ANALYTICS_CHECK_FOR_UPDATES=false\n",
                )
                containers.append(name)
                endpoint = docker("port", name, "3000/tcp")
                if not endpoint.startswith("127.0.0.1:"):
                    raise RuntimeError("Fixture must remain loopback-only")
                urls.append(f"http://{endpoint}/")
                ready(urls[-1])
            clients = [GrafanaClient(url, basic_auth("admin", admin)) for url in urls]
            for client in clients:
                client.connect()
            source = {
                "uid": "persistence",
                "title": "Imported source",
                "schemaVersion": 42,
                "editable": False,
                "panels": [],
            }
            require_complete(execute_imports(clients[0], prepare_imports(clients[0], [source])))
            original = clients[1].get("persistence")
            assert original is not None and original["spec"]["editable"] is True
            edited = clients[1].write(
                {**clients[1].portable(original), "title": "Saved user edit"}, "", original
            )
            assert edited is not None
            jar = http.cookiejar.CookieJar()
            session = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            request = urllib.request.Request(
                urls[0] + "login",
                data=json.dumps({"user": "admin", "password": admin}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with session.open(request, timeout=10) as response:
                assert response.status == 200

            def observe() -> None:
                for name in gf:
                    # Docker may allocate a new host port on stop/start. Reopening
                    # the observation transport does not change product state.
                    endpoint = docker("port", name, "3000/tcp")
                    if not endpoint.startswith("127.0.0.1:"):
                        raise RuntimeError("Fixture must remain loopback-only")
                    url = f"http://{endpoint}/"
                    client = GrafanaClient(url, basic_auth("admin", admin))
                    ready(url)
                    client.connect()
                    saved = client.get("persistence")
                    assert saved is not None and saved["spec"]["title"] == "Saved user edit"
                    assert saved["spec"]["editable"] is True
                    with session.open(url + "api/user", timeout=10) as response:
                        assert json.load(response)["login"] == "admin"

            observe()
            # No imports, replay or database writes are used to repair these observations.
            for name in gf:
                print("Checking Grafana restart persistence", flush=True)
                docker("restart", name)
                observe()
            print("Checking PostgreSQL restart persistence", flush=True)
            docker("restart", pg)
            postgres_ready(pg, password)
            observe()
            # Recreate PostgreSQL against the same persistent volume, not just the process.
            print("Checking PostgreSQL volume reuse", flush=True)
            docker("stop", pg)
            docker("rm", pg)
            containers.remove(pg)
            docker(
                "run",
                "--detach",
                "--name",
                pg,
                "--network",
                network,
                "--volume",
                f"{volume}:/var/lib/postgresql",
                POSTGRES,
            )
            containers.append(pg)
            postgres_ready(pg, password)
            observe()
            print(
                "Verified two Grafana instances, shared sessions, editable API dashboards, Grafana restarts and PostgreSQL volume reuse without replay."
            )
        except Exception:
            for name in containers:
                result = subprocess.run(
                    ["docker", "logs", "--tail", "18", name], capture_output=True, text=True
                )
                detail = result.stdout + result.stderr
                for value in (admin, password, encryption, pg_admin):
                    detail = detail.replace(value, "[REDACTED]")
                print(f"Fixture diagnostics ({name}): {detail}", flush=True)
            raise
        finally:
            for name in reversed(containers):
                docker("rm", "--force", name)
            if volume_created:
                docker("volume", "rm", volume)
            if network_created:
                docker("network", "rm", network)


if __name__ == "__main__":
    main()
