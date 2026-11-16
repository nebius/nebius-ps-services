"""Bounded GitHub adapter; no candidate-provided URLs or shell commands."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from urllib.parse import quote

import httpx

from .contracts import Blocked, Candidate, Producer


def changed_paths(files: list[dict]) -> list[str]:
    """Both sides of a rename affect governance, CI and package inputs."""
    return sorted(
        {
            path
            for file in files
            for path in (file["filename"], file.get("previous_filename"))
            if path
        }
    )


class GitHub:
    def __init__(self, repository: str, token: str, client=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise Blocked("GITHUB_NOT_CONFIGURED")
        self.repository = repository
        self.client = client or httpx.Client(
            base_url="https://api.github.com",
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def graphql(self, query, variables):
        """Read-only GraphQL discovery; no caller-supplied endpoint or mutation."""
        if not query.lstrip().startswith("query "):
            raise Blocked("GITHUB_QUERY_REQUIRED")
        try:
            response = self.client.post("/graphql", json={"query": query, "variables": variables})
            value = response.json()
            if response.status_code != 200 or value.get("errors") or not value.get("data"):
                raise Blocked("GITHUB_READ_UNAVAILABLE")
            return value["data"]
        except (httpx.HTTPError, ValueError):
            raise Blocked("GITHUB_READ_UNAVAILABLE") from None

    def request(self, path: str, *, method="GET", body=None, absent=False):
        try:
            endpoint = f"/repos/{self.repository}" + ("/" + path if path else "")
            response = self.client.request(method, endpoint, json=body)
            if absent and response.status_code == 404:
                return None
            if response.status_code not in {200, 201, 202, 204}:
                if method != "GET" and response.status_code in {
                    400,
                    401,
                    403,
                    404,
                    405,
                    409,
                    410,
                    422,
                    429,
                }:
                    raise Blocked("GITHUB_WRITE_NOT_SUBMITTED")
                if method != "GET":
                    raise Blocked("GITHUB_OUTCOME_UNKNOWN")
                raise Blocked("GITHUB_REQUEST_REJECTED")
            return response.json() if response.content else None
        except httpx.HTTPError:
            raise Blocked(
                "GITHUB_OUTCOME_UNKNOWN" if method != "GET" else "GITHUB_READ_UNAVAILABLE"
            ) from None

    def pages(self, path: str, *, field: str | None = None):
        result = []
        for page in range(1, 101):
            value = self.request(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            chunk = value[field] if field else value
            if not isinstance(chunk, list):
                raise Blocked("GITHUB_RESPONSE_INVALID")
            result.extend(chunk)
            if len(chunk) < 100:
                return result
        raise Blocked("GITHUB_PAGINATION_LIMIT")

    def candidate(self, number: int):
        if type(number) is not int or number <= 0:
            raise Blocked("INVALID_PR_NUMBER")
        pr = self.request(f"pulls/{number}")
        repo = self.request("")
        if pr["base"]["repo"]["id"] != repo["id"] or pr["base"]["ref"] != repo["default_branch"]:
            raise Blocked("PR_TARGET_UNSUPPORTED")
        if not pr["head"].get("repo"):
            raise Blocked("PR_SOURCE_UNAVAILABLE")
        commit = self.request(f"git/commits/{pr['head']['sha']}")
        candidate = Candidate(
            repository=self.repository,
            repository_id=repo["id"],
            head_repository=pr["head"]["repo"]["full_name"],
            head_repository_id=pr["head"]["repo"]["id"],
            head_ref=pr["head"]["ref"],
            pr_id=pr["id"],
            pr_number=number,
            head_sha=pr["head"]["sha"],
            base_sha=pr["base"]["sha"],
            tree_sha=commit["tree"]["sha"],
        )
        return candidate, pr

    def paths(self, candidate: Candidate):
        files = self.pages(f"pulls/{candidate.pr_number}/files")
        current, pr = self.candidate(candidate.pr_number)
        if current != candidate or len(files) != pr["changed_files"]:
            raise Blocked("PR_SNAPSHOT_CHANGED")
        return changed_paths(files)

    def permission(self, login: str):
        value = self.request(f"collaborators/{quote(login, safe='')}/permission")
        # GitHub's legacy permission field collapses Maintain into Write.
        # Preserve that distinction without elevating ordinary/custom Write roles.
        if value["permission"] == "write" and value.get("role_name") == "maintain":
            return "maintain"
        return value["permission"]

    def producer(self, producer: Producer, *, completed=True):
        run = self.request(f"actions/runs/{producer.run_id}/attempts/{producer.attempt}")
        repo = self.request("")
        latest = self.request(f"actions/runs/{producer.run_id}")
        if (
            run["repository"]["full_name"] != self.repository
            or run["path"] != producer.workflow
            or run["event"] != "workflow_dispatch"
            or run["head_branch"] != repo["default_branch"]
            or run["head_sha"] != producer.workflow_sha
            or latest["run_attempt"] != producer.attempt
            or (completed and (run["status"] != "completed" or run["conclusion"] != "success"))
        ):
            raise Blocked("PRODUCER_UNTRUSTED")
        return run

    def artifact(self, run_id: int, name: str, filename: str) -> dict:
        artifacts = [
            a
            for a in self.pages(f"actions/runs/{run_id}/artifacts", field="artifacts")
            if a["name"] == name
        ]
        if len(artifacts) != 1 or artifacts[0]["expired"]:
            raise Blocked("ARTIFACT_UNAVAILABLE")
        artifact = artifacts[0]
        if artifact["workflow_run"]["id"] != run_id or artifact["size_in_bytes"] > 1_048_576:
            raise Blocked("ARTIFACT_INVALID")
        response = self.client.get(
            f"/repos/{self.repository}/actions/artifacts/{artifact['id']}/zip",
            follow_redirects=False,
        )
        if response.status_code == 302:
            # Never forward the GitHub token to the artifact host.
            url = response.headers["location"]
            if not url.startswith("https://"):
                raise Blocked("ARTIFACT_URL_INVALID")
            response = httpx.get(url, timeout=30, follow_redirects=False)
        if response.status_code != 200 or len(response.content) > 1_048_576:
            raise Blocked("ARTIFACT_DOWNLOAD_FAILED")
        if (
            artifact.get("digest")
            and artifact["digest"] != "sha256:" + hashlib.sha256(response.content).hexdigest()
        ):
            raise Blocked("ARTIFACT_DIGEST_MISMATCH")
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                if (
                    archive.namelist() != [filename]
                    or archive.getinfo(filename).file_size > 1_048_576
                ):
                    raise Blocked("ARTIFACT_CONTENT_INVALID")
                return json.loads(archive.read(filename))
        except (ValueError, zipfile.BadZipFile, KeyError):
            raise Blocked("ARTIFACT_CONTENT_INVALID") from None

    def dispatch(self, workflow: str, inputs: dict):
        if not re.fullmatch(r"[a-z0-9-]+\.yml", workflow):
            raise Blocked("INVALID_WORKFLOW")
        default = self.request("")["default_branch"]
        self.request(
            f"actions/workflows/{workflow}/dispatches",
            method="POST",
            body={"ref": default, "inputs": inputs},
        )

    def mark_fork(self, candidate):
        if not candidate.fork:
            return
        label = self.request("labels/source%3Afork", absent=True)
        if not label:
            self.request(
                "labels",
                method="POST",
                body={
                    "name": "source:fork",
                    "color": "d4c5f9",
                    "description": "Contribution from a fork repository",
                },
            )
        labels = self.pages(f"issues/{candidate.pr_number}/labels")
        if not any(row["name"] == "source:fork" for row in labels):
            self.request(
                f"issues/{candidate.pr_number}/labels",
                method="POST",
                body={"labels": ["source:fork"]},
            )

    def comment(self, number: int, code: str, *, detail=""):
        # Never include raw provider errors, model output or cloud target metadata.
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", code):
            raise Blocked("INVALID_PUBLIC_RESULT")
        pr = self.request(f"pulls/{number}")
        source = pr["head"].get("repo")
        origin = ""
        if source and source["id"] != pr["base"]["repo"]["id"]:
            from .requests import fork_merging

            policy = (
                "Eligible for automated merge only after every required gate passes. "
                if fork_merging()
                else "Automatic fork approval and merging are disabled. "
            )
            origin = (
                "**Fork contribution:** This PR comes from a fork. "
                + policy
                + "Required branch changes must be applied by its contributor.\n\n"
            )
        body = f"<!-- github-ai-automation -->\n{origin}Automation result: **{code}**.\n{detail}"
        comments = self.pages(f"issues/{number}/comments")
        existing = [
            c
            for c in comments
            if c["user"].get("id") == 41898282
            and c["body"].startswith("<!-- github-ai-automation -->")
        ]
        if existing:
            posted = self.request(
                f"issues/comments/{existing[-1]['id']}", method="PATCH", body={"body": body}
            )
        else:
            posted = self.request(f"issues/{number}/comments", method="POST", body={"body": body})
        observed = self.request(f"issues/comments/{posted['id']}")
        if observed.get("body") != body or observed.get("user", {}).get("id") != 41898282:
            raise Blocked("COMMENT_NOT_VERIFIED")
