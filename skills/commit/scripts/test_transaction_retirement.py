"""Real disposable evidence archives; no historical grant is executed."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import transaction_retirement as admin

spec = importlib.util.spec_from_file_location(
    "retirement_fixture", Path(__file__).with_name("test-commit-transaction.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
tx = admin.tx


class RetirementTests(fixture.TransactionFixture):
    def historical(self, version=1):
        head = fixture.git(self.root, "rev-parse", "HEAD")
        common = tx._common_dir(self.root)
        self.store = tx._transaction_root(common)
        session = tx._digest_text("historical-session")
        intent = {"fixture": "original intake"}
        receipt = tx._digest_bytes(tx._stable_json(intent))
        self.relative = f"sessions/{session[:24]}/tasks/{receipt}.json"
        self.task = self.store / self.relative
        ref = "refs/heads/feature/test"
        grant = dict.fromkeys(admin.GRANT_KEYS)
        grant.update(
            schema=f"commit-transaction.task.v{version}",
            repo_root=str(self.root),
            worktree=str(self.root),
            common_dir=str(common),
            session_sha256=session,
            requested_action="create-pr",
            receipt_sha256=receipt,
            turn_sha256="a" * 64,
            prompt_sha256="b" * 64,
            initial_ref=ref,
            initial_head=head,
            targets={ref: {"head": head, "kind": "local"}},
            dependencies={ref: []},
            origin_sha256="c" * 64,
            allow_default_branch=False,
        )
        target = {"head": head, "commits": 0, "last_claim": None, "sync": None}
        state = {
            "schema": f"commit-transaction.task-state.v{version}",
            "targets": {ref: target},
            "base_head": None,
        }
        if version >= 2:
            grant["completion"] = {
                "policy": "preserve",
                "final_ref": ref,
                "refs": {ref: head},
            }
            target.update(materialized=True, merged_result=None)
            state["completion"] = {
                "phase": "active",
                "pending": None,
                "effects": [],
                "updates": {},
                "fetch": "not-requested",
                "destination_head": None,
                "checkout": None,
            }
        if version >= 3:
            grant["pr_targets"] = {}
            target["publication"] = {
                "remote_head": head,
                "published_head": None,
                "last_rejected": None,
                "advanced_head": None,
                "pending": None,
            }
        if version == 4:
            grant["review_parent"] = None
            state["reviews"] = {}
        state["grant_sha256"] = tx._digest_bytes(tx._stable_json(grant))
        tx._atomic_json(self.task, grant)
        tx._atomic_json(self.task.with_suffix(".state.json"), state)
        tx._atomic_json(self.task.parent.parent / "intent.json", intent)
        self.grant, self.state = grant, state
        return admin.observe(self.root, self.relative)

    def evidence(self, audit):
        proof = self.codex_home / "independent-fixture-proof.json"
        tx._atomic_json(
            proof,
            {
                "fixture": "owning session relinquished; independent remote inspection settled effects"
            },
        )
        refs = [{"path": str(proof), "sha256": tx._digest_bytes(proof.read_bytes())}]
        return {
            "schema": admin.EVIDENCE,
            "audit_sha256": audit["sha256"],
            "owner": {"status": "relinquished", "references": refs},
            "effects": {"status": "settled", "references": refs},
        }

    def test_audit_is_read_only_and_does_not_infer_remote_or_owner_state(self):
        self.historical()
        before = {str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}
        result = admin.observe(self.root, self.relative)
        after = {str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["owner"], "unverified")
        self.assertEqual(result["remote_effects"], "unverified")
        self.assertFalse(result["publication_observed"])
        self.assertEqual(result["disposition"], "retained")

    def test_known_schemas_retire_without_removing_grant_or_shared_evidence(self):
        for version in range(1, 5):
            with self.subTest(version=version):
                audit = self.historical(version)
                evidence = self.evidence(audit)
                receipt = self.task.parent.parent / "intent.json"
                before = receipt.read_bytes()
                original = self.task.read_bytes()
                result = admin.retire(self.root, audit, evidence)
                self.assertEqual(result["status"], "retired")
                self.assertEqual(receipt.read_bytes(), before)
                self.assertEqual(
                    admin.decode(self.task.read_bytes())["schema"], admin.TOMBSTONE
                )
                archive = (
                    self.store
                    / "retirements"
                    / audit["sha256"]
                    / "files"
                    / self.relative
                )
                self.assertEqual(archive.read_bytes(), original)
                self.assertTrue(admin.retire(self.root, audit, evidence)["replayed"])
                with self.assertRaises(tx.TransactionError):
                    tx._validate_task(
                        {
                            "owner_evidence_path": str(self.task),
                            "owner_evidence_sha256": tx._digest_bytes(
                                self.task.read_bytes()
                            ),
                        },
                        self.root,
                        "historical-session",
                    )
                # Every historical reader requires its exact task schema at this
                # still-present pathname; the tombstone can never satisfy it.
                self.assertNotIn(
                    admin.TOMBSTONE,
                    {f"commit-transaction.task.v{n}" for n in range(1, 5)},
                )

    def test_unproven_owner_or_effects_cannot_retire(self):
        audit = self.historical(3)
        for key in ("owner", "effects"):
            evidence = self.evidence(audit)
            evidence[key]["status"] = "unknown"
            with self.subTest(key=key), self.assertRaises(tx.TransactionError):
                admin.retire(self.root, audit, evidence)
        evidence = self.evidence(audit)
        evidence["owner"]["references"] = []
        with self.assertRaises(tx.TransactionError):
            admin.retire(self.root, audit, evidence)

    def test_pending_effects_and_changed_snapshot_are_retained(self):
        audit = self.historical(3)
        evidence = self.evidence(audit)
        target = next(iter(self.state["targets"].values()))
        target["publication"]["pending"] = {
            "from_head": target["head"],
            "to_head": target["head"],
        }
        tx._atomic_json(self.task.with_suffix(".state.json"), self.state)
        self.assertIn(
            "pending_publication", admin.observe(self.root, self.relative)["blockers"]
        )
        with self.assertRaises(tx.TransactionError):
            admin.retire(self.root, audit, evidence)

    def test_unknown_shape_and_symlink_are_rejected(self):
        self.historical()
        self.grant["unexpected"] = True
        tx._atomic_json(self.task, self.grant)
        with self.assertRaises(tx.TransactionError):
            admin.observe(self.root, self.relative)

        self.task.unlink()
        self.task.symlink_to(self.task.with_suffix(".state.json"))
        with self.assertRaises(tx.TransactionError):
            admin.observe(self.root, self.relative)

    def test_scratch_validation_journals_require_original_owner(self):
        self.historical(3)
        for journal in (
            {"phase": "creating"},
            {"unknown": "journal"},
            {"phase": "done"},
        ):
            tx._atomic_json(self.task.with_suffix(".validation.json"), journal)
            audit = admin.observe(self.root, self.relative)
            self.assertIn(
                "validation_journal_requires_original_owner", audit["blockers"]
            )
            with self.assertRaises(tx.TransactionError):
                admin.retire(self.root, audit, self.evidence(audit))

    def test_interruptions_before_and_after_tombstone_replay(self):
        for after in (False, True):
            with self.subTest(after=after):
                audit = self.historical(2)
                evidence = self.evidence(audit)
                real = tx._atomic_json

                def interrupt(path, value):
                    if path == self.task:
                        if after:
                            real(path, value)
                        raise RuntimeError("interrupted")
                    return real(path, value)

                with (
                    patch.object(tx, "_atomic_json", side_effect=interrupt),
                    self.assertRaises(RuntimeError),
                ):
                    admin.retire(self.root, audit, evidence)
                self.assertTrue(self.task.exists())
                self.assertEqual(
                    admin.retire(self.root, audit, evidence)["replayed"], after
                )

    def test_newer_shared_authorization_is_never_archived_or_modified(self):
        self.historical(3)
        path = self.task.parent.parent / "authorization.json"
        tx._atomic_json(path, {"owner_evidence_path": "another operation"})
        audit = admin.observe(self.root, self.relative)
        self.assertNotIn(str(path.relative_to(self.store)), audit["files"])
        before = path.read_bytes()
        admin.retire(self.root, audit, self.evidence(audit))
        self.assertEqual(path.read_bytes(), before)

    def test_pending_sync_completion_and_delegation_are_retained(self):
        cases = ("sync", "completion", "reviews")
        for kind in cases:
            self.historical(4)
            target = next(iter(self.state["targets"].values()))
            head = target["head"]
            ref = next(iter(self.state["targets"]))
            if kind == "sync":
                target["sync"] = {
                    "from_head": head,
                    "source_head": head,
                    "source_ref": ref,
                    "result_head": None,
                    "result_tree": None,
                }
                blocker = "pending_sync"
            elif kind == "completion":
                self.state["completion"]["pending"] = {
                    "kind": "switch",
                    "ref": ref,
                    "old": head,
                    "new": head,
                    "from_ref": ref,
                    "from_head": head,
                    "status_sha256": "a" * 64,
                }
                blocker = "pending_completion"
            else:
                self.state["reviews"] = {ref: {"phase": "active"}}
                blocker = "delegated_execution_requires_original_owner"
            tx._atomic_json(self.task.with_suffix(".state.json"), self.state)
            audit = admin.observe(self.root, self.relative)
            self.assertIn(blocker, audit["blockers"])
            with self.assertRaises(tx.TransactionError):
                admin.retire(self.root, audit, self.evidence(audit))

    def test_claim_uncertainty_and_missing_last_claim_are_retained(self):
        for state in ("PREPARED", "STAGED", "REVIEW_REQUIRED", "STALE"):
            self.historical(3)
            head = self.grant["initial_head"]
            tree = fixture.git(self.root, "rev-parse", "HEAD^{tree}")
            ref = self.grant["initial_ref"]
            claim = dict.fromkeys(admin.CLAIM_KEYS)
            claim.update(
                schema=tx.ROOT_CLAIM_SCHEMA,
                state=state,
                repo_root=str(self.root),
                worktree=str(self.root),
                common_dir=self.grant["common_dir"],
                ref=ref,
                branch=ref.removeprefix("refs/heads/"),
                base_head=head,
                initial_index_tree=tree,
                candidate_tree=tree,
                candidate_index_sha256=tx._digest_text(tree),
                session_sha256=self.grant["session_sha256"],
                turn_sha256="b" * 64,
                initial_status_sha256="c" * 64,
                authorization_sha256="d" * 64,
                token_sha256="e" * 64,
                authorization_owner="create-pr",
                allow_default_branch=False,
                owner_evidence_path=str(self.task),
                owner_evidence_sha256=self.state["grant_sha256"],
                commit_head=head if state in {"STALE", "REVIEW_REQUIRED"} else None,
                commit_tree=tree if state in {"STALE", "REVIEW_REQUIRED"} else None,
            )
            path = self.store / "claims" / (tx._ref_key(ref) + ".json")
            tx._atomic_json(path, claim)
            target = self.state["targets"][ref]
            target["last_claim"] = tx._digest_bytes(tx._stable_json(claim))
            tx._atomic_json(self.task.with_suffix(".state.json"), self.state)
            audit = admin.observe(self.root, self.relative)
            self.assertIn(
                "stale_claim_with_effect" if state == "STALE" else "unresolved_claim",
                audit["blockers"],
            )
            with self.assertRaises(tx.TransactionError):
                admin.retire(self.root, audit, self.evidence(audit))
            path.unlink()
            self.assertIn(
                "last_claim_missing",
                admin.observe(self.root, self.relative)["blockers"],
            )

    def test_archive_member_interruption_leaves_original_authority_and_replays(self):
        audit = self.historical(3)
        evidence = self.evidence(audit)
        before = self.task.read_bytes()
        real = admin.immutable

        def interrupt(path, raw, boundary):
            real(path, raw, boundary)
            if path.name == self.task.name:
                raise RuntimeError("archive member published")

        with (
            patch.object(admin, "immutable", side_effect=interrupt),
            self.assertRaises(RuntimeError),
        ):
            admin.retire(self.root, audit, evidence)
        self.assertEqual(self.task.read_bytes(), before)
        self.assertFalse(admin.retire(self.root, audit, evidence)["replayed"])

    def test_modified_archive_blocks_replay(self):
        audit = self.historical()
        evidence = self.evidence(audit)
        admin.retire(self.root, audit, evidence)
        path = self.store / "retirements" / audit["sha256"] / "files" / self.relative
        tx._atomic_json(path, {"modified": True})
        with self.assertRaises(tx.TransactionError):
            admin.retire(self.root, audit, evidence)

    def test_replay_uses_archived_proof_after_original_is_rotated(self):
        audit = self.historical(3)
        evidence = self.evidence(audit)
        real = tx._atomic_json

        def interrupt(path, value):
            if path.name == "committed.json":
                raise RuntimeError("interrupted after tombstone")
            return real(path, value)

        with (
            patch.object(tx, "_atomic_json", side_effect=interrupt),
            self.assertRaises(RuntimeError),
        ):
            admin.retire(self.root, audit, evidence)
        Path(evidence["owner"]["references"][0]["path"]).unlink()
        self.assertTrue(admin.retire(self.root, audit, evidence)["replayed"])


if __name__ == "__main__":
    unittest.main()
