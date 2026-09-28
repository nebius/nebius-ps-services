"""Topology extension contract for the existing single-maintenance campaign."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .config_model import to_dynamic_payload
from .soperator_full_stack_upgrade import CampaignSegmentResult, SoperatorUpgradeCampaignIntent


class CampaignDeploymentHooks(Protocol):
    generation: Any

    @property
    def contract(self) -> Mapping[str, Any]: ...

    def bind_source_inventory(
        self,
        groups: Sequence[Any],
        source_component: Mapping[str, Any] | None,
        *,
        compatibility_lookup: Callable[..., Sequence[Any]],
    ) -> None: ...

    def newly_owned_ids(self) -> Mapping[str, str]: ...

    def execute(
        self,
        name: str,
        *,
        intent: SoperatorUpgradeCampaignIntent,
        assert_authority: Callable[[], object],
        config_store: Any,
        kube_env: Mapping[str, str],
        kube_context: str,
        namespace: str,
    ) -> CampaignSegmentResult: ...

    def verify_desired(
        self, *, kube_env: Mapping[str, str], documents_projection: Any = None
    ) -> Mapping[str, Any]: ...

    def final_intent(
        self, intent: SoperatorUpgradeCampaignIntent
    ) -> SoperatorUpgradeCampaignIntent: ...


class CampaignGenerationHooks:
    """Publish sealed effective bytes for either cluster ownership model."""

    cli: Any
    executor: Any
    admissions: dict[str, Any]

    def verify_desired(
        self, *, kube_env: Mapping[str, str], documents_projection: Any = None
    ) -> Mapping[str, Any]:
        # The checks owner supplies its exact phase, including partition admission.
        if documents_projection is not None:
            self.executor._checks_documents_projection = documents_projection
        return self.executor.verify_soperator_desired(kube_env=kube_env)

    def _reload(self) -> None:
        from .generated_manifest import runtime_config_from_manifest

        self.executor.manifest = self.cli.load_generated_manifest(self.executor.paths.generated_dir)
        self.executor.config = runtime_config_from_manifest(self.executor.manifest)

    def _publish(
        self, name: str, *, config_store: Any, assert_authority: Callable[[], object]
    ) -> None:
        from .deployment_state import DeploymentGeneration
        from .operation_config_authority import apply_project_generation_transition

        admission = self.admissions[name]
        frozen = DeploymentGeneration.from_payload(
            admission.application["generation"], expected_id=admission.application["generationId"]
        )
        frozen = self.executor.campaign_application_generation(
            name, frozen, config_store=config_store, assert_authority=assert_authority
        )
        paths = self.executor.paths
        staged = self.cli.staged_generated_paths(paths)
        # Replace the newly allocated empty staging directory with admitted bytes.
        staged.generated_dir.rmdir()
        frozen.materialize(staged)
        try:
            manifest = self.cli.load_generated_manifest(staged.generated_dir)
            # Project transaction owns locators relative to the final project.
            manifest["paths"] = self.executor.manifest["paths"]
            for target in manifest.get("deploy", {}).get("targets", []):
                if target.get("flux_dir"):
                    target["flux_dir"] = str(target["flux_dir"]).replace(
                        staged.generated_dir.relative_to(paths.repo_root).as_posix(),
                        paths.generated_dir.relative_to(paths.repo_root).as_posix(),
                        1,
                    )
            from .deployment_state import canonical_json
            from .generated_manifest import manifest_path_for_generated_dir

            manifest_path_for_generated_dir(staged.generated_dir).write_bytes(
                canonical_json(manifest)
            )
            plan = self.cli.build_project_generation_plan(
                final_paths=paths,
                staged_paths=staged,
                config_path=paths.config_path,
                config_content=self.cli.render_updated_source_payload(
                    to_dynamic_payload(frozen.manifest["runtime_config"])
                ),
            )

            def fence() -> None:
                assert_authority()

            apply_project_generation_transition(
                project_dir=paths.project_dir,
                config_path=paths.config_path,
                owner="soperator-upgrade",
                stage=f"deployment:{name}",
                store=config_store,
                build_plan=lambda: plan,
                assert_authority=fence,
                current_project_snapshot_sha256=lambda: self.cli.project_generation_snapshot_sha256(
                    paths
                ),
            )
        finally:
            self.cli.reset_generated_bundle(staged)
        self._reload()

    def _resource_identities(self) -> dict[str, str]:
        from .deployment_dependencies import resource_identity

        state = self.cli.terraform_show_json(
            self.executor.paths.infra_dir,
            extra_env=self.executor.runtime_env,
            initialize=False,
        )
        return {
            row["address"]: resource_identity(row.get("values"))
            for row in self.cli._terraform_state_resources(state)
        }

    def _dependencies(self, stage_name: str, intent: Any) -> tuple[Any, tuple[Any, ...]]:
        from .deployment_dependencies import DependencyJournal
        from .deployment_state import digest

        admission = self.admissions[stage_name]
        references = {
            item
            for stage in self.admissions.values()
            for item in stage.prerequisite_deletions
            if item.stage == stage_name
        }
        if not admission.prerequisite_deletions and not references:
            return None, ()
        journal = DependencyJournal(
            self.executor.paths.reports_dir / "soperator-campaign-dependencies.json", intent.digest
        )
        for item in admission.prerequisite_deletions:
            predecessor = self.admissions.get(item.stage)
            if predecessor is None or digest(predecessor.terraform) != item.admission_digest:
                raise RuntimeError("Predecessor admission differs from its deletion prerequisite")
        if admission.prerequisite_deletions:
            journal.require(admission.prerequisite_deletions, self._resource_identities())
            journal.begin_creation(admission.prerequisite_deletions)
        return journal, tuple(references)


class TerraformCampaignHooks(CampaignGenerationHooks):
    """Frozen topology stages executed by the existing maintenance campaign.

    Terraform resource addresses bind group keys to provider IDs. Names are
    descriptive only and never grant ownership. The parent persists every
    child journal through the shared execution checkpoint callback.
    """

    def __init__(self, executor: Any, *, source: Mapping[str, Any], generation: Any) -> None:
        from .deployment_plan import node_groups

        self.executor = executor
        self.cli = executor.cli
        self.source = source
        self.generation = generation
        self.source_groups = node_groups(source, executor.plan.target_ref)
        self.desired_groups = node_groups(
            generation.manifest["runtime_config"], executor.plan.target_ref
        )
        self.admissions: dict[str, Any] = {}
        self._contract: dict[str, Any] = {}
        self.intent: SoperatorUpgradeCampaignIntent | None = None

    @property
    def contract(self) -> Mapping[str, Any]:
        if not self._contract:
            raise RuntimeError("Topology admission has not bound its source inventory")
        return self._contract

    def _owned_ids(self) -> dict[str, str]:
        import json
        import re

        state = self.cli.terraform_show_json(
            self.executor.paths.infra_dir,
            extra_env=self.executor.runtime_env,
            initialize=False,
        )
        modules = {
            key
            for key, value in self.cli._generated_bundle_mk8s_module_index(
                self.executor.manifest
            ).items()
            if value[1] == self.executor.plan.target_ref
        }
        result: dict[str, str] = {}
        for resource in self.cli._terraform_state_resources(state):
            address = str(resource.get("address", ""))
            if (
                resource.get("type") != "nebius_mk8s_v1_node_group"
                or self.cli._terraform_state_module_name(address) not in modules
            ):
                continue
            match = re.search(r'\[("(?:[^"\\]|\\.)*")\]$', address)
            values = resource.get("values", {})
            metadata = self.cli._state_mapping(values.get("metadata"))
            provider_id = str(metadata.get("id") or values.get("id") or "")
            if not match or not provider_id:
                raise RuntimeError("Terraform node-group ownership is incomplete")
            key = json.loads(match.group(1))
            if key in result or provider_id in result.values():
                raise RuntimeError("Terraform node-group ownership is ambiguous")
            result[key] = provider_id
        return result

    def bind_source_inventory(
        self,
        groups: Sequence[Any],
        source_component: Mapping[str, Any] | None,
        *,
        compatibility_lookup: Callable[..., Sequence[Any]],
    ) -> None:
        from .deployment_plan import DeploymentStageKind, node_groups

        if self._contract:
            # Resume inventory is validated by assert_campaign_phase_inventory;
            # never rebuild the original identity from the partially changed API.
            return
        ids = self._owned_ids()
        live_ids = [str(group.metadata.id) for group in groups]
        if (
            set(ids) != set(self.source_groups)
            or set(ids.values()) != set(live_ids)
            or len(live_ids) != len(set(live_ids))
        ):
            raise RuntimeError(
                "Provider groups differ from the exact Terraform-owned source inventory"
            )
        plan = self.executor.plan
        retirement = next(
            (stage for stage in plan.stages if stage.name is DeploymentStageKind.RETIRE), None
        )
        remaining = (
            node_groups(retirement.config, plan.target_ref) if retirement else self.source_groups
        )
        removed = set(self.source_groups) - set(remaining)
        added = set(self.desired_groups) - set(remaining)
        self._contract = {
            "generation": self.generation.identity,
            "source_group_ids": sorted(ids.values()),
            "source_ids_by_key": ids,
            "retired_group_ids": sorted(ids[key] for key in removed),
            "remaining_group_ids": sorted(ids[key] for key in remaining),
            "added_group_keys": sorted(added),
            "release_transition": plan.source_release != plan.target_release,
        }
        from .mk8s_node_groups import iter_node_groups
        from .mk8s_upgrade import find_source_mk8s_component

        component = find_source_mk8s_component(
            self.generation.manifest["runtime_config"], plan.target_ref
        )
        cluster = component["inputs"]["cluster"]
        version = str(cluster.get("k8s_version") or cluster.get("version") or "")
        templates: dict[str, Any] = {}
        for group in iter_node_groups(component["inputs"]):
            drivers = group.gpu_stack_preset if group.gpu else ""
            choices = compatibility_lookup(target_version=version, platform=group.platform)
            if not any(
                choice.os == group.os and (not drivers or choice.drivers_preset == drivers)
                for choice in choices
            ):
                raise RuntimeError(f"Desired group {group.key!r} has no supported target template")
            templates[group.key] = {
                "version": version,
                "os": group.os,
                "drivers": drivers,
                "platform": group.platform,
                "gpu": group.gpu,
                "name": group.name,
            }
        if set(templates) != set(self.desired_groups):
            raise RuntimeError("Desired topology contains disabled or invalid node groups")
        self._contract["final_templates"] = templates
        for name, admission in self.admissions.items():
            if name in {"retire", "grow", "reconcile"}:
                self._contract["final-reconcile" if name == "reconcile" else name] = {
                    "generation": admission.application["generationId"],
                }

    def newly_owned_ids(self) -> Mapping[str, str]:
        owned = self._owned_ids()
        return {
            key: owned[key]
            for key in self.contract["added_group_keys"]
            if key in owned and owned[key] not in self.contract["source_group_ids"]
        }

    def _quiescence(
        self, *, namespace: str, kube_context: str, kube_env: Mapping[str, str]
    ) -> None:
        # Admission pauses every partition and jobs are cleared by the parent.
        # Recheck the whole affected domain immediately before any capacity write.
        if self.cli._soperator_upgrade_affected_jobs(
            namespace=namespace,
            node_names=(),
            kube_context=kube_context,
            extra_env=kube_env,
            include_pending=False,
            all_jobs=True,
        ):
            raise RuntimeError("Retirement requires all running Slurm jobs to be cleared")
        if any(
            self.cli.slurm_partition_state_token(row.state) == "UP"
            for row in self.cli._soperator_upgrade_partition_state_snapshot(
                namespace=namespace, kube_context=kube_context, extra_env=kube_env
            )
        ):
            raise RuntimeError("Retirement requires every Slurm partition to remain paused")

    def _freeze_retirement(
        self,
        *,
        intent: SoperatorUpgradeCampaignIntent,
        assert_authority: Callable[[], object],
        namespace: str,
        kube_context: str,
        kube_env: Mapping[str, str],
    ) -> None:
        import json

        from .deployment_retirement import freeze_capacity, retirement_members
        from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

        stage = self.admissions["retire"].stage
        current_ids = self._owned_ids()
        affected = {key: current_ids[key] for key in stage.retired_groups if key in current_ids}
        if not affected:
            return
        sdk = self.cli.init_nebius_sdk(
            parent_id=str(self.executor.config.client_info.nebius.project_id),
            context="Deployment retirement",
            prefer_operator_auth=True,
        )
        try:
            provider = self.cli.Mk8sKubernetesVersionExecutor(sdk)
            by_id = {
                str(row.metadata.id): row for row in provider.list_node_groups(intent.cluster_id)
            }
            counts = {}
            for key, provider_id in affected.items():
                group = by_id.get(provider_id)
                if group is None:
                    raise RuntimeError("Retirement lost Terraform-owned provider group")
                status = group.status
                count = status.node_count
                if (
                    isinstance(count, bool)
                    or not isinstance(count, int)
                    or status.ready_node_count != count
                    or status.target_node_count != count
                    or status.outdated_node_count
                    or status.reconciling
                ):
                    raise RuntimeError("Retirement requires stable whole-group capacity")
                counts[key] = count

            def members() -> Any:
                result = self.cli._run_soperator_upgrade_kubectl(
                    namespace,
                    ["get", "nodes", "-o", "json", "--request-timeout=20s"],
                    kube_context=kube_context,
                    extra_env=kube_env,
                    timeout_seconds=60,
                    check=True,
                )
                payload = json.loads(result.stdout)
                if not isinstance(payload.get("items"), list):
                    raise RuntimeError("Retirement node inventory is unavailable")
                return retirement_members(payload["items"], group_ids=affected, counts=counts)

            before = members()
            journal_path = self.executor.paths.reports_dir / "soperator-campaign-topology.json"
            journal = (
                read_owner_only_json(journal_path, label="Topology retirement")
                if journal_path.exists()
                else {
                    "schema": "nebius-cxcli.topology-retirement.v1",
                    "intent": intent.digest,
                    "attempts": [],
                }
            )
            if (
                not isinstance(journal, dict)
                or journal.get("intent") != intent.digest
                or not isinstance(journal.get("attempts"), list)
            ):
                raise RuntimeError("Retirement recovery journal belongs to another intent")
            journal["attempts"].append({"groups": affected, "capacity": counts, "members": before})
            write_owner_only_json(journal_path, journal)
            for key, provider_id in affected.items():
                assert_authority()
                self._quiescence(namespace=namespace, kube_context=kube_context, kube_env=kube_env)
                freeze_capacity(sdk, provider_id=provider_id, count=counts[key])
            if members() != before:
                raise RuntimeError("Retirement membership changed while capacity was frozen")
            assert_authority()
        finally:
            sdk.sync_close()

    def execute(
        self,
        name: str,
        *,
        intent: SoperatorUpgradeCampaignIntent,
        assert_authority: Callable[[], object],
        config_store: Any,
        kube_env: Mapping[str, str],
        kube_context: str,
        namespace: str,
    ) -> CampaignSegmentResult:
        from .deployment_plan import assert_stage_plan, terraform_changes

        stage_name = "reconcile" if name == "final-reconcile" else name
        self._quiescence(namespace=namespace, kube_context=kube_context, kube_env=kube_env)
        assert_authority()
        self._publish(stage_name, config_store=config_store, assert_authority=assert_authority)
        journal, deletions = self._dependencies(stage_name, intent)
        self.executor.preflight()
        from .compatibility_execution import verify_constraint_replay

        verify_constraint_replay(
            self.admissions[stage_name].application["compatibilityAdmission"],
            self.executor.compatibility_report,
        )
        plan_file, refreshed = self.executor._terraform_plan(
            purpose=f"Refresh {stage_name} stage before apply"
        )
        assert_stage_plan(self.admissions[stage_name].terraform, refreshed)
        assert_authority()
        self._quiescence(namespace=namespace, kube_context=kube_context, kube_env=kube_env)
        if name == "retire" and terraform_changes(refreshed):
            self._freeze_retirement(
                intent=intent,
                assert_authority=assert_authority,
                namespace=namespace,
                kube_context=kube_context,
                kube_env=kube_env,
            )
            plan_file, refreshed = self.executor._terraform_plan(
                purpose="Refresh retirement plan after worker quiescence"
            )
            assert_stage_plan(self.admissions[stage_name].terraform, refreshed)
        if terraform_changes(refreshed):
            assert_authority()
            self._quiescence(namespace=namespace, kube_context=kube_context, kube_env=kube_env)
            self.cli._run_terraform_apply_with_status(
                self.executor.config,
                self.executor.paths,
                initialize=False,
                run_mk8s_preflight=False,
                plan_file=plan_file,
                expected_plan_sha256=self.cli._sha256_file(plan_file),
                extra_env=self.executor.runtime_env,
                assert_authority=assert_authority,
            )
        assert_authority()
        _, after = self.executor._terraform_plan(purpose=f"Verify {stage_name} stage convergence")
        if terraform_changes(after):
            raise RuntimeError("Topology stage did not reach its admitted Terraform postconditions")
        if deletions:
            journal.deleted(deletions, self._resource_identities())
        prerequisites = self.admissions[stage_name].prerequisite_deletions
        if prerequisites:
            journal.require(prerequisites, self._resource_identities())
        # Publish app topology only under the parent's maintenance owner; the
        # release child keeps the barrier and owns any same-release settings.
        if name == "final-reconcile":
            self.executor.prepare_campaign_applications(
                kube_env=kube_env, assert_authority=assert_authority
            )
            self.executor._release(dry_run=False, campaign=(intent, config_store, assert_authority))
        evidence: dict[str, Any] = {
            "generation": self.admissions[stage_name].application["generationId"],
            "terraform": "converged",
        }
        if name == "grow":
            evidence["added_group_ids"] = dict(self.newly_owned_ids())
            if set(evidence["added_group_ids"]) != set(self.contract["added_group_keys"]):
                raise RuntimeError("Growth has not bound every new Terraform-owned group")
        return CampaignSegmentResult(evidence, irreversible_frontier=f"topology:{name}")

    def final_intent(
        self, intent: SoperatorUpgradeCampaignIntent
    ) -> SoperatorUpgradeCampaignIntent:
        from dataclasses import replace

        from .soperator_full_stack_upgrade import FrozenCompatibilityRow, FrozenNodeGroupTarget

        owned = self._owned_ids()
        if set(owned) != set(self.desired_groups):
            raise RuntimeError("Final inventory differs from the frozen desired topology")
        targets = []
        rows = []
        existing = {group.key: group for group in intent.node_groups}
        frozen_rows = self.contract["final_templates"]
        for key, row in frozen_rows.items():
            prior = existing.get(key)
            targets.append(
                FrozenNodeGroupTarget(
                    key=key,
                    provider_name=row["name"],
                    provider_id=owned[key],
                    platform=row["platform"],
                    source_version=prior.source_version if prior else row["version"],
                    source_os=prior.source_os if prior else row["os"],
                    source_drivers_preset=prior.source_drivers_preset if prior else row["drivers"],
                    target_version=row["version"],
                    target_os=row["os"],
                    target_drivers_preset=row["drivers"],
                    gpu=row["gpu"],
                )
            )
            rows.append(
                FrozenCompatibilityRow(
                    key, row["version"], row["platform"], row["os"], row["drivers"]
                )
            )
        return replace(intent, node_groups=tuple(targets), compatibility_rows=tuple(rows))


class ApplicationCampaignHooks(CampaignGenerationHooks):
    """Final desired application publication for a provider-owned cluster."""

    def __init__(self, executor: Any, *, source: Mapping[str, Any], generation: Any) -> None:
        self.executor, self.cli = executor, executor.cli
        self.source, self.generation = source, generation
        self.admissions: dict[str, Any] = {}
        self._contract: dict[str, Any] = {}
        self.intent: SoperatorUpgradeCampaignIntent | None = None

    @property
    def contract(self) -> Mapping[str, Any]:
        if not self._contract:
            raise RuntimeError("Application admission has not bound its source inventory")
        return self._contract

    def bind_source_inventory(
        self,
        groups: Sequence[Any],
        source_component: Mapping[str, Any] | None,
        *,
        compatibility_lookup: Callable[..., Sequence[Any]],
    ) -> None:
        if self._contract:
            return
        ids = [str(group.metadata.id) for group in groups]
        if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError("Registered source group inventory is incomplete or ambiguous")
        self._contract = {
            "generation": self.generation.identity,
            "source_group_ids": sorted(ids),
            "retired_group_ids": [],
            "remaining_group_ids": sorted(ids),
            "added_group_keys": [],
            "release_transition": self.executor.plan.source_release
            != self.executor.plan.target_release,
            "final-reconcile": {
                "generation": self.admissions["reconcile"].application["generationId"]
            },
        }

    def newly_owned_ids(self) -> Mapping[str, str]:
        return {}

    def final_intent(
        self, intent: SoperatorUpgradeCampaignIntent
    ) -> SoperatorUpgradeCampaignIntent:
        return intent

    def execute(
        self,
        name: str,
        *,
        intent: SoperatorUpgradeCampaignIntent,
        assert_authority: Callable[[], object],
        config_store: Any,
        kube_env: Mapping[str, str],
        kube_context: str,
        namespace: str,
    ) -> CampaignSegmentResult:
        if (
            name != "final-reconcile"
            or intent.ownership != "onboarded"
            or intent.backend != "provider-api"
        ):
            raise RuntimeError("Onboarded application hook cannot execute managed topology")
        assert_authority()
        self._publish("reconcile", config_store=config_store, assert_authority=assert_authority)
        # The shared project may contain ordinary Terraform infrastructure. Its
        # admitted ownership never includes this registered provider-owned cluster.
        self.executor.apply_admitted_project_terraform(
            self.admissions["reconcile"], assert_authority
        )
        self.executor.prepare_campaign_applications(
            kube_env=kube_env, assert_authority=assert_authority
        )
        self.executor._release(dry_run=False, campaign=(intent, config_store, assert_authority))
        assert_authority()
        return CampaignSegmentResult(
            {
                "generation": self.admissions["reconcile"].application["generationId"],
                "application": "published-awaiting-parent-final-proof",
            },
            irreversible_frontier="application:final-reconcile",
        )
