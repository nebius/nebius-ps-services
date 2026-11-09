"""Deterministic suite catalog, bounded execution and capability dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib.util
import re
import subprocess
import sys
import time

DEFAULT_CAPABILITY_SUITE_TIMEOUT_SECONDS = 120
CAPABILITY_SUITE_TIMEOUT_SECONDS = {
    "worktree": 900,
    "execution": 900,
    "three-tier": 900,
    "task-waves": 900,
    # Real owned-origin setup measured 259s for the verifier aggregate.
    "verifier": 600,
    # Complete both-host hook regression run measured 214s.
    "hooks": 600,
    # Unchanged dispatch suite measured 259 seconds on the qualification host.
    "dispatch": 600,
}


def parse_unittest_results(output: str) -> tuple[set[str], set[str]]:
    passed: set[str] = set()
    skipped: set[str] = set()
    pattern = re.compile(r"^(test_[A-Za-z0-9_]+).*\.\.\.\s+(ok|skipped\b.*)$")
    ansi_escape = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    for line in output.splitlines():
        match = pattern.match(ansi_escape.sub("", line).strip())
        if not match:
            continue
        name, outcome = match.groups()
        if outcome == "ok":
            passed.add(name)
        else:
            skipped.add(name)
    return passed, skipped


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    command: list[str]
    cwd: Path
    timeout: int


@dataclass(frozen=True)
class SuiteResult:
    name: str
    returncode: int
    elapsed: float
    passed: frozenset[str]
    skipped: frozenset[str]
    failed: tuple[str, ...]

    @property
    def outcome(self) -> str:
        return {
            0: "PASS",
            124: "TIMEOUT",
            127: "START_FAILED",
            125: "EXECUTION_UNPROVEN",
            130: "INTERRUPTED",
        }.get(self.returncode, "FAIL")

    @property
    def detail(self) -> str:
        result = (
            f"{self.name} (exit {self.returncode}; {self.outcome}; {self.elapsed:.1f}s"
        )
        if self.failed:
            result += "; failing tests: " + ", ".join(self.failed[:20])
            if len(self.failed) > 20:
                result += f"; {len(self.failed) - 20} more"
        return result + ")"


def suite_catalog(skills_root: Path) -> dict[str, SuiteSpec]:
    suites = {
        "protected-merge": (
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-v",
                "-s",
                str(skills_root / "merge-pr/scripts"),
                "-p",
                "test_merge*.py",
            ],
            skills_root,
        ),
        "project-specs": (
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-v",
                "-s",
                str(skills_root / "maintain-project-specs" / "scripts"),
                "-p",
                "test_project_specs*.py",
            ],
            skills_root,
        ),
        "objective": (
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-v",
                "-s",
                str(skills_root / "sdlc-start/scripts"),
                "-p",
                "test_*.py",
            ],
            skills_root,
        ),
        "execution": (
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                # Dispatch and task-wave contracts have their own suites below.
                # Do not run those expensive real-Git suites twice.
                *(
                    str(skills_root / "worktree/scripts" / name)
                    for name in (
                        "test_workflow_execution.py",
                        "test_workflow_waves.py",
                        "test_workflow_design_revision.py",
                        "test_workflow_objectives.py",
                        "test_workflow_spec_bootstrap.py",
                        "test_workflow_program.py",
                        "test_workflow_qualification.py",
                        "test_workflow_delivery.py",
                        "test_workflow_handoffs.py",
                        "test_workflow_security.py",
                        "test_workflow_fixture.py",
                    )
                ),
            ],
            skills_root,
        ),
        "dispatch": (
            [sys.executable, "worktree/scripts/test_workflow_dispatch.py", "-v"],
            skills_root,
        ),
        "worktree": (
            [sys.executable, "scripts/test-worktree-manager.py", "-v"],
            skills_root / "worktree",
        ),
        "git-promotion": (
            [sys.executable, "scripts/test-git-promotion.py", "-v"],
            skills_root / "worktree",
        ),
        "implement": (
            [sys.executable, "implement/scripts/test_implement_contract.py", "-v"],
            skills_root,
        ),
        "task-waves": (
            [sys.executable, "worktree/scripts/test_workflow_contracts.py", "-v"],
            skills_root,
        ),
        "hooks": (
            [
                sys.executable,
                str(
                    skills_root
                    / "sdlc-start"
                    / "assets"
                    / "hooks"
                    / "tests"
                    / "test_sdlc_hooks.py"
                ),
                "-v",
            ],
            skills_root,
        ),
        "observability": (
            [
                sys.executable,
                str(
                    skills_root
                    / "sdlc-evaluate"
                    / "scripts"
                    / "test_observability_contract.py"
                ),
                "-v",
            ],
            skills_root,
        ),
        "repair-control": (
            [
                sys.executable,
                str(
                    skills_root
                    / "sdlc-classify-failure"
                    / "scripts"
                    / "test_repair_control.py"
                ),
                "-v",
            ],
            skills_root,
        ),
        "evaluation-failure": (
            [
                sys.executable,
                str(
                    skills_root
                    / "sdlc-evaluate"
                    / "scripts"
                    / "test_failure_contract.py"
                ),
                "-v",
            ],
            skills_root,
        ),
        "corrective-plan": (
            [
                sys.executable,
                str(
                    skills_root
                    / "sdlc-create-plan"
                    / "scripts"
                    / "test_corrective_plan_contract.py"
                ),
                "-v",
            ],
            skills_root,
        ),
        "troubleshoot-contract": (
            [
                sys.executable,
                str(
                    skills_root / "troubleshoot" / "scripts" / "test_skill_contract.py"
                ),
                "-v",
            ],
            skills_root,
        ),
        "verifier": (
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                str(
                    skills_root
                    / "sdlc-workflow-test"
                    / "scripts"
                    / "test_verify_agentic_sdlc.py"
                ),
                str(
                    skills_root
                    / "sdlc-workflow-test"
                    / "scripts"
                    / "test_collect_live_evidence.py"
                ),
                str(
                    skills_root
                    / "sdlc-workflow-test/scripts/test_verification_suites.py"
                ),
                str(
                    skills_root
                    / "sdlc-workflow-test/scripts/test_verification_workspace.py"
                ),
                str(
                    skills_root / "sdlc-workflow-test/scripts/test_three_tier_native.py"
                ),
            ],
            skills_root,
        ),
        "three-tier": (
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "sdlc-workflow-test/scripts/test_three_tier_objective.py",
                "sdlc-workflow-test/scripts/test_three_tier_browser.py",
                "sdlc-workflow-test/scripts/test_three_tier_lifecycle.py",
                "sdlc-workflow-test/scripts/test_three_tier_runtime.py",
                "sdlc-workflow-test/scripts/test_three_tier_recovery.py",
                "sdlc-workflow-test/scripts/test_owned_git_origin.py",
                "sdlc-workflow-test/scripts/test_git_environment.py",
                "sdlc-workflow-test/scripts/test_three_tier_target.py",
                "sdlc-workflow-test/scripts/test_verification_catalog.py",
            ],
            skills_root,
        ),
    }
    return {
        name: SuiteSpec(
            name,
            command,
            cwd,
            CAPABILITY_SUITE_TIMEOUT_SECONDS.get(
                name, DEFAULT_CAPABILITY_SUITE_TIMEOUT_SECONDS
            ),
        )
        for name, (command, cwd) in suites.items()
    }


def capability_catalog() -> dict[str, tuple[str, tuple[tuple[str, str], ...]]]:
    return {
        "source.obligations": (
            "Complete document-package obligation coverage",
            tuple(
                ("execution", test)
                for test in (
                    "test_large_package_has_complete_canonical_coverage",
                    "test_missing_catalog_id_is_not_compressed_away",
                    "test_cannot_opt_out_of_catalog_extraction",
                    "test_source_drift_conflicts_and_missing_references_fail",
                    "test_malformed_and_unmapped_dispositions_fail",
                )
            ),
        ),
        "program.phase-slices": (
            "Phase barriers and cross-feature delivery slices",
            tuple(
                ("execution", test)
                for test in (
                    "test_external_phase_and_full_obligation_plan",
                    "test_future_slice_and_phase_dependencies_rejected",
                    "test_all_source_obligations_remain_scheduled",
                    "test_phase_only_exit_without_a_runner_route_is_rejected",
                )
            ),
        ),
        "program.source-admission": (
            "Exact portable source and canonical pair admission",
            tuple(
                ("execution", test)
                for test in (
                    "test_fresh_input_admission_stages_exact_snapshot_and_pair",
                    "test_initial_pair_is_committed_once_before_execution",
                    "test_document_program_contract_includes_exact_admitted_bundle",
                    "test_contract_recovers_commit_before_state_write",
                    "test_managed_commit_recovery_records_head_before_reconciliation",
                    "test_managed_intake_owns_source_before_authoring",
                    "test_publication_race_cannot_commit_unpublished_bytes",
                    "test_clean_unjournaled_contract_commit_is_rejected",
                )
            ),
        ),
        "program.completion-freshness": (
            "Immutable delivered slices and current obligation evidence",
            tuple(
                ("execution", test)
                for test in (
                    "test_completed_slice_cannot_acquire_new_obligations_or_move",
                    "test_full_completion_requires_current_evidence_beyond_phase_exit_subset",
                    "test_transitive_phase_barrier_and_changed_source_invalidate_exit",
                )
            ),
        ),
        "execution.bounded-scale": (
            "Large task graphs and bounded cross-feature batches",
            tuple(
                ("execution", test)
                for test in (
                    "test_more_than_two_thousand_tasks_in_both_chain_orders",
                    "test_fanout_fanin_bounded_batches_and_cross_feature_mapping",
                    "test_cycles_and_conflicting_claims",
                    "test_four_retained_worktrees_then_next_batch_uses_validated_tip",
                    "test_large_fan_in_is_indexed_and_tampering_is_not_repaired",
                )
            ),
        ),
        "three-tier.harness": (
            "Three-tier objective and lifecycle harness",
            (
                (
                    "three-tier",
                    "test_aggregate_profile_resolves_its_owned_lifecycle_and_independent_identity",
                ),
                ("three-tier", "test_cleanup_signals_only_owned_runner_and_chrome"),
                ("three-tier", "test_cleanup_refuses_reused_pid"),
                (
                    "three-tier",
                    "test_recovery_preserves_interrupted_attempt_and_is_idempotent",
                ),
                (
                    "three-tier",
                    "test_browser_stages_reject_out_of_order_and_failed_trial",
                ),
                (
                    "three-tier",
                    "test_fresh_post_restart_capture_may_match_persisted_pixels",
                ),
                ("three-tier", "test_browser_checkpoint_correlates_api_and_database"),
                (
                    "three-tier",
                    "test_browser_stage_rejects_stale_deployment_before_launch",
                ),
                (
                    "three-tier",
                    "test_deployment_rechecks_running_image_mounts_and_endpoint",
                ),
                (
                    "three-tier",
                    "test_owned_build_records_image_and_rejects_failed_rebuild",
                ),
                (
                    "three-tier",
                    "test_owned_restart_refreshes_dynamic_port_before_deployment_check",
                ),
                (
                    "three-tier",
                    "test_restart_endpoint_refresh_rejects_unowned_or_public_binding",
                ),
                (
                    "three-tier",
                    "test_build_model_rejects_external_context_and_web_mounts",
                ),
                (
                    "three-tier",
                    "test_fresh_specs_admit_without_early_commit_and_target_real_integration",
                ),
                ("three-tier", "test_distinct_catalog_owner_isolated_and_exact_basis_preserved"),
                ("three-tier", "test_catalog_failure_precedes_new_run_effects_and_replacement_cleanup"),
                ("three-tier", "test_catalog_binding_missing_tampered_and_wrong_native_installation_rejected"),
                ("three-tier", "test_validate_results_is_read_only_and_rejects_failed_validations"),
                ("three-tier", "test_result_validation_subprocess_reads_under_parent_workspace_lock"),
                (
                    "three-tier",
                    "test_target_rejects_foreign_checkpoint_and_unregistered_worktree",
                ),
                (
                    "three-tier",
                    "test_completed_execution_targets_exact_promoted_checkout",
                ),
                (
                    "three-tier",
                    "test_owned_origin_is_idempotent_and_remains_frozen_after_project_commit",
                ),
                (
                    "three-tier",
                    "test_remote_and_hook_configuration_tampering_is_rejected",
                ),
                (
                    "three-tier",
                    "test_inherited_filter_is_isolated_before_baseline_and_origin_mutation",
                ),
                (
                    "three-tier",
                    "test_inherited_transport_is_isolated_and_environment_overrides_are_rejected",
                ),
                ("three-tier", "test_interrupted_receipt_publication_is_not_adopted"),
                ("three-tier", "test_preparation_isolates_inherited_git_filters"),
                ("three-tier", "test_profile_tampering_and_missing_files_fail_closed"),
                (
                    "three-tier",
                    "test_caller_overrides_are_not_masked_by_a_valid_profile",
                ),
                (
                    "three-tier",
                    "test_local_executable_and_inactive_include_configuration_is_rejected",
                ),
                ("three-tier", "test_workflow_worker_scope_and_runtime_propagation"),
                ("three-tier", "test_commit_owner_and_hook_admit_only_the_owned_scope"),
                (
                    "three-tier",
                    "test_native_launcher_validates_and_passes_profile_to_fresh_process",
                ),
                (
                    "three-tier",
                    "test_tampering_blocks_cleanup_before_resource_operations",
                ),
                (
                    "three-tier",
                    "test_nested_foreign_bare_repository_is_not_owned_scope",
                ),
                (
                    "three-tier",
                    "test_disabled_origin_guard_blocks_project_worker_and_origin_commands",
                ),
                ("three-tier", "test_removed_origin_blocks_sealed_profile_admission"),
                (
                    "three-tier",
                    "test_common_directory_redirects_and_borrowed_storage_are_rejected",
                ),
                (
                    "three-tier",
                    "test_nested_common_directory_without_objects_is_not_owned_scope",
                ),
                ("three-tier", "test_global_attributes_do_not_enter_owned_checkout"),
                ("three-tier", "test_promotion_owner_revalidates_profile_before_git"),
                ("three-tier", "test_sdlc_hook_git_reads_reject_tampered_profile"),
                (
                    "three-tier",
                    "test_rendered_objective_has_no_managed_frontmatter",
                ),
                ("three-tier", "test_renderer_rejects_compose_identity_drift"),
                (
                    "three-tier",
                    "test_prepare_public_images_uses_private_config_and_fixed_images",
                ),
                (
                    "three-tier",
                    "test_named_product_failures",
                ),
                ("three-tier", "test_inspection_failures_are_distinct_and_bounded"),
                (
                    "three-tier",
                    "test_unexpected_observer_exception_never_leaks_exception_text",
                ),
                (
                    "three-tier",
                    "test_worker_observations_are_development_only_and_unknown_failure_stays_blocked",
                ),
                (
                    "three-tier",
                    "test_failed_receipt_orphan_is_reconciled_before_retry_and_cleanup",
                ),
                (
                    "three-tier",
                    "test_cleanup_resumes_after_raw_observation_was_deleted",
                ),
                (
                    "three-tier",
                    "test_tampered_observation_blocks_admission_and_cleanup",
                ),
                (
                    "three-tier",
                    "test_phase_record_cannot_overwrite_browser_failure",
                ),
                (
                    "three-tier",
                    "test_headless_browser_contract_is_mirrored",
                ),
                ("three-tier", "test_semantic_rejects_out_of_order_gui_steps"),
                (
                    "three-tier",
                    "test_semantic_rejects_duplicate_test_evidence_content",
                ),
            ),
        ),
        "evaluation.observability-contract": (
            "Bounded observability evaluation contract",
            (
                (
                    "observability",
                    "test_observability_is_limited_to_predefined_operational_criteria",
                ),
                (
                    "observability",
                    "test_production_read_and_experiment_boundaries_are_distinct",
                ),
                (
                    "observability",
                    "test_inconclusive_is_a_first_class_criterion_result",
                ),
                (
                    "observability",
                    "test_template_records_bounded_observability_provenance",
                ),
                (
                    "observability",
                    "test_eval_cases_require_zero_call_admission_and_sequential_queries",
                ),
            ),
        ),
        "repair.control-contract": (
            "Evidence-guided repair-control contract",
            (
                (
                    "execution",
                    "test_integrated_failed_wave_correction_preserves_failure_and_blocks_bypass",
                ),
                (
                    "execution",
                    "test_failed_wave_cleanup_refusal_and_retry_preserve_original_evidence",
                ),
                (
                    "execution",
                    "test_failed_wave_rejects_inactive_or_tampered_dispatch_without_cleanup",
                ),
                (
                    "repair-control",
                    "test_ambiguous_evaluation_routes_to_troubleshoot_exactly_once",
                ),
                (
                    "repair-control",
                    "test_failed_direct_repair_requires_troubleshooting_before_attempt_two",
                ),
                (
                    "repair-control",
                    "test_no_bug_found_remains_unresolved_never_design",
                ),
                (
                    "repair-control",
                    "test_design_requires_positive_system_contract_proof",
                ),
                (
                    "evaluation-failure",
                    "test_ambiguous_failure_requires_diagnosis",
                ),
                (
                    "corrective-plan",
                    "test_valid_corrective_plan_preserves_history_and_appends_task",
                ),
                (
                    "troubleshoot-contract",
                    "test_sdlc_handoff_and_live_proof_stay_owned",
                ),
            ),
        ),
        "public.interface": (
            "Direct objective public entry points",
            (
                ("objective", "test_public_interface_is_mirrored"),
                ("implement", "test_public_interface_and_policy"),
                ("implement", "test_retired_private_commands_fail_before_mutation"),
            ),
        ),
        "objective.intake": (
            "Direct objective intake",
            (
                ("execution", "test_accept_persists_only_normalized_objective"),
                ("execution", "test_current_plan_has_explicit_provenance"),
                ("execution", "test_detached_and_unborn_git_are_rejected"),
            ),
        ),
        "spec.validation-receipt": (
            "Owner-issued specification validation and no-impact outcomes",
            (
                ("objective", "test_valid_specs_emit_current_advisory_snapshot"),
                ("objective", "test_ready_feature_with_placeholder_is_rejected"),
                ("objective", "test_unknown_requirement_mapping_is_rejected"),
                ("objective", "test_foreign_owner_marker_is_rejected"),
                ("task-waves", "test_no_impact_needs_no_spec_documents"),
                (
                    "project-specs",
                    "test_bootstrap_missing_specs_publishes_pending_pair_and_preserves_counterpart",
                ),
            ),
        ),
        "objective.revisions": (
            "Normalized objective revisions",
            (
                ("execution", "test_raw_history_keys_are_rejected"),
                ("execution", "test_snapshot_tampering_is_rejected"),
                ("execution", "test_other_objectives_are_not_queued"),
            ),
        ),
        "objective.binding": (
            "Explicit native session binding",
            (
                ("execution", "test_new_session_does_not_infer_sole_active_run"),
                ("execution", "test_restore_binding_is_read_only"),
                ("execution", "test_cross_session_requires_explicit_continue"),
            ),
        ),
        "objective.lifecycle": (
            "Coordinator initialization, steering and cancellation",
            (
                ("execution", "test_same_session_continues_without_new_revision"),
                ("execution", "test_steering_fences_dispatch_before_replanning"),
                ("execution", "test_cancellation_fences_without_git_rollback"),
                ("execution", "test_old_run_schema_is_rejected_without_conversion"),
                ("objective", "test_hook_only_history_is_preserved_by_fresh_cli"),
                (
                    "objective",
                    "test_concurrent_initialization_has_one_authoritative_snapshot",
                ),
                ("objective", "test_interrupted_initial_publication_uses_new_snapshot"),
                (
                    "objective",
                    "test_partial_initial_history_is_preserved_and_recovered",
                ),
                ("objective", "test_progressed_state_is_never_reset"),
                ("objective", "test_active_ownership_disagreement_has_no_effect"),
                ("objective", "test_managed_intake_requires_exact_active_outer_lease"),
                ("objective", "test_externally_released_outer_lease_cannot_initialize"),
            ),
        ),
        "execution.scope": (
            "Exact initialized-folder scope",
            (
                ("execution", "test_nested_project_scope_is_persisted_and_enforced"),
                (
                    "execution",
                    "test_tdd_seal_rejects_changes_outside_nested_project_scope",
                ),
                (
                    "execution",
                    "test_claim_outside_nested_project_scope_fails_before_resources",
                ),
                (
                    "execution",
                    "test_claim_crossing_gitlink_fails_before_resources",
                ),
                (
                    "execution",
                    "test_prefix_claim_containing_tracked_symlink_fails_before_resources",
                ),
            ),
        ),
        "execution.sessions-recovery": (
            "Worker sessions and interrupted recovery",
            (
                ("execution", "test_both_seals_recover_full_message_after_commit"),
                ("execution", "test_both_seals_retry_before_commit_once"),
                (
                    "execution",
                    "test_both_seals_normal_creation_uses_same_message_contract",
                ),
                (
                    "execution",
                    "test_both_seals_reject_hook_rewrite_without_publishing_or_recommitting",
                ),
                ("three-tier", "test_unsealed_tdd_rejects_changed_leading_indentation"),
                (
                    "execution",
                    "test_interrupted_worker_recovery_transfers_claimed_dirty_state",
                ),
                ("execution", "test_interrupted_worker_recovery_accepts_clean_base"),
                (
                    "execution",
                    "test_interrupted_worker_recovery_rejects_worker_created_commit",
                ),
                ("execution", "test_one_worker_session_cannot_own_two_tasks"),
                ("execution", "test_parallel_session_claim_is_atomic"),
                (
                    "execution",
                    "test_session_claim_is_complete_before_atomic_publication",
                ),
                (
                    "execution",
                    "test_failed_session_claim_publication_leaves_no_partial_claim",
                ),
                (
                    "execution",
                    "test_task_finish_recovers_commit_before_result_write",
                ),
                (
                    "execution",
                    "test_task_finish_recovers_result_before_task_state_write",
                ),
            ),
        ),
        "execution.replan": (
            "Resource-free future-wave replan",
            (
                (
                    "execution",
                    "test_future_replan_replaces_only_resource_free_planned_waves",
                ),
            ),
        ),
        "execution.secret-gate": (
            "Secret-safe result persistence",
            (
                (
                    "execution",
                    "test_finish_rejects_sensitive_staged_content_without_leaking",
                ),
            ),
        ),
        "execution.sequential-fallback": (
            "Sequential ephemeral Codex fallback",
            (
                ("dispatch", "test_dispatches_fresh_ephemeral_workers_sequentially"),
                (
                    "dispatch",
                    "test_first_failure_stops_later_dispatch_and_retains_concise_error",
                ),
                ("dispatch", "test_missing_codex_is_environment_blocker"),
                ("dispatch", "test_terminal_watch_interrupts_worker"),
                ("dispatch", "test_legacy_assignment_requires_upgrade"),
                ("dispatch", "test_watch_failure_stops_worker_process_group"),
            ),
        ),
        "execution.worker-liveness": (
            "Armed and monitored Agentic worker lifecycle",
            (
                ("execution", "test_worker_must_be_armed_before_start"),
                ("execution", "test_arm_start_heartbeat_and_watch_liveness"),
                ("execution", "test_prestart_mutation_and_timeout_fail_closed"),
                (
                    "execution",
                    "test_confirmed_prestart_requeue_allows_a_fresh_arm",
                ),
                (
                    "execution",
                    "test_prestart_scope_violations_use_scope_error",
                ),
                ("execution", "test_progress_does_not_bypass_maximum_runtime"),
                ("dispatch", "test_terminal_watch_interrupts_worker"),
            ),
        ),
        "git.promotion-safety": (
            "Verified remote-default, local-source, and exact promotion safety",
            (
                (
                    "git-promotion",
                    "test_resolves_symbolic_remote_default_without_name_guessing",
                ),
                (
                    "git-promotion",
                    "test_recorded_remote_default_head_drift_is_rejected",
                ),
                (
                    "git-promotion",
                    "test_ff_only_promotion_and_exact_branch_deletion",
                ),
                (
                    "git-promotion",
                    "test_promotion_lock_regular_file_fails_with_structured_error",
                ),
                (
                    "worktree",
                    "test_add_uses_clean_local_source_branch_and_exact_head",
                ),
            ),
        ),
        "interop.outer-lease-v4": (
            "Managed outer-worktree lease lifecycle",
            (
                ("task-waves", "test_pending_blocks_before_terminal_writes"),
                ("task-waves", "test_stale_or_missing_authoritative_proof_blocks"),
                (
                    "task-waves",
                    "test_authoritative_inspection_failure_blocks_without_terminal_writes",
                ),
                ("task-waves", "test_disappearing_managed_proof_blocks_completion"),
                (
                    "task-waves",
                    "test_matching_recorded_and_authoritative_proof_completes",
                ),
                (
                    "worktree",
                    "test_add_serializes_lifecycle_selection_and_activation",
                ),
                (
                    "worktree",
                    "test_concurrent_directory_bootstrap_revalidates_winner",
                ),
                (
                    "worktree",
                    "test_task_lease_blocks_integration_until_release",
                ),
                (
                    "worktree",
                    "test_old_manifest_and_reservation_schemas_fail_closed",
                ),
                (
                    "worktree",
                    "test_remove_deletes_terminal_lease_receipt",
                ),
                (
                    "worktree",
                    "test_remove_retry_deletes_terminal_receipt_after_git_cleanup",
                ),
                (
                    "worktree",
                    "test_internal_coordinators_do_not_call_public_lifecycle_actions",
                ),
                (
                    "execution",
                    "test_managed_outer_execution_releases_to_local_source_integration",
                ),
                (
                    "execution",
                    "test_private_interop_rejects_public_lifecycle_before_subprocess",
                ),
                (
                    "hooks",
                    "test_stop_does_not_continue_worktree_integration",
                ),
                (
                    "hooks",
                    "test_stop_does_not_continue_worktree_next_skill",
                ),
            ),
        ),
        "interop.shared-execution": (
            "Shared Worktree execution and assignment runtime ownership",
            (
                (
                    "execution",
                    "test_managed_outer_execution_releases_to_local_source_integration",
                ),
                (
                    "execution",
                    "test_private_interop_rejects_public_lifecycle_before_subprocess",
                ),
                (
                    "task-waves",
                    "test_refill_waits_for_admission_and_does_not_wait_for_peer",
                ),
                (
                    "task-waves",
                    "test_runtime_lease_requires_exact_worker_and_exclusive_identity",
                ),
            ),
        ),
        "interop.implement-promotion": (
            "Automatic Implement delivery and exact cleanup",
            (
                ("task-waves", "test_clean_nondefault_delivery_and_completion"),
                ("task-waves", "test_unknown_default_delivers_feature_checkout"),
                (
                    "task-waves",
                    "test_dirty_source_requires_dependencies_and_preserves_unrelated_content",
                ),
                (
                    "task-waves",
                    "test_cancellation_drains_writers_without_admitting_results",
                ),
            ),
        ),
        "steering.continuation": (
            "Objective-bound steering continuation",
            (
                ("hooks", "test_stop_continues_running_next_skill"),
                (
                    "hooks",
                    "test_completed_features_do_not_override_coordinator_slice_route",
                ),
                ("hooks", "test_stop_continues_for_pause_steering"),
                ("hooks", "test_stop_reconstructs_explicit_project_continuation"),
            ),
        ),
        "publication.authorization": (
            "Exact Agentic SDLC publication authorization",
            (
                (
                    "hooks",
                    "test_pretool_allows_push_with_exact_create_pr_authorization",
                ),
                (
                    "hooks",
                    "test_pretool_denies_authorized_push_with_extra_refs_or_tags",
                ),
                ("hooks", "test_pretool_denies_wrapped_authorized_push"),
                ("hooks", "test_pretool_allows_gh_pr_create_with_exact_authorization"),
                (
                    "hooks",
                    "test_pretool_denies_gh_pr_create_without_explicit_head",
                ),
                (
                    "hooks",
                    "test_pretool_denies_gh_pr_create_without_exact_base",
                ),
                (
                    "hooks",
                    "test_pretool_denies_gh_pr_create_when_remote_default_head_advanced",
                ),
                ("hooks", "test_pretool_denies_compound_gh_pr_create"),
                ("hooks", "test_pretool_guards_only_github_pr_creation_writes"),
            ),
        ),
        "merge.authorization": (
            "Protected SDLC qualification, admission and merge recovery",
            (
                (
                    "execution",
                    "test_labels_skips_wrong_artifacts_and_intervention_cannot_pass",
                ),
                (
                    "execution",
                    "test_forged_receipt_recomputed_hash_is_not_producer_admitted",
                ),
                ("execution", "test_revoked_authority_prevents_runner_launch"),
                ("execution", "test_locked_plan_drift_and_missing_criteria_block"),
                ("execution", "test_failed_trial_cannot_be_reused_as_clean_evidence"),
                ("execution", "test_revoked_authority_blocks_final_effect"),
                ("execution", "test_two_slices_and_all_supported_merge_methods"),
                (
                    "execution",
                    "test_ambiguous_submission_reconciles_without_second_effect",
                ),
                ("execution", "test_failed_result_verification_does_not_repeat_merge"),
                ("execution", "test_queue_and_base_drift_block_before_dispatch"),
                (
                    "protected-merge",
                    "test_intent_uses_the_review_validated_by_its_snapshot",
                ),
                (
                    "protected-merge",
                    "test_sdlc_evidence_change_after_approval_blocks_merge",
                ),
                ("hooks", "test_pretool_denies_gh_pr_merge_without_authorization"),
                ("hooks", "test_pretool_denies_label_authorized_gh_pr_merge"),
                (
                    "hooks",
                    "test_pretool_denies_merge_when_remote_default_head_advanced",
                ),
                (
                    "hooks",
                    "test_pretool_denies_label_authorized_merge_queue_command",
                ),
                ("hooks", "test_pretool_denies_label_authorized_pr_url_merge"),
                ("hooks", "test_pretool_denies_merge_without_exact_head_guard"),
                (
                    "hooks",
                    "test_pretool_denies_merge_command_outside_exact_authorization",
                ),
                (
                    "hooks",
                    "test_pretool_denies_merge_without_explicit_pr_target",
                ),
                (
                    "hooks",
                    "test_pretool_denies_wrapped_or_prepended_gh_pr_merge",
                ),
                ("hooks", "test_pretool_denies_nested_shell_gh_pr_merge"),
                ("hooks", "test_pretool_denies_merge_admin_bypass"),
                ("hooks", "test_pretool_denies_merge_branch_deletion"),
                ("hooks", "test_pretool_denies_compound_merge_command"),
                ("hooks", "test_pretool_denies_merge_pr_scope_mismatch"),
                ("hooks", "test_pretool_denies_merge_without_passing_readiness"),
                ("hooks", "test_pretool_denies_github_merge_mcp_in_active_sdlc"),
            ),
        ),
        "verifier.self-tests": (
            "Verifier contract self-tests",
            (
                ("verifier", "test_live_evidence_is_the_only_public_evidence_option"),
                (
                    "verifier",
                    "test_prompt_routing_check_ignores_only_the_owned_home_prefix",
                ),
                (
                    "verifier",
                    "test_hook_fixture_seed_isolates_a_distinct_source_catalog",
                ),
                (
                    "verifier",
                    "test_failed_hook_fixture_seed_becomes_sanitized_report_failure",
                ),
                (
                    "verifier",
                    "test_public_live_action_is_renamed_without_a_legacy_alias",
                ),
                ("verifier", "test_any_deterministic_failure_forces_fail"),
                ("verifier", "test_source_phase_contract_rejects_bad_source_prefix"),
                (
                    "verifier",
                    "test_source_catalog_validation_rejects_missing_reference",
                ),
                ("verifier", "test_spec_ownership_contract_accepts_canonical_sources"),
                ("verifier", "test_spec_ownership_contract_rejects_legacy_owner"),
                (
                    "verifier",
                    "test_spec_ownership_contract_rejects_missing_owner_dependency",
                ),
                (
                    "verifier",
                    "test_spec_ownership_contract_rejects_missing_phase_invariant",
                ),
                (
                    "verifier",
                    "test_spec_ownership_contract_rejects_unsafe_readme_boundary",
                ),
                (
                    "verifier",
                    "test_spec_ownership_contract_rejects_missing_template_envelope",
                ),
                ("verifier", "test_missing_live_evidence_is_partial"),
                ("verifier", "test_valid_live_evidence_is_accepted"),
                (
                    "verifier",
                    "test_generic_digest_backed_evidence_cannot_reach_pass",
                ),
                ("verifier", "test_no_change_all_pass_manifest_fails_closed"),
                ("verifier", "test_stale_live_evidence_fails_closed"),
                ("verifier", "test_alternate_live_baseline_fails_closed"),
                ("verifier", "test_out_of_scope_committed_change_fails_closed"),
                ("verifier", "test_committed_private_state_fails_closed"),
                ("verifier", "test_deleted_out_of_scope_history_fails_closed"),
                ("verifier", "test_deleted_private_state_history_fails_closed"),
                ("verifier", "test_symlinked_live_manifest_fails_closed"),
                ("verifier", "test_invalid_utf8_live_manifest_fails_closed"),
                ("verifier", "test_invalid_utf8_context_fails_closed"),
                (
                    "verifier",
                    "test_invalid_utf8_context_during_setup_writes_fail_report",
                ),
                ("verifier", "test_capability_matrix_isolates_unrelated_warnings"),
                ("verifier", "test_report_is_private"),
                ("verifier", "test_verification_root_is_private"),
                ("verifier", "test_flat_fixture_is_rejected_without_conversion"),
                ("verifier", "test_dirty_fixture_fails_without_mutation"),
                (
                    "verifier",
                    "test_symlinked_disposable_project_fails_without_target_mutation",
                ),
                (
                    "verifier",
                    "test_symlinked_services_component_fails_without_target_mutation",
                ),
                (
                    "verifier",
                    "test_regular_file_fixture_parent_fails_without_mutation",
                ),
                ("verifier", "test_run_timeout_becomes_failure_result"),
                ("verifier", "test_tree_digest_ignores_install_provenance"),
                ("verifier", "test_tree_digest_rejects_symlinks"),
                ("verifier", "test_tree_digest_rejects_symlinked_root"),
                (
                    "verifier",
                    "test_file_digest_rejects_symlinked_hook_payload",
                ),
                (
                    "verifier",
                    "test_report_path_must_stay_under_verification_root",
                ),
                ("verifier", "test_report_path_rejects_symlinked_parent"),
                ("verifier", "test_report_path_rejects_symlinked_file"),
                (
                    "verifier",
                    "test_capability_requirements_resolve_to_declared_tests",
                ),
                (
                    "verifier",
                    "test_golden_path_intake_precedes_coordinator_only_requirements",
                ),
                ("verifier", "test_collects_every_required_skill_owner"),
                ("verifier", "test_support_skill_outside_evidence_matrix_is_rejected"),
                ("verifier", "test_design_contract_accepts_current_canonical_source"),
                (
                    "verifier",
                    "test_vertical_slice_contract_accepts_current_canonical_sources",
                ),
                ("verifier", "test_vertical_slice_contract_normalizes_whitespace"),
                (
                    "verifier",
                    "test_vertical_slice_contract_rejects_missing_operational_clauses",
                ),
                (
                    "verifier",
                    "test_readiness_matrix_reports_canonical_owner_receipt_result",
                ),
                (
                    "verifier",
                    "test_shared_owner_drift_and_missing_installation_fail_parity",
                ),
                (
                    "verifier",
                    "test_document_program_tests_are_required_and_cannot_be_skipped",
                ),
                ("verifier", "test_merge_checklist_uses_protected_delivery_authority"),
                ("verifier", "test_timeout_retains_completed_test_identifiers"),
                ("verifier", "test_cleanup_drain_timeout_is_unproven_and_verifies_group_absence"),
                ("verifier", "test_cleanup_timeout_preserves_prior_results_and_stops_later_suites"),
                (
                    "verifier",
                    "test_interruption_stops_later_suites_and_preserves_prior_results",
                ),
                (
                    "verifier",
                    "test_lightweight_destroy_preserves_report_and_is_idempotent",
                ),
                ("verifier", "test_unbound_checkpoint_is_reconciled_only_when_exact"),
                ("verifier", "test_resigned_checkpoint_cannot_delete_retained_report"),
                ("verifier", "test_custom_context_bound_manifest_is_removed"),
                (
                    "verifier",
                    "test_missing_native_launcher_proof_blocks_started_product",
                ),
                (
                    "verifier",
                    "test_source_discovery_alone_never_qualifies_native_activation",
                ),
                (
                    "verifier",
                    "test_foreign_session_and_unsupported_version_are_rejected",
                ),
                ("verifier", "test_hook_events_alone_are_not_native_proof"),
                ("verifier", "test_private_hook_dependency_drift_and_deletion_are_rejected"),
                ("verifier", "test_unterminated_hook_suffix_is_not_ignored"),
            ),
        ),
    }


def run_command(args, *, cwd, env, timeout):
    """Own descendants on every exit; progress contains no subprocess output."""
    owner_path = (
        Path(__file__).resolve().parents[2] / "worktree/scripts/workflow_process.py"
    )
    spec = importlib.util.spec_from_file_location("verifier_owned_process", owner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Owned process support is unavailable")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    group = owner.OwnedProcessGroup()
    child = None
    stdout = stderr = ""
    code = 127
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            process_group=group.id,
        )
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                code = 124
                break
            try:
                stdout, stderr = child.communicate(timeout=min(30, remaining))
                code = child.returncode
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                print(f"  suite running: {elapsed:.0f}s / {timeout}s", flush=True)
    except KeyboardInterrupt:
        code = 130
    except OSError:
        code = 127
    finally:
        try:
            group.close(grace=0.05)
            if child is not None:
                stdout, stderr = child.communicate(timeout=5)
        finally:
            group.verify_absent()
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


def execute(spec: SuiteSpec, env: dict[str, str]) -> SuiteResult:
    started = time.monotonic()
    try:
        completed = run_command(
            spec.command, cwd=spec.cwd, env=env, timeout=spec.timeout
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return SuiteResult(
            spec.name, 125, time.monotonic() - started, frozenset(), frozenset(), ()
        )
    output = (completed.stderr or "") + (completed.stdout or "")
    passed, skipped = parse_unittest_results(output)
    failed = tuple(
        sorted(
            set(
                re.findall(
                    r"^(?:FAIL|ERROR): (test_[A-Za-z0-9_]{1,120})(?:\s|$)",
                    output,
                    re.MULTILINE,
                )
            )
        )
    )
    return SuiteResult(
        spec.name,
        completed.returncode,
        time.monotonic() - started,
        frozenset(passed),
        frozenset(skipped),
        failed,
    )
