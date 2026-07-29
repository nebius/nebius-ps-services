#!/usr/bin/env python3
"""Focused contract tests for the Agentic SDLC verifier."""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("verify_agentic_sdlc.py")
SPEC = importlib.util.spec_from_file_location("verify_agentic_sdlc", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


def git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


class VerifierContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.verification_root = (self.root / "verification").resolve(strict=False)
        self.project = self.verification_root / "disposable-project"
        self.selected = self.project / "services" / "resource-validator"
        self.selected.mkdir(parents=True)
        verifier.write_private_json(
            self.verification_root / verifier.VERIFICATION_ROOT_MARKER,
            {"schema": verifier.VERIFICATION_ROOT_MARKER_SCHEMA},
        )
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "config", "user.name", "Verifier Test")
        git(self.project, "config", "user.email", "verifier@example.invalid")
        (self.selected / "README.md").write_text("fixture\n", encoding="utf-8")
        (self.project / verifier.DISPOSABLE_FIXTURE_MARKER).write_text(
            verifier.DISPOSABLE_FIXTURE_MARKER_CONTENT,
            encoding="utf-8",
        )
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "initial")
        self.head = git(self.project, "rev-parse", "HEAD")
        self.ctx = verifier.Context(
            skills_root=self.root / "skills",
            repo_root=self.root / "source-repo",
            design_path=self.root / "design.md",
            global_skills_dir=self.root / "installed",
            codex_home=self.root / "codex-home",
            verification_root=self.verification_root,
            disposable_project=self.project,
            selected_project=self.selected,
            fixture_codex_home=self.verification_root / "fixture-codex-home",
            live_evidence_path=self.verification_root / "live-results.json",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_manifest(
        self,
        *,
        verification_id: str | None = None,
        baseline_head: str | None = None,
        final_head: str | None = None,
        context_baseline: str | None = None,
        all_lanes: bool = False,
    ) -> None:
        baseline = baseline_head or self.head
        context_head = context_baseline or self.head
        verifier.write_private_json(
            self.verification_root / "verification-context.json",
            verifier.expected_verification_context(self.ctx, context_head),
        )
        lanes = {}
        final = final_head or git(self.project, "rev-parse", "HEAD")
        identity = verification_id or verifier.verification_id(self.ctx, baseline)

        def assertion_record(
            owner: Path, assertion: str, *, passed: bool
        ) -> dict[str, object]:
            relative = owner / "artifacts" / f"{assertion}.json"
            artifact = self.verification_root / relative
            artifact.parent.mkdir(parents=True, exist_ok=True)
            value: dict[str, object] = {
                "assertion": assertion,
                "passed": passed,
            }
            if assertion == "canonical_phase_skill_names":
                value = {
                    "schema": "agentic-sdlc/canonical-phase-skill-names-v1",
                    "sequence": list(verifier.GOLDEN_PHASE_SEQUENCE),
                }
            elif assertion == "canonical_uat_handoff":
                value = {
                    "schema": "agentic-sdlc/canonical-uat-handoff-v1",
                    "from": "sdlc-commit",
                    "to": "sdlc-uat-tests",
                }
            artifact.write_text(
                json.dumps(value) + "\n",
                encoding="utf-8",
            )
            os.chmod(artifact, 0o600)
            return {
                "passed": passed,
                "evidence": [
                    {
                        "path": relative.as_posix(),
                        "sha256": verifier.file_sha256(artifact),
                    }
                ],
            }

        profiles = {}
        for profile in sorted(verifier.PROFILE_SOURCE_SCHEMAS):
            relative = Path("evidence") / "profiles" / profile / "result.json"
            evidence = self.verification_root / relative
            evidence.parent.mkdir(parents=True, exist_ok=True)
            source_relative = (
                Path("evidence") / "profiles" / profile / "sources" / "source.json"
            )
            source = self.verification_root / source_relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                json.dumps(
                    {
                        "schema": verifier.PROFILE_SOURCE_SCHEMAS[profile],
                        (
                            "verification_id" if profile == "three-tier" else "run_id"
                        ): f"{profile}:{identity}",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(source, 0o600)
            evidence.write_text(
                json.dumps(
                    {
                        "schema": "agentic-sdlc/evidence-profile-v3",
                        "profile": profile,
                        "verification_id": identity,
                        "status": "PARTIAL",
                        "source_schema": verifier.PROFILE_SOURCE_SCHEMAS[profile],
                        "source_identity": f"{profile}:{identity}",
                        "source_artifacts": [
                            {
                                "path": source_relative.as_posix(),
                                "sha256": verifier.file_sha256(source),
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(evidence, 0o600)
            profiles[profile] = {
                "status": "PARTIAL",
                "evidence": [relative.as_posix()],
            }

        for lane in verifier.LIVE_LANES:
            relative = Path("evidence") / lane / "result.json"
            evidence = self.verification_root / relative
            evidence.parent.mkdir(parents=True, exist_ok=True)
            lane_status = "PASS" if all_lanes else "PARTIAL"
            evidence.write_text(
                json.dumps(
                    {
                        "schema": verifier.LIVE_LANE_RESULT_SCHEMA,
                        "lane": lane,
                        "profile": "lightweight",
                        "verification_id": identity,
                        "baseline_head": baseline,
                        "final_head": final,
                        "status": lane_status,
                        "assertions": {
                            key: assertion_record(
                                Path("evidence") / lane,
                                key,
                                passed=lane_status == "PASS",
                            )
                            for key in verifier.LIVE_LANE_REQUIRED_ASSERTIONS[lane]
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(evidence, 0o600)
            lanes[lane] = {"status": lane_status, "evidence": [relative.as_posix()]}
        skills = {}
        for skill in verifier.REQUIRED_SDLC_SKILLS:
            relative = Path("evidence") / "skills" / skill / "result.json"
            evidence = self.verification_root / relative
            evidence.parent.mkdir(parents=True, exist_ok=True)
            basis = verifier.SKILL_EVIDENCE_BASES[skill]
            skill_status = "PASS" if all_lanes else "PARTIAL"
            evidence.write_text(
                json.dumps(
                    {
                        "schema": verifier.SKILL_RESULT_SCHEMA,
                        "skill": skill,
                        "verification_id": identity,
                        "baseline_head": baseline,
                        "final_head": final,
                        "status": skill_status,
                        "basis": basis,
                        "profiles": verifier.SKILL_REQUIRED_PROFILES[skill],
                        "assertions": {
                            assertion: assertion_record(
                                Path("evidence") / "skills" / skill,
                                assertion,
                                passed=skill_status == "PASS",
                            )
                            for assertion in verifier.SKILL_REQUIRED_ASSERTIONS[skill]
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(evidence, 0o600)
            skills[skill] = {
                "status": skill_status,
                "basis": basis,
                "evidence": [relative.as_posix()],
            }
        value = {
            "schema": verifier.LIVE_RESULTS_SCHEMA,
            "verification_id": identity,
            "project_root": str(self.selected),
            "baseline_head": baseline,
            "final_head": final,
            "profiles": profiles,
            "lanes": lanes,
            "skills": skills,
        }
        self.ctx.live_evidence_path.write_text(
            json.dumps(value) + "\n", encoding="utf-8"
        )
        os.chmod(self.ctx.live_evidence_path, 0o600)
        if all_lanes:
            for capabilities in verifier.DETERMINISTIC_SKILL_CAPABILITIES.values():
                for capability in capabilities:
                    self.ctx.add(
                        "Capability regression results",
                        capability,
                        "PASS",
                        "Test backing capability passed.",
                        capability_id=capability,
                    )
            self.ctx.add(
                "Stop continuation test results",
                "Do not auto-continue merge",
                "PASS",
                "Explicit user request is required.",
                capability_id="merge.authorization",
            )

    def commit_selected_change(self) -> str:
        path = self.selected / "value.py"
        path.write_text("VALUE = 1\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "selected live change")
        return git(self.project, "rev-parse", "HEAD")

    def test_any_deterministic_failure_forces_fail(self) -> None:
        self.ctx.add("Environment checked", "Execution contract", "FAIL", "failed")
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_missing_live_evidence_is_partial(self) -> None:
        verifier.add_agent_required_sections(self.ctx)
        live = [
            check
            for check in self.ctx.checks
            if check.capability_id and check.capability_id.startswith("live.")
        ]
        self.assertEqual(len(live), len(verifier.LIVE_LANES))
        self.assertTrue(all(check.status == "WARN" for check in live))
        self.assertEqual(verifier.final_status(self.ctx), "PARTIAL")

    def test_nonexistent_outside_live_manifest_fails_closed(self) -> None:
        self.ctx = replace(
            self.ctx, live_evidence_path=self.root / "outside-live-results.json"
        )
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_broken_symlink_live_manifest_fails_closed(self) -> None:
        self.ctx.live_evidence_path.symlink_to(
            self.verification_root / "missing-live-results.json"
        )
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_live_manifest_below_regular_file_parent_fails_closed(self) -> None:
        parent = self.verification_root / "not-a-directory"
        parent.write_text("preserve\n", encoding="utf-8")
        self.ctx = replace(self.ctx, live_evidence_path=parent / "live-results.json")
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_valid_live_evidence_is_accepted(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        lanes = verifier.load_live_results(self.ctx)
        self.assertIsNotNone(lanes)
        assert lanes is not None
        self.assertEqual(lanes["golden-path"]["status"], "PARTIAL")
        integrity = [
            check
            for check in self.ctx.checks
            if check.capability_id == "live.evidence-integrity"
        ]
        self.assertEqual([check.status for check in integrity], ["PASS"])

    def test_generic_digest_backed_evidence_cannot_reach_pass(self) -> None:
        self.commit_selected_change()
        self.write_manifest(all_lanes=True)
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_deterministic_profile_is_derived_from_verifier_capabilities(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        for capabilities in verifier.DETERMINISTIC_SKILL_CAPABILITIES.values():
            for capability in capabilities:
                self.ctx.add(
                    "Capability regression results",
                    capability,
                    "PASS",
                    "Capability passed.",
                    capability_id=capability,
                )
        manifest = json.loads(self.ctx.live_evidence_path.read_text(encoding="utf-8"))
        manifest["skills"]["sdlc-start"]["status"] = "PASS"
        self.ctx.live_evidence_path.write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(self.ctx.live_evidence_path, 0o600)
        result = self.verification_root / "evidence/skills/sdlc-start/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        value["status"] = "PASS"
        for assertion in value["assertions"].values():
            assertion["passed"] = True
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNotNone(verifier.load_live_results(self.ctx))

    def test_safety_profile_is_derived_from_merge_guard_capability(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        self.ctx.add(
            "Stop continuation test results",
            "Do not auto-continue merge",
            "PASS",
            "Explicit authorization is required.",
            capability_id="merge.authorization",
        )
        manifest = json.loads(self.ctx.live_evidence_path.read_text(encoding="utf-8"))
        manifest["skills"]["sdlc-merge-pr"]["status"] = "PASS"
        self.ctx.live_evidence_path.write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(self.ctx.live_evidence_path, 0o600)
        result = self.verification_root / "evidence/skills/sdlc-merge-pr/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        value["status"] = "PASS"
        for assertion in value["assertions"].values():
            assertion["passed"] = True
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNotNone(verifier.load_live_results(self.ctx))

    def test_placeholder_live_lane_result_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/golden-path/result.json"
        result.write_text('{"result":"pass"}\n', encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_skill_evidence_contract_covers_all_required_skills(self) -> None:
        self.assertEqual(
            set(verifier.SKILL_EVIDENCE_BASES), set(verifier.REQUIRED_SDLC_SKILLS)
        )
        self.assertEqual(verifier.SKILL_EVIDENCE_BASES["sdlc-merge-pr"], "safety-only")
        self.assertEqual(
            set(verifier.SKILL_REQUIRED_ASSERTIONS),
            set(verifier.REQUIRED_SDLC_SKILLS),
        )

    def test_observability_provider_is_a_runtime_parity_dependency(self) -> None:
        self.assertIn(
            "nebius-grafana-query",
            verifier.REQUIRED_RUNTIME_SUPPORT_SKILLS,
        )
        self.assertIn(
            "nebius-grafana-query",
            verifier.SOURCE_PARITY_SKILLS,
        )

        provider = self.ctx.skills_root / "nebius-grafana-query"
        provider.mkdir(parents=True)
        skill = provider / "SKILL.md"
        skill.write_text("provider contract v1\n", encoding="utf-8")
        first_identity = verifier.verification_id(self.ctx, self.head)
        skill.write_text("provider contract v2\n", encoding="utf-8")
        second_identity = verifier.verification_id(self.ctx, self.head)

        self.assertNotEqual(first_identity, second_identity)

        self.ctx.global_skills_dir.mkdir(parents=True)
        for support_name in verifier.REQUIRED_RUNTIME_SUPPORT_SKILLS:
            installed = self.ctx.global_skills_dir / support_name
            installed.mkdir()
            (installed / "SKILL.md").write_text(
                f"installed {support_name}\n",
                encoding="utf-8",
            )
        verifier.check_skill_discovery(self.ctx)
        support_checks = {
            check.capability_id: check.status
            for check in self.ctx.checks
            if check.capability_id
            in {"runtime.worktree-dependency", "runtime.observability-dependency"}
        }
        self.assertEqual(
            support_checks,
            {
                "runtime.worktree-dependency": "PASS",
                "runtime.observability-dependency": "PASS",
            },
        )

    def test_troubleshoot_is_conditional_runtime_support_not_golden_phase(self) -> None:
        self.assertIn("troubleshoot", verifier.REQUIRED_RUNTIME_SUPPORT_SKILLS)
        self.assertIn("troubleshoot", verifier.SOURCE_PARITY_SKILLS)
        self.assertNotIn("troubleshoot", verifier.GOLDEN_PHASE_SEQUENCE)
        self.assertIn(
            "ambiguous_failure_diagnosed_once",
            verifier.LIVE_LANE_REQUIRED_ASSERTIONS["failure-routing"],
        )

    def test_generic_skill_assertion_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        artifact = self.verification_root / "evidence/skills/sdlc-start/result.json"
        value = json.loads(artifact.read_text(encoding="utf-8"))
        existing = next(iter(value["assertions"].values()))
        value["assertions"] = {"required contract exercised": existing}
        artifact.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(artifact, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_all_true_lane_assertions_without_artifact_provenance_are_rejected(
        self,
    ) -> None:
        self.commit_selected_change()
        self.write_manifest()
        artifact = self.verification_root / "evidence/golden-path/result.json"
        value = json.loads(artifact.read_text(encoding="utf-8"))
        value["assertions"] = {
            name: True for name in verifier.LIVE_LANE_REQUIRED_ASSERTIONS["golden-path"]
        }
        artifact.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(artifact, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_skill_result_with_stale_identity_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        artifact = self.verification_root / "evidence/skills/sdlc-tdd/result.json"
        value = json.loads(artifact.read_text(encoding="utf-8"))
        value["verification_id"] = "0" * 64
        artifact.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(artifact, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_assertion_artifact_digest_mismatch_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/golden-path/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        record = next(iter(value["assertions"].values()))
        record["evidence"][0]["sha256"] = "0" * 64
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_short_uat_alias_in_canonical_handoff_is_rejected(self) -> None:
        artifact = self.verification_root / "short-uat-handoff.json"
        artifact.write_text(
            json.dumps(
                {
                    "schema": "agentic-sdlc/canonical-uat-handoff-v1",
                    "from": "sdlc-commit",
                    "to": "sdlc-uat",
                }
            ),
            encoding="utf-8",
        )
        self.assertFalse(
            verifier.assertion_artifact_semantics_valid(
                "canonical_uat_handoff", artifact
            )
        )

    @unittest.skipUnless(os.name == "posix", "hard-link safety requires POSIX")
    def test_hard_linked_assertion_artifact_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/golden-path/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        record = value["assertions"]["clean_final_head"]["evidence"][0]
        artifact = self.verification_root / record["path"]
        outside = self.root / "hard-linked-artifact.json"
        os.link(artifact, outside)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_skill_pass_requires_registered_three_tier_profile(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        manifest = json.loads(self.ctx.live_evidence_path.read_text(encoding="utf-8"))
        manifest["skills"]["sdlc-gui-test"]["status"] = "PASS"
        skill_result = (
            self.verification_root / "evidence/skills/sdlc-gui-test/result.json"
        )
        skill_value = json.loads(skill_result.read_text(encoding="utf-8"))
        skill_value["status"] = "PASS"
        skill_result.write_text(json.dumps(skill_value), encoding="utf-8")
        os.chmod(skill_result, 0o600)
        del manifest["profiles"]["three-tier"]
        self.ctx.live_evidence_path.write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(self.ctx.live_evidence_path, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_profile_without_source_artifacts_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/profiles/lightweight/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        del value["source_artifacts"]
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_minimal_profile_header_cannot_claim_pass(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        manifest = json.loads(self.ctx.live_evidence_path.read_text(encoding="utf-8"))
        manifest["profiles"]["lightweight"]["status"] = "PASS"
        self.ctx.live_evidence_path.write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(self.ctx.live_evidence_path, 0o600)
        result = self.verification_root / "evidence/profiles/lightweight/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        value["status"] = "PASS"
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_profile_source_digest_mismatch_is_rejected(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/profiles/three-tier/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        value["source_artifacts"][0]["sha256"] = "0" * 64
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_profile_source_schema_must_match_profile(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/profiles/lightweight/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        value["source_schema"] = verifier.PROFILE_SOURCE_SCHEMAS["three-tier"]
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_profile_source_content_must_match_declared_schema(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        result = self.verification_root / "evidence/profiles/lightweight/result.json"
        value = json.loads(result.read_text(encoding="utf-8"))
        source_ref = value["source_artifacts"][0]
        source = self.verification_root / source_ref["path"]
        source.write_text(
            json.dumps(
                {
                    "schema": verifier.PROFILE_SOURCE_SCHEMAS["three-tier"],
                    "run_id": value["source_identity"],
                }
            ),
            encoding="utf-8",
        )
        source_ref["sha256"] = verifier.file_sha256(source)
        result.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(result, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_v3_manifest_requires_all_seven_lanes(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        manifest = json.loads(self.ctx.live_evidence_path.read_text(encoding="utf-8"))
        del manifest["lanes"]["failure-routing"]
        self.ctx.live_evidence_path.write_text(json.dumps(manifest), encoding="utf-8")
        os.chmod(self.ctx.live_evidence_path, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_no_change_all_pass_manifest_fails_closed(self) -> None:
        self.write_manifest(all_lanes=True)
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_stale_live_evidence_fails_closed(self) -> None:
        self.write_manifest(verification_id="0" * 64)
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_alternate_live_baseline_fails_closed(self) -> None:
        (self.selected / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "later allowed change")
        later = git(self.project, "rev-parse", "HEAD")
        self.write_manifest(baseline_head=later, final_head=later)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_out_of_scope_committed_change_fails_closed(self) -> None:
        sibling = self.project / "services" / "unrelated" / "value.py"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("OUTSIDE = True\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "outside selected scope")
        final = git(self.project, "rev-parse", "HEAD")
        self.write_manifest(final_head=final)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_committed_private_state_fails_closed(self) -> None:
        private = self.selected / ".codex" / "current-state.json"
        private.parent.mkdir(parents=True)
        private.write_text("{}\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "invalid private state")
        final = git(self.project, "rev-parse", "HEAD")
        self.write_manifest(final_head=final)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_deleted_out_of_scope_history_fails_closed(self) -> None:
        sibling = self.project / "services" / "unrelated" / "value.py"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("OUTSIDE = True\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "temporary outside change")
        sibling.unlink()
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "remove outside change")
        self.write_manifest(final_head=git(self.project, "rev-parse", "HEAD"))
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_deleted_private_state_history_fails_closed(self) -> None:
        private = self.selected / ".codex" / "current-state.json"
        private.parent.mkdir(parents=True)
        private.write_text("{}\n", encoding="utf-8")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "temporary private state")
        private.unlink()
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "remove private state")
        self.write_manifest(final_head=git(self.project, "rev-parse", "HEAD"))
        self.assertIsNone(verifier.load_live_results(self.ctx))

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_symlinked_live_manifest_fails_closed(self) -> None:
        self.write_manifest()
        target = self.verification_root / "live-results-target.json"
        self.ctx.live_evidence_path.replace(target)
        self.ctx.live_evidence_path.symlink_to(target)
        self.assertIsNone(verifier.load_live_results(self.ctx))
        self.assertEqual(verifier.final_status(self.ctx), "FAIL")

    def test_invalid_utf8_live_manifest_fails_closed(self) -> None:
        self.write_manifest()
        self.ctx.live_evidence_path.write_bytes(b"\xff")
        os.chmod(self.ctx.live_evidence_path, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_invalid_utf8_context_fails_closed(self) -> None:
        self.write_manifest()
        context = self.verification_root / "verification-context.json"
        context.write_bytes(b"\xff")
        os.chmod(context, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_invalid_utf8_context_during_setup_writes_fail_report(self) -> None:
        context = self.verification_root / "verification-context.json"
        context.write_bytes(b"\xff")
        os.chmod(context, 0o600)
        verifier.setup_disposable_project(self.ctx)
        report = self.verification_root / "report.md"
        verifier.write_report(self.ctx, report)
        self.assertEqual(context.read_bytes(), b"\xff")
        self.assertTrue(any(check.status == "FAIL" for check in self.ctx.checks))
        self.assertIn("invalid UTF-8", report.read_text(encoding="utf-8"))

    def test_partial_evidence_must_use_its_lane_directory(self) -> None:
        self.commit_selected_change()
        self.write_manifest()
        wrong_relative = Path("evidence") / "other" / "result.json"
        wrong = self.verification_root / wrong_relative
        wrong.parent.mkdir(parents=True)
        wrong.write_text('{"result":"partial"}\n', encoding="utf-8")
        os.chmod(wrong, 0o600)
        value = json.loads(self.ctx.live_evidence_path.read_text(encoding="utf-8"))
        value["lanes"]["golden-path"] = {
            "status": "PARTIAL",
            "evidence": [wrong_relative.as_posix()],
        }
        self.ctx.live_evidence_path.write_text(
            json.dumps(value) + "\n", encoding="utf-8"
        )
        os.chmod(self.ctx.live_evidence_path, 0o600)
        self.assertIsNone(verifier.load_live_results(self.ctx))

    def test_capability_matrix_isolates_unrelated_warnings(self) -> None:
        self.ctx.add(
            "Capability regression results",
            "Public contract",
            "PASS",
            "passed",
            capability_id="public.interface",
        )
        self.ctx.add(
            "Live workflow results",
            "Golden path",
            "WARN",
            "missing",
            capability_id="live.golden-path",
        )
        matrix = dict(verifier.summarize_matrix(self.ctx))
        self.assertEqual(matrix["Public two-command interface"], "PASS")
        self.assertEqual(matrix["Golden-path live run"], "PARTIAL")

    def test_report_is_private(self) -> None:
        report = self.verification_root / "report.md"
        verifier.write_report(self.ctx, report)
        self.assertTrue(report.is_file())
        if os.name == "posix":
            self.assertEqual(report.stat().st_mode & 0o077, 0)

    def test_verification_root_is_private(self) -> None:
        self.assertIsNone(
            verifier.prepare_verification_root(self.ctx, self.verification_root)
        )
        verifier.setup_disposable_project(self.ctx)
        if os.name == "posix":
            self.assertEqual(self.verification_root.stat().st_mode & 0o077, 0)

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_symlinked_verification_root_is_rejected_before_chmod(self) -> None:
        target = self.root / "verification-target"
        target.mkdir()
        target.chmod(0o755)
        linked = self.root / "verification-link"
        linked.symlink_to(target, target_is_directory=True)
        linked_ctx = replace(self.ctx, verification_root=target.resolve())
        before = target.stat().st_mode & 0o777
        self.assertIsNotNone(verifier.verification_root_problem(linked_ctx, linked))
        self.assertIsNone(
            verifier.private_output_path(
                linked / "report.md",
                target.resolve(),
                requested_root=linked,
            )
        )
        self.assertEqual(target.stat().st_mode & 0o777, before)

    def test_flat_fixture_migrates_cleanly_to_nested_scope(self) -> None:
        shutil.rmtree(self.project)
        self.project.mkdir(parents=True)
        old_files = {
            "README.md": "# Disposable SDLC Verification Project\n",
            "pyproject.toml": (
                '[project]\nname = "sdlc-verification-project"\nversion = "0.0.0"\n'
            ),
            "src/resource_name.py": '"""Disposable verification module."""\n',
            "tests/test_resource_name.py": "def test_placeholder():\n    assert True\n",
            "docs/.gitkeep": "",
        }
        for relative, content in old_files.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "config", "user.name", "Verifier Test")
        git(self.project, "config", "user.email", "verifier@example.invalid")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "old flat fixture")
        verifier.setup_disposable_project(self.ctx)
        self.assertEqual(git(self.project, "status", "--porcelain"), "")
        self.assertTrue((self.selected / "pyproject.toml").is_file())
        self.assertFalse((self.project / "pyproject.toml").exists())

    def test_dirty_fixture_fails_without_mutation(self) -> None:
        unknown = self.project / "unknown.txt"
        unknown.write_text("preserve me\n", encoding="utf-8")
        verifier.setup_disposable_project(self.ctx)
        self.assertEqual(unknown.read_text(encoding="utf-8"), "preserve me\n")
        failures = [check for check in self.ctx.checks if check.status == "FAIL"]
        self.assertTrue(failures)

    def test_unknown_clean_repository_fails_without_mutation(self) -> None:
        unknown = self.verification_root / "unknown-project"
        selected = unknown / "services" / "resource-validator"
        selected.mkdir(parents=True)
        sentinel = unknown / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        git(unknown, "init", "-q", "-b", "main")
        git(unknown, "config", "user.name", "Verifier Test")
        git(unknown, "config", "user.email", "verifier@example.invalid")
        git(unknown, "add", "-A")
        git(unknown, "commit", "-qm", "unowned repository")
        head = git(unknown, "rev-parse", "HEAD")
        unknown_ctx = replace(
            self.ctx,
            disposable_project=unknown,
            selected_project=selected,
        )
        verifier.setup_disposable_project(unknown_ctx)
        self.assertEqual(git(unknown, "rev-parse", "HEAD"), head)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse((unknown / verifier.DISPOSABLE_FIXTURE_MARKER).exists())
        self.assertTrue(any(check.status == "FAIL" for check in unknown_ctx.checks))

    def test_unknown_non_git_directory_fails_without_mutation(self) -> None:
        unknown = self.verification_root / "unknown-directory"
        unknown.mkdir()
        sentinel = unknown / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        unknown_ctx = replace(
            self.ctx,
            disposable_project=unknown,
            selected_project=unknown / "services" / "resource-validator",
        )
        verifier.setup_disposable_project(unknown_ctx)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse((unknown / ".git").exists())
        self.assertFalse((unknown / verifier.DISPOSABLE_FIXTURE_MARKER).exists())
        self.assertTrue(any(check.status == "FAIL" for check in unknown_ctx.checks))

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_symlinked_disposable_project_fails_without_target_mutation(self) -> None:
        outside = self.root / "real-project"
        outside.mkdir()
        git(outside, "init", "-q", "-b", "main")
        git(outside, "config", "user.name", "Verifier Test")
        git(outside, "config", "user.email", "verifier@example.invalid")
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        git(outside, "add", "-A")
        git(outside, "commit", "-qm", "sentinel")
        head = git(outside, "rev-parse", "HEAD")
        shutil.rmtree(self.project)
        self.project.symlink_to(outside, target_is_directory=True)
        verifier.setup_disposable_project(self.ctx)
        self.assertEqual(git(outside, "rev-parse", "HEAD"), head)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertTrue(any(check.status == "FAIL" for check in self.ctx.checks))

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_symlinked_services_component_fails_without_target_mutation(self) -> None:
        outside = self.root / "outside-services"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")
        shutil.rmtree(self.project / "services")
        (self.project / "services").symlink_to(outside, target_is_directory=True)
        head = git(self.project, "rev-parse", "HEAD")
        verifier.setup_disposable_project(self.ctx)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), head)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertTrue(any(check.status == "FAIL" for check in self.ctx.checks))

    def test_regular_file_fixture_parent_fails_without_mutation(self) -> None:
        services = self.project / "services"
        shutil.rmtree(services)
        services.write_text("preserve\n", encoding="utf-8")
        head = git(self.project, "rev-parse", "HEAD")
        verifier.setup_disposable_project(self.ctx)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), head)
        self.assertEqual(services.read_text(encoding="utf-8"), "preserve\n")
        self.assertTrue(any(check.status == "FAIL" for check in self.ctx.checks))

    def test_run_timeout_becomes_failure_result(self) -> None:
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["test"], timeout=1),
        ):
            result = verifier.run(["test"], timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertNotIn("test", result.stderr)

    def test_tree_digest_ignores_install_provenance(self) -> None:
        skill = self.root / "sample-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("contract\n", encoding="utf-8")
        before = verifier.tree_digest(skill)
        (skill / ".install-source-id").write_text("local source\n", encoding="utf-8")
        self.assertEqual(verifier.tree_digest(skill), before)

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_tree_digest_rejects_symlinks(self) -> None:
        skill = self.root / "symlinked-skill"
        skill.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("private value\n", encoding="utf-8")
        (skill / "linked.txt").symlink_to(outside)
        self.assertEqual(verifier.tree_digest(skill), "unsafe-symlink:linked.txt")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_tree_digest_rejects_symlinked_root(self) -> None:
        target = self.root / "skill-target"
        target.mkdir()
        (target / "SKILL.md").write_text("contract\n", encoding="utf-8")
        linked = self.root / "linked-skill"
        linked.symlink_to(target, target_is_directory=True)
        self.assertEqual(verifier.tree_digest(linked), "unsafe-symlink:.")

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_file_digest_rejects_symlinked_hook_payload(self) -> None:
        target = self.root / "hook-target.py"
        target.write_text("print('safe fixture')\n", encoding="utf-8")
        linked = self.root / "hook.py"
        linked.symlink_to(target)
        self.assertEqual(verifier.file_digest(linked), "unsafe-symlink")

    def test_report_path_must_stay_under_verification_root(self) -> None:
        inside = self.verification_root / "reports" / "report.md"
        outside = self.root / "outside-report.md"
        self.assertEqual(
            verifier.private_output_path(inside, self.verification_root),
            inside.resolve(strict=False),
        )
        self.assertIsNone(verifier.private_output_path(outside, self.verification_root))

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_report_path_rejects_symlinked_parent(self) -> None:
        target = self.verification_root / "real-reports"
        target.mkdir()
        linked = self.verification_root / "reports"
        linked.symlink_to(target, target_is_directory=True)
        self.assertIsNone(
            verifier.private_output_path(linked / "report.md", self.verification_root)
        )

    @unittest.skipUnless(os.name == "posix", "symlink safety requires POSIX")
    def test_report_path_rejects_symlinked_file(self) -> None:
        target = self.verification_root / "target-report.md"
        target.write_text("preserve\n", encoding="utf-8")
        linked = self.verification_root / "report.md"
        linked.symlink_to(target)
        self.assertIsNone(verifier.private_output_path(linked, self.verification_root))

    def test_report_path_rejects_regular_file_parent(self) -> None:
        parent = self.verification_root / "not-a-directory"
        parent.write_text("preserve\n", encoding="utf-8")
        self.assertIsNone(
            verifier.private_output_path(parent / "report.md", self.verification_root)
        )

    def test_report_path_rejects_existing_directory_target(self) -> None:
        target = self.verification_root / "report.md"
        target.mkdir()
        self.assertIsNone(verifier.private_output_path(target, self.verification_root))

    def test_malformed_hooks_json_is_a_failure(self) -> None:
        self.ctx.codex_home.mkdir()
        (self.ctx.codex_home / "hooks.json").write_text("{bad\n", encoding="utf-8")
        verifier.check_hook_config(self.ctx)
        self.assertTrue(
            any(
                check.status == "FAIL"
                and check.capability_id == "hooks.registration"
                and "malformed" in check.detail
                for check in self.ctx.checks
            )
        )

    def test_semantically_malformed_hook_entry_is_a_failure(self) -> None:
        self.ctx.codex_home.mkdir()
        value = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "prompt",
                                "command": "python3 /tmp/not-a-command-hook.py",
                            }
                        ]
                    }
                ]
            }
        }
        (self.ctx.codex_home / "hooks.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        verifier.check_hook_config(self.ctx)
        malformed = [
            check for check in self.ctx.checks if check.name == "Hook source hooks.json"
        ]
        self.assertEqual([check.status for check in malformed], ["FAIL"])
        self.assertEqual(
            [check.capability_id for check in malformed], ["hooks.registration"]
        )
        self.assertIn(
            ("Hook registration", "FAIL"), verifier.summarize_matrix(self.ctx)
        )

    def test_toml_hook_state_metadata_is_not_an_event(self) -> None:
        self.ctx.codex_home.mkdir()
        (self.ctx.codex_home / "config.toml").write_text(
            '[hooks.state]\n"hooks.json:pre_tool_use:0:0" = { decision = "allow" }\n',
            encoding="utf-8",
        )
        verifier.check_hook_config(self.ctx)
        source = [
            check
            for check in self.ctx.checks
            if check.name == "Hook source config.toml"
        ]
        self.assertEqual([check.status for check in source], ["PASS"])

    def test_wrong_hook_entrypoint_path_is_a_failure(self) -> None:
        self.ctx.codex_home.mkdir()
        value = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /tmp/pre_tool_use_sdlc_policy.py",
                            }
                        ]
                    }
                ]
            }
        }
        (self.ctx.codex_home / "hooks.json").write_text(
            json.dumps(value), encoding="utf-8"
        )
        verifier.check_hook_config(self.ctx)
        registration = [
            check
            for check in self.ctx.checks
            if check.name == "PreToolUse SDLC hook configured"
        ]
        self.assertEqual([check.status for check in registration], ["FAIL"])
        self.assertFalse(
            any(
                check.name == "Configured hook payload parity"
                for check in self.ctx.checks
            )
        )

    def test_canonical_hook_template_entrypoint_is_accepted(self) -> None:
        expected = self.ctx.codex_home / "hooks" / "pre_tool_use_sdlc_policy.py"
        command = (
            'python3 "${CODEX_HOME:-$HOME/.codex}/hooks/pre_tool_use_sdlc_policy.py"'
        )
        self.assertTrue(
            verifier.hook_command_targets(
                command, expected, codex_home=self.ctx.codex_home
            )
        )

    def test_canonical_hook_path_as_later_argument_is_rejected(self) -> None:
        expected = self.ctx.codex_home / "hooks" / "pre_tool_use_sdlc_policy.py"
        command = f"python3 /tmp/wrapper.py {expected}"
        self.assertFalse(
            verifier.hook_command_targets(
                command, expected, codex_home=self.ctx.codex_home
            )
        )

    def test_shell_wrapped_or_extended_hook_commands_are_rejected(self) -> None:
        expected = self.ctx.codex_home / "hooks" / "pre_tool_use_sdlc_policy.py"
        commands = (
            f"bash -c python3 {expected}",
            f'python3 {expected} "$(touch /tmp/unexpected)"',
            f"python3 {expected}\npython3 /tmp/unexpected.py",
            f"python3 {expected} --unexpected",
            f"/tmp/python3 {expected}",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(
                    verifier.hook_command_targets(
                        command, expected, codex_home=self.ctx.codex_home
                    )
                )

    def test_unittest_result_parser_does_not_count_skips_as_passes(self) -> None:
        passed, skipped = verifier.parse_unittest_results(
            "\x1b[32mtest_pass\x1b[0m (Suite.test_pass) ... \x1b[32mok\x1b[0m\n"
            "test_skip (Suite.test_skip) ... skipped 'POSIX only'\n"
        )
        self.assertEqual(passed, {"test_pass"})
        self.assertEqual(skipped, {"test_skip"})

    def test_live_results_schema_lane_paths_match_runtime(self) -> None:
        schema_path = MODULE_PATH.parents[1] / "assets" / "live-results.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        lane_properties = schema["properties"]["lanes"]["properties"]
        self.assertEqual(set(lane_properties), set(verifier.LIVE_LANES))
        skill_properties = schema["properties"]["skills"]["properties"]
        self.assertEqual(set(skill_properties), set(verifier.REQUIRED_SDLC_SKILLS))
        self.assertEqual(
            set(schema["properties"]["skills"]["required"]),
            set(verifier.REQUIRED_SDLC_SKILLS),
        )
        for lane in verifier.LIVE_LANES:
            self.assertTrue(
                verifier.valid_lane_evidence_path(lane, f"evidence/{lane}/result.json")
            )
            for invalid in (
                f"evidence/{lane}/../other/result.json",
                f"evidence/{lane}//result.json",
                f"evidence/{lane}/./result.json",
                f"evidence/other/{lane}/result.json",
            ):
                self.assertFalse(verifier.valid_lane_evidence_path(lane, invalid))


if __name__ == "__main__":
    unittest.main()
