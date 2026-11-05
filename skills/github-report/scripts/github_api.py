"""Fixed read-only GitHub queries through existing gh authentication."""

from __future__ import annotations

from datetime import datetime
import json
import os
import re
import subprocess
from urllib.parse import urlsplit

from report_model import (
    ReportError,
    UTC,
    evidence_text,
    iso,
    repositories,
    timestamp,
    valid_git_path,
)

ACTOR = "__typename login ... on User { id databaseId name }"
IDENTITY = "id number url title state repository { nameWithOwner }"
COMMON = IDENTITY + " body createdAt updatedAt closedAt author { " + ACTOR + " }"
DISCOVERY = IDENTITY + " createdAt updatedAt"
ISSUE = COMMON + " stateReason"
PR = (
    COMMON + " isDraft mergedAt additions deletions changedFiles commits { totalCount }"
)
PR_REF = IDENTITY + " mergedAt"
TIMELINE = """
__typename
... on ClosedEvent { id createdAt actor { ACTOR } stateReason
  closer { __typename ... on PullRequest { PR_REF } ... on Commit { id url } } }
... on ReopenedEvent { id createdAt actor { ACTOR } }
... on LabeledEvent { id createdAt actor { ACTOR } label { name } }
... on UnlabeledEvent { id createdAt actor { ACTOR } label { name } }
... on CrossReferencedEvent { id createdAt actor { ACTOR }
  source { __typename ... on PullRequest { PR_REF } } }
""".replace("ACTOR", ACTOR).replace("PR_REF", PR_REF)
PAGE = "pageInfo { hasNextPage endCursor } totalCount"


def run_read(
    args: list[str], payload: str | None = None, cwd=None, *, graphql=False
) -> str:
    env = os.environ.copy()
    env.pop("GH_DEBUG", None)
    env.pop("GH_FORCE_TTY", None)
    env.update(GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0", GH_PAGER="cat")
    try:
        result = subprocess.run(
            args,
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=45,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError:
        raise ReportError(
            "TOOL_UNAVAILABLE", "A required existing tool (git or gh) is unavailable."
        ) from None
    except subprocess.TimeoutExpired:
        raise ReportError(
            "TIMEOUT", "A read request timed out; coverage is incomplete."
        ) from None
    if result.returncode:
        # gh exits nonzero for GraphQL errors even when stdout has a valid
        # typed error envelope. Let the query boundary classify that envelope;
        # never treat a nonzero response containing only data as a success.
        if graphql:
            try:
                errors = json.loads(result.stdout).get("errors")
                if (
                    isinstance(errors, list)
                    and errors
                    and all(isinstance(error, dict) for error in errors)
                ):
                    return result.stdout
            except (ValueError, TypeError, AttributeError):
                pass
        # Never print upstream stderr, which can contain credentials or private URLs.
        error = result.stderr.lower()
        code = (
            "RATE_LIMIT"
            if "rate limit" in error
            else "INACCESSIBLE"
            if any(
                word in error
                for word in ("http 403", "http 404", "forbidden", "not found")
            )
            else "READ_FAILED"
        )
        raise ReportError(
            code,
            "Read failed; check existing tool authentication and repository access.",
        )
    return result.stdout


def remote_repository(remote: str) -> str | None:
    if remote.startswith("git@github.com:"):
        value = remote[len("git@github.com:") :]
    else:
        parsed = urlsplit(remote)
        if (
            parsed.hostname != "github.com"
            or parsed.scheme not in {"https", "ssh"}
            or parsed.password
            or (
                parsed.username
                and not (parsed.scheme == "ssh" and parsed.username == "git")
            )
            or parsed.query
            or parsed.fragment
            or parsed.port
        ):
            return None
        value = parsed.path.lstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    try:
        return repositories([value])[0]
    except ReportError:
        return None


def checkout_repository() -> str:
    try:
        try:
            root = run_read(["git", "rev-parse", "--show-toplevel"]).strip()
        except ReportError as exc:
            if exc.code != "READ_FAILED":
                raise
            raise ReportError(
                "NO_CHECKOUT",
                "Supply --repo: the current directory is not a readable Git checkout.",
            ) from None
        try:
            lines = run_read(
                ["git", "config", "--get-regexp", r"^remote\..*\.url$"], cwd=root
            ).splitlines()
        except ReportError as exc:
            if exc.code != "READ_FAILED":
                raise
            raise ReportError(
                "AMBIGUOUS_REPO",
                "Supply --repo: checkout remotes could not be resolved.",
            ) from None
        targets = set()
        for line in lines:
            parts = line.split(None, 1)
            target = remote_repository(parts[1]) if len(parts) == 2 else None
            if target is None:
                raise ReportError(
                    "AMBIGUOUS_REPO",
                    "Supply --repo: the checkout has unsupported or ambiguous remotes.",
                )
            targets.add(target.lower())
        if len(targets) != 1:
            raise ReportError(
                "AMBIGUOUS_REPO",
                "Supply --repo to select among the checkout's repositories.",
            )
        effective = json.loads(
            run_read(["gh", "repo", "view", "--json", "nameWithOwner,url"], cwd=root)
        )
        name = effective.get("nameWithOwner", "")
        if (
            name.lower() not in targets
            or remote_repository(effective.get("url", "")) != name
        ):
            raise ReportError(
                "AMBIGUOUS_REPO",
                "Supply --repo: GitHub CLI defaults disagree with checkout remotes.",
            )
        return name
    except (json.JSONDecodeError, ValueError):
        raise ReportError(
            "INVALID_RESPONSE", "Could not verify the checkout's GitHub repository."
        ) from None


class GitHub:
    def __init__(self, max_requests: int = 2000):
        self.requests = 0
        self.max_requests = max_requests

    def query(self, query: str, variables: dict | None = None) -> dict:
        if not query.lstrip().startswith("query"):
            raise ReportError(
                "UNSAFE_QUERY", "Only fixed GraphQL read queries are supported."
            )
        if self.requests >= self.max_requests:
            raise ReportError(
                "REQUEST_BUDGET",
                "Read budget reached; use a narrower date or repository scope.",
            )
        self.requests += 1
        payload = json.dumps({"query": query, "variables": variables or {}})
        raw = run_read(
            ["gh", "api", "graphql", "--hostname", "github.com", "--input", "-"],
            payload,
            graphql=True,
        )
        try:
            response = json.loads(raw)
            if response.get("errors"):
                types = {
                    e.get("type") for e in response["errors"] if isinstance(e, dict)
                }
                code = (
                    "RATE_LIMIT"
                    if "RATE_LIMITED" in types
                    else "INACCESSIBLE"
                    if types & {"NOT_FOUND", "FORBIDDEN"}
                    else "GRAPHQL_ERROR"
                )
                raise ReportError(
                    code,
                    "GitHub returned incomplete GraphQL data; do not claim complete coverage.",
                )
            if not isinstance(response.get("data"), dict):
                raise ValueError
            return response["data"]
        except (ValueError, TypeError, AttributeError):
            raise ReportError(
                "INVALID_RESPONSE", "GitHub returned an invalid read response."
            ) from None

    def viewer(self, expected_id: int | None = None) -> dict:
        value = self.query("query { viewer { id databaseId login } }").get("viewer")
        if not value or not value.get("id") or not value.get("databaseId"):
            raise ReportError(
                "IDENTITY_UNAVAILABLE",
                "The authenticated GitHub identity could not be verified.",
            )
        if expected_id is not None and value["databaseId"] != expected_id:
            raise ReportError(
                "IDENTITY_MISMATCH",
                "Connector and GitHub CLI accounts differ; do not combine their evidence.",
            )
        return {**value, "host": "github.com"}

    def pages(
        self,
        query: str,
        variables: dict,
        path: tuple[str, ...],
        stop=None,
        page_counts_only: bool = False,
    ) -> dict:
        nodes, seen, cursors = [], set(), set()
        cursor = None
        total = None
        limits = []
        while True:
            try:
                data = self.query(query, {**variables, "cursor": cursor})
                connection = data
                for key in path:
                    connection = connection[key]
                page = connection["pageInfo"]
                count = connection["totalCount"]
                if (
                    type(count) is not int
                    or count < 0
                    or type(page["hasNextPage"]) is not bool
                ):
                    raise ValueError
                if not page_counts_only and total is not None and total != count:
                    limits.append("collection changed while paginating")
                total = count
                raw_nodes = connection["nodes"]
                if not isinstance(raw_nodes, list):
                    raise ValueError
                if page_counts_only and connection.get("pageCount") != len(raw_nodes):
                    limits.append("timeline page count does not match retrieved nodes")
                stopped = False
                for node in raw_nodes:
                    if node is None:
                        limits.append("inaccessible node")
                        continue
                    # PullRequestCommit is an edge-like node without a Node ID.
                    key = (
                        node.get("id")
                        or (node.get("commit") or {}).get("id")
                        or node.get("path")
                    )
                    if not key:
                        # GitActor is not a Node; account identity is enough here.
                        key = (node.get("user") or {}).get("id") or node.get("name")
                    if key in seen:
                        limits.append("duplicate node while paginating")
                        continue
                    seen.add(key)
                    if stop and stop(node):
                        stopped = True
                    else:
                        nodes.append(node)
                if stopped:
                    break
                if not page["hasNextPage"]:
                    if not page_counts_only and len(seen) != total:
                        limits.append("connection count does not match retrieved nodes")
                    break
                cursor = page["endCursor"]
                if not cursor or cursor in cursors:
                    raise ValueError
                cursors.add(cursor)
            except ReportError as exc:
                limits.append(exc.code)
                break
            except (KeyError, TypeError, ValueError, AttributeError):
                limits.append("invalid pagination response")
                break
        return {
            "nodes": nodes,
            "total": total,
            "coverage": "partial" if limits else "complete",
            "limits": sorted(set(limits)),
        }

    def candidates(self, repo: str, kind: str, start: str | None) -> dict:
        owner, name = repo.split("/")
        connection = "pullRequests" if kind == "pr" else "issues"
        fields = DISCOVERY
        state_filter = "states:[OPEN], " if start is None else ""
        query = f"""query($owner:String!, $name:String!, $cursor:String) {{
          repository(owner:$owner, name:$name) {{
            {connection}(first:100, after:$cursor, {state_filter}orderBy:{{field:UPDATED_AT,direction:DESC}}) {{
              nodes {{ {fields} }} {PAGE}
            }}
          }}
        }}"""

        def stop(node):
            updated = timestamp(node.get("updatedAt"))
            if updated is None:
                raise ValueError
            return updated < timestamp(start)

        return self.pages(
            query,
            {"owner": owner, "name": name},
            ("repository", connection),
            stop if start is not None else None,
        )

    def connection(self, item: dict, name: str, fields: str) -> dict:
        typename = "PullRequest" if item["kind"] == "pr" else "Issue"
        arguments = "first:100, after:$cursor"
        if name == "timelineItems":
            arguments += ", itemTypes:[CLOSED_EVENT,REOPENED_EVENT,LABELED_EVENT,UNLABELED_EVENT,CROSS_REFERENCED_EVENT]"
        counts = PAGE + (" pageCount" if name == "timelineItems" else "")
        query = f"""query($id:ID!, $cursor:String) {{ node(id:$id) {{
          ... on {typename} {{ {name}({arguments}) {{ nodes {{ {fields} }} {counts} }} }}
        }} }}"""
        return self.pages(
            query,
            {"id": item["id"]},
            ("node", name),
            page_counts_only=name == "timelineItems",
        )

    def issue(self, node_id: str) -> dict:
        item = self.query(
            f"query($id:ID!) {{ node(id:$id) {{ ... on Issue {{ {ISSUE} }} }} }}",
            {"id": node_id},
        ).get("node")
        if not item or "number" not in item:
            raise ReportError("INACCESSIBLE", "A linked issue is inaccessible.")
        item["kind"] = "issue"
        return self.enrich(item, linked_only=True)

    def enrich(
        self, item: dict, linked_only: bool = False, *, inventory: bool = False
    ) -> dict:
        if "body" not in item:
            typename = "PullRequest" if item["kind"] == "pr" else "Issue"
            fields = PR if item["kind"] == "pr" else ISSUE
            detail = self.query(
                f"query($id:ID!) {{ node(id:$id) {{ ... on {typename} {{ {fields} }} }} }}",
                {"id": item["id"]},
            ).get("node")
            if not detail or detail.get("id") != item["id"]:
                raise ReportError("INACCESSIBLE", "Item details could not be verified.")
            item.update(detail)
        item["limits"] = []
        item["observed_at"] = iso(datetime.now(UTC))

        def read(name, fields, target):
            result = self.connection(item, name, fields)
            item[target] = result["nodes"]
            item["limits"].extend(name + ": " + limit for limit in result["limits"])
            return result

        if not inventory:
            read("timelineItems", TIMELINE, "timeline_data")
        if not linked_only and not inventory:
            read(
                "comments",
                "id url body createdAt author { " + ACTOR + " }",
                "comments_data",
            )
        if item["kind"] == "pr":
            files = read("files", "path", "files_data")
            item["files_coverage"] = files["coverage"]
            if files["total"] != item.get("changedFiles"):
                item["files_coverage"] = "partial"
                item["limits"].append(
                    "PR changed-file evidence incomplete: API file total differs from changedFiles"
                )
            read("closingIssuesReferences", ISSUE, "linked_issues_data")
            if inventory:
                item["coverage"] = "partial" if item["limits"] else "complete"
                return item
            read(
                "reviews",
                "id url body state submittedAt author { " + ACTOR + " }",
                "reviews_data",
            )
            threads = read(
                "reviewThreads",
                "id",
                "threads_data",
            )
            item["review_comments_data"] = []
            for thread in threads["nodes"]:
                # A thread's comments need their own cursor; never paginate only threads.
                query = (
                    "query($id:ID!, $cursor:String) { node(id:$id) { ... on PullRequestReviewThread { comments(first:100, after:$cursor) { nodes { id url body publishedAt author { "
                    + ACTOR
                    + " } } "
                    + PAGE
                    + " } } } }"
                )
                result = self.pages(query, {"id": thread["id"]}, ("node", "comments"))
                item["review_comments_data"].extend(result["nodes"])
                item["limits"].extend(
                    "review comments: " + limit for limit in result["limits"]
                )
            result = read(
                "commits",
                "commit { id url oid committedDate messageHeadline authors(first:100) { nodes { user { __typename id login name } } "
                + PAGE
                + " } }",
                "commit_nodes",
            )
            item["commits_data"] = []
            if result["total"] != (item.get("commits") or {}).get("totalCount"):
                item["limits"].append("PR commit count changed during collection")
            for node in result["nodes"]:
                commit = node["commit"]
                authors = commit["authors"]
                if authors["pageInfo"]["hasNextPage"]:
                    query = (
                        "query($id:ID!, $cursor:String) { node(id:$id) { ... on Commit { authors(first:100, after:$cursor) { nodes { user { __typename id login name } } "
                        + PAGE
                        + " } } } }"
                    )
                    result = self.pages(
                        query, {"id": commit["id"]}, ("node", "authors")
                    )
                    commit["authors_data"] = result["nodes"]
                    item["limits"].extend(
                        "commit authors: " + limit for limit in result["limits"]
                    )
                else:
                    commit["authors_data"] = [
                        author
                        for author in authors["nodes"]
                        if isinstance(author, dict)
                    ]
                    if len(commit["authors_data"]) != authors["totalCount"]:
                        item["limits"].append("incomplete commit authors")
                item["commits_data"].append(commit)
        item["coverage"] = "partial" if item["limits"] else "complete"
        return item

    def repository_context(
        self, repo: str, rows: list[dict], project: str | None
    ) -> dict:
        """Bounded corroboration at one commit, never execute repository content."""
        repositories([repo])
        owner, name = repo.split("/")
        context = {"folders": [], "evidence": {}, "coverage": "complete", "limits": []}
        try:
            data = self.query(
                "query($owner:String!, $name:String!) { repository(owner:$owner,name:$name) { defaultBranchRef { target { ... on Commit { oid tree { oid } } } } } }",
                {"owner": owner, "name": name},
            )
            target = ((data.get("repository") or {}).get("defaultBranchRef") or {}).get(
                "target"
            ) or {}
            commit, tree = target.get("oid"), (target.get("tree") or {}).get("oid")
            if not all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40,64}", value)
                for value in (commit, tree)
            ):
                raise ReportError(
                    "CONTEXT_UNAVAILABLE", "Repository tree identity unavailable."
                )
            context["commit"] = commit
            if self.requests >= self.max_requests:
                raise ReportError("REQUEST_BUDGET", "Read budget reached.")
            self.requests += 1
            raw = json.loads(
                run_read(
                    [
                        "gh",
                        "api",
                        "--hostname",
                        "github.com",
                        "--method",
                        "GET",
                        f"/repos/{owner}/{name}/git/trees/{tree}?recursive=1",
                    ]
                )
            )
            nodes = raw.get("tree")
            if not isinstance(nodes, list):
                raise ValueError
            folders = set()
            paths = set()
            for node in nodes:
                if not isinstance(node, dict) or not valid_git_path(node.get("path")):
                    context["limits"].append("invalid repository path evidence")
                    continue
                path = node["path"]
                paths.add(path)
                if node.get("type") == "tree":
                    folders.add(path)
            if raw.get("truncated") is not False:
                context["limits"].append("repository tree incomplete")
            if len(folders) > 2000:
                context["limits"].append("folder context bounded to 2000 entries")
            # Prefer shallow folders and the selected path; do not turn an omitted path into nonmembership.
            ordered = sorted(folders, key=lambda f: (f != project, f.count("/"), f))[
                :2000
            ]
            context["folders"] = sorted(ordered)
            context["evidence"] = {
                "tree:" + f: {"folder": f, "path": f, "kind": "directory"}
                for f in ordered
            }
            prose = " ".join(
                r["title"] + " " + r["description"]["text"] for r in rows
            ).lower()
            candidates = [
                f
                for f in ordered
                if f == project
                or re.search(
                    r"(?<![a-z0-9])"
                    + re.escape(f.split("/")[-1].lower())
                    + r"(?![a-z0-9])",
                    prose,
                )
            ]
            documents = [("", "README.md")] + [
                (f, f + "/README.md") for f in candidates if f + "/README.md" in paths
            ]
            if len(documents) > 12:
                context["limits"].append("README context bounded to 12 documents")
            for folder, path in documents[:12]:
                if path not in paths:
                    continue
                blob = (
                    self.query(
                        "query($owner:String!, $name:String!, $expression:String!) { repository(owner:$owner,name:$name) { object(expression:$expression) { ... on Blob { text isTruncated byteSize } } } }",
                        {
                            "owner": owner,
                            "name": name,
                            "expression": commit + ":" + path,
                        },
                    ).get("repository")
                    or {}
                ).get("object") or {}
                if not isinstance(blob.get("text"), str):
                    context["limits"].append("README text unavailable")
                    continue
                excerpt = evidence_text(blob["text"], 6000)
                excerpt["truncated"] = (
                    excerpt["truncated"] or blob.get("isTruncated") is not False
                )
                context["evidence"]["readme:" + path] = {
                    "folder": folder,
                    "path": path,
                    "kind": "readme",
                    **excerpt,
                }
                if excerpt["truncated"]:
                    context["limits"].append("README excerpts truncated")
        except (ReportError, ValueError, TypeError, KeyError, AttributeError) as exc:
            context["limits"].append(
                exc.code
                if isinstance(exc, ReportError)
                else "invalid repository context"
            )
        context["limits"] = sorted(set(context["limits"]))
        context["coverage"] = "partial" if context["limits"] else "complete"
        return context
