#!/usr/bin/env python3
"""Administrative evidence audit/retirement; never executes historical authority."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import commit_transaction as tx

AUDIT = "commit-transaction.retirement-audit.v1"
EVIDENCE = "commit-transaction.retirement-evidence.v1"
TOMBSTONE = "commit-transaction.retired.v1"
TASK = re.compile(r"sessions/[0-9a-f]{24}/tasks/[0-9a-f]{64}\.json")
GRANT_KEYS = set(
    "schema repo_root worktree common_dir session_sha256 requested_action receipt_sha256 turn_sha256 prompt_sha256 initial_ref initial_head targets dependencies base_ref base_head origin_sha256 default_ref allow_default_branch validation_ref recovery_claim_sha256".split()
)
CLAIM_KEYS = set(
    "schema state repo_root worktree common_dir ref branch base_head initial_index_tree initial_status_sha256 candidate_tree candidate_index_sha256 session_sha256 turn_sha256 authorization_sha256 authorization_owner owner_evidence_path owner_evidence_sha256 token_sha256 allow_default_branch commit_head commit_tree failure".split()
)


def require(ok, message):
    if not ok:
        raise tx.TransactionError(message, "effect_unknown")


def read(path, boundary):
    """Bounded stable bytes, with no symlinks, hardlinks or foreign owners."""
    require(
        tx._safe_private_file(path, boundary), "unsafe administrative evidence path"
    )
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read(tx.MAX_JSON_BYTES + 1)
        after = os.fstat(stream.fileno())

    def identity(info):
        return [
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ]

    require(
        len(raw) <= tx.MAX_JSON_BYTES
        and identity(before) == identity(after)
        and identity(after) == identity(path.lstat()),
        "administrative evidence changed during read",
    )
    return raw, {"sha256": tx._digest_bytes(raw), "identity": identity(after)}


def decode(raw):
    value = json.loads(raw)
    require(isinstance(value, dict), "administrative evidence must be an object")
    return value


def identity(repo):
    root = tx._canonical_repo(str(repo))
    common = tx._common_dir(root)
    return root, common, tx._transaction_root(common)


def task_path(store, relative):
    require(
        isinstance(relative, str) and TASK.fullmatch(relative),
        "noncanonical historical task selector",
    )
    return store / relative


def observe(repo, relative):
    root, common, store = identity(repo)
    task = task_path(store, relative)
    files = {}

    def capture(path):
        raw, metadata = read(path, store)
        files[str(path.relative_to(store))] = metadata
        return decode(raw)

    grant = capture(task)
    if grant.get("schema") == TOMBSTONE:
        return {
            "schema": AUDIT,
            "task": relative,
            "disposition": "retired",
            "audit_sha256": grant["audit_sha256"],
        }
    blocked = []
    version = grant.get("schema", "").removeprefix("commit-transaction.task.v")
    require(version in {"1", "2", "3", "4"}, "unsupported historical grant schema")
    version = int(version)
    extra = (
        ({"completion"} if version >= 2 else set())
        | ({"pr_targets"} if version >= 3 else set())
        | ({"review_parent"} if version == 4 else set())
    )
    require(set(grant) == GRANT_KEYS | extra, "unsupported historical grant fields")
    require(
        grant["common_dir"] == str(common)
        and grant["repo_root"] == str(root)
        and grant["worktree"] == str(root)
        and grant["requested_action"] == "create-pr"
        and tx._valid_digest(grant["session_sha256"])
        and relative
        == f"sessions/{grant['session_sha256'][:24]}/tasks/{grant['receipt_sha256']}.json",
        "historical grant identity mismatch",
    )
    grant_hash = tx._digest_bytes(tx._stable_json(grant))
    checkpoint = capture(task.with_suffix(".state.json"))
    state_keys = {"schema", "grant_sha256", "targets", "base_head"}
    if version >= 2:
        state_keys.add("completion")
    if version == 4:
        state_keys.add("reviews")
        if checkpoint.get("reviews") or grant["review_parent"] is not None:
            blocked.append("delegated_execution_requires_original_owner")
    require(
        set(checkpoint) == state_keys
        and checkpoint["schema"] == f"commit-transaction.task-state.v{version}"
        and checkpoint["grant_sha256"] == grant_hash
        and isinstance(grant["targets"], dict)
        and grant["targets"]
        and set(checkpoint["targets"]) == set(grant["targets"]),
        "unsupported historical checkpoint",
    )
    target_keys = {"head", "commits", "last_claim", "sync"}
    if version >= 2:
        target_keys |= {"materialized", "merged_result"}
    if version >= 3:
        target_keys.add("publication")
    heads, last_claims = set(), set()
    for ref, target in checkpoint["targets"].items():
        original = grant["targets"][ref]
        require(
            set(original) == {"head", "kind"}
            and tx._valid_object(original["head"])
            and isinstance(target, dict)
            and set(target) == target_keys
            and tx._valid_object(target["head"])
            and type(target["commits"]) is int
            and target["commits"] >= 0,
            "unsupported historical target",
        )
        heads.update((original["head"], target["head"]))
        if target["sync"] is not None:
            blocked.append("pending_sync")
        if target["last_claim"] is not None:
            require(tx._valid_digest(target["last_claim"]), "invalid last claim digest")
            last_claims.add(target["last_claim"])
        elif target["commits"]:
            blocked.append("committed_repairs_missing_claim")
        if version >= 2 and target["merged_result"] is not None:
            require(tx._valid_object(target["merged_result"]), "invalid merge result")
            heads.add(target["merged_result"])
        if version >= 3:
            publication = target["publication"]
            require(
                isinstance(publication, dict)
                and set(publication)
                == {
                    "remote_head",
                    "published_head",
                    "last_rejected",
                    "advanced_head",
                    "pending",
                },
                "unsupported publication checkpoint",
            )
            if publication["pending"] is not None:
                blocked.append("pending_publication")
    if version >= 2:
        completion = checkpoint["completion"]
        require(
            isinstance(completion, dict)
            and set(completion)
            == {
                "phase",
                "pending",
                "effects",
                "updates",
                "fetch",
                "destination_head",
                "checkout",
            },
            "unsupported completion checkpoint",
        )
        tx._validate_completion(completion, grant)
        if completion["pending"] is not None:
            blocked.append("pending_completion")
    for suffix in (".validation.json", ".closed.json"):
        path = task.with_suffix(suffix)
        if path.exists() or path.is_symlink():
            capture(path)
            if suffix == ".closed.json":
                blocked.append("already_closed")
            else:
                # Scratch refs and their recovery protocol belong to the old
                # execution owner, even if a journal labels itself done.
                blocked.append("validation_journal_requires_original_owner")
    attempts = task.with_suffix(".attempts")
    if attempts.exists() or attempts.is_symlink():
        require(
            not attempts.is_symlink() and attempts.is_dir(), "unsafe attempt directory"
        )
        for path in sorted(attempts.iterdir()):
            require(
                re.fullmatch(r"[0-9a-f]{64}\.json", path.name),
                "unsupported attempt artifact",
            )
            attempt = capture(path)
            require(
                attempt.get("owner_evidence_path") == str(task)
                and attempt.get("owner_evidence_sha256") == grant_hash,
                "attempt owner mismatch",
            )
    claims = []
    for pattern in ("claims/*.json", "history/*/*.json"):
        for path in sorted(store.glob(pattern)):
            raw, metadata = read(path, store)
            claim = decode(raw)
            if claim.get("owner_evidence_path") != str(task):
                continue
            require(
                set(claim) == CLAIM_KEYS
                and claim["schema"] == tx.ROOT_CLAIM_SCHEMA
                and claim["owner_evidence_sha256"] == grant_hash
                and claim["session_sha256"] == grant["session_sha256"]
                and claim["common_dir"] == str(common)
                and claim["ref"] in grant["targets"],
                "unsupported or mismatched historical claim",
            )
            require(
                claim["repo_root"] == str(root)
                and claim["worktree"] == str(root)
                and claim["branch"] == claim["ref"].removeprefix("refs/heads/")
                and claim["authorization_owner"] == "create-pr"
                and claim["state"] in tx.CLAIM_STATES
                and type(claim["allow_default_branch"]) is bool
                and all(
                    tx._valid_object(claim[key])
                    for key in ("base_head", "initial_index_tree", "candidate_tree")
                )
                and all(
                    tx._valid_digest(claim[key])
                    for key in (
                        "initial_status_sha256",
                        "candidate_index_sha256",
                        "turn_sha256",
                        "authorization_sha256",
                        "token_sha256",
                    )
                )
                and claim["candidate_index_sha256"]
                == tx._digest_text(claim["candidate_tree"])
                and (claim["commit_head"] is None) == (claim["commit_tree"] is None)
                and all(
                    claim[key] is None or tx._valid_object(claim[key])
                    for key in ("commit_head", "commit_tree")
                )
                and (
                    claim["failure"] is None
                    or isinstance(claim["failure"], str)
                    and bool(claim["failure"])
                ),
                "invalid historical claim fields",
            )
            files[str(path.relative_to(store))] = metadata
            claims.append(
                {
                    "sha256": tx._digest_bytes(tx._stable_json(claim)),
                    "state": claim["state"],
                }
            )
            if claim["state"] not in {"COMMITTED", "STALE"}:
                blocked.append("unresolved_claim")
            elif claim["state"] == "COMMITTED":
                require(
                    tx._valid_object(claim["commit_head"]), "committed claim lacks head"
                )
                heads.add(claim["commit_head"])
            elif claim["commit_head"] is not None:
                blocked.append("stale_claim_with_effect")
    if not last_claims <= {claim["sha256"] for claim in claims}:
        blocked.append("last_claim_missing")
    for name in ("intent.json", "authorization.json"):
        path = task.parent.parent / name
        if path.exists() or path.is_symlink():
            raw, metadata = read(path, store)
            value = decode(raw)
            associated = (
                tx._digest_bytes(raw) == grant["receipt_sha256"]
                if name == "intent.json"
                else value.get("owner_evidence_path") == str(task)
                and value.get("owner_evidence_sha256") == grant_hash
            )
            if associated:
                files[str(path.relative_to(store))] = metadata
    missing = [
        head
        for head in sorted(heads)
        if tx._run_git(
            root, ("cat-file", "-e", head + "^{commit}"), check=False
        ).returncode
    ]
    if missing:
        blocked.append("missing_commit_objects")
    result = {
        "schema": AUDIT,
        "repo_root": str(root),
        "task": relative,
        "grant_schema": grant["schema"],
        "files": files,
        "claims": claims,
        "missing_objects": missing,
        "blockers": sorted(set(blocked)),
        "owner": "unverified",
        "remote_effects": "unverified",
        "publication_observed": version >= 3,
        "disposition": "retained",
    }
    return {**result, "sha256": tx._digest_bytes(tx._stable_json(result))}


def immutable(path, raw, boundary):
    tx._secure_directory(path.parent, create=True)
    if path.exists() or path.is_symlink():
        require(read(path, boundary)[0] == raw, "retirement archive changed")
        return
    # All callers hold the repository lock. Publish only complete durable bytes;
    # an interrupted temporary write never becomes an immutable archive member.
    fd, temporary = tempfile.mkstemp(prefix=".retirement-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require(
            not path.exists() and not path.is_symlink(),
            "retirement archive appeared during write",
        )
        os.replace(temporary, path)
        tx._fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def evidence_refs(value, home, archive=None):
    require(
        isinstance(value, list) and 0 < len(value) <= 16,
        "retirement needs concrete evidence references",
    )
    result = {}
    for entry in value:
        require(
            isinstance(entry, dict)
            and set(entry) == {"path", "sha256"}
            and tx._valid_digest(entry["sha256"]),
            "invalid retirement evidence reference",
        )
        source = Path(entry["path"])
        require(
            source.is_absolute()
            and source.is_relative_to(home)
            and ".." not in source.parts,
            "retirement evidence must be private",
        )
        raw, _ = read(archive / "proofs" / entry["sha256"] if archive else source, home)
        require(
            raw.strip() and tx._digest_bytes(raw) == entry["sha256"],
            "retirement evidence drift",
        )
        result[entry["sha256"]] = raw
    return result


def retire(repo, audit, evidence):
    root, common, store = identity(repo)
    body = {k: v for k, v in audit.items() if k != "sha256"}
    require(
        audit.get("schema") == AUDIT
        and audit.get("sha256") == tx._digest_bytes(tx._stable_json(body))
        and audit.get("repo_root") == str(root)
        and not audit.get("blockers"),
        "invalid or blocked retirement audit",
    )
    require(
        set(evidence) == {"schema", "audit_sha256", "owner", "effects"}
        and evidence["schema"] == EVIDENCE
        and evidence["audit_sha256"] == audit["sha256"],
        "retirement evidence is not bound to the audit",
    )
    for name, statuses in (
        ("owner", {"relinquished", "terminated"}),
        ("effects", {"settled"}),
    ):
        value = evidence[name]
        require(
            isinstance(value, dict)
            and set(value) == {"status", "references"}
            and value["status"] in statuses,
            "owner or effect evidence is unresolved",
        )
    task = task_path(store, audit["task"])
    archive = store / "retirements" / audit["sha256"]
    tombstone = {
        "schema": TOMBSTONE,
        "audit_sha256": audit["sha256"],
        "grant_sha256": audit["files"][audit["task"]]["sha256"],
        "evidence_sha256": tx._digest_bytes(tx._stable_json(evidence)),
    }
    with tx._repository_lock(common):
        current = decode(read(task, store)[0])
        replay = current == tombstone
        if not replay:
            require(
                observe(root, audit["task"]) == audit,
                "historical record moved after audit",
            )
        else:
            require(
                read(archive / "audit.json", store)[0] == tx._stable_json(audit)
                and read(archive / "evidence.json", store)[0]
                == tx._stable_json(evidence),
                "retirement replay archive mismatch",
            )
        proofs = {}
        for name in ("owner", "effects"):
            proofs.update(
                evidence_refs(
                    evidence[name]["references"],
                    tx._codex_home(),
                    archive if replay else None,
                )
            )
        for digest, raw in proofs.items():
            immutable(archive / "proofs" / digest, raw, store)
        for relative, snapshot in audit["files"].items():
            # Paths come from a fresh matching audit; replay validates its immutable copy.
            require(
                not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                "unsafe archive member",
            )
            destination = archive / "files" / relative
            raw, metadata = read(destination if replay else store / relative, store)
            require(
                metadata["sha256"] == snapshot["sha256"], "retirement snapshot changed"
            )
            if not replay:
                immutable(destination, raw, store)
        immutable(archive / "audit.json", tx._stable_json(audit), store)
        immutable(archive / "evidence.json", tx._stable_json(evidence), store)
        immutable(archive / "intent.json", tx._stable_json(tombstone), store)
        if not replay:
            require(
                observe(root, audit["task"]) == audit,
                "historical record changed before retirement",
            )
            for name in ("owner", "effects"):
                evidence_refs(evidence[name]["references"], tx._codex_home())
            tx._atomic_json(task, tombstone)
        tx._atomic_json(archive / "committed.json", tombstone)
    return {"status": "retired", "audit_sha256": audit["sha256"], "replayed": replay}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit_parser = commands.add_parser(
        "audit", help="read-only historical record inventory"
    )
    audit_parser.add_argument("--repo-root", type=Path, required=True)
    audit_parser.add_argument("--task", help="exact transaction-relative grant path")
    retire_parser = commands.add_parser(
        "retire", help="retire an audited record with independent evidence"
    )
    retire_parser.add_argument("--repo-root", type=Path, required=True)
    retire_parser.add_argument("--audit", type=Path, required=True)
    retire_parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "retire":
            result = retire(
                args.repo_root,
                decode(read(args.audit, tx._codex_home())[0]),
                decode(read(args.evidence, tx._codex_home())[0]),
            )
        else:
            _, _, store = identity(args.repo_root)
            tasks = (
                [args.task]
                if args.task
                else sorted(
                    str(path.relative_to(store))
                    for path in store.glob("sessions/*/tasks/*.json")
                    if TASK.fullmatch(str(path.relative_to(store)))
                    and not path.with_suffix(".closed.json").exists()
                )
            )
            results = []
            for task in tasks:
                try:
                    results.append(observe(args.repo_root, task))
                except (
                    tx.TransactionError,
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                ) as error:
                    results.append(
                        {"task": task, "disposition": "retained", "reason": str(error)}
                    )
            result = {"records": results}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (tx.TransactionError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
