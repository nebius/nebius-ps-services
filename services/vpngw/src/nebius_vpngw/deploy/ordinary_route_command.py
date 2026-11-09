"""Compose frozen ordinary route intent with the existing apply command owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from . import ordinary_route_plan
from .ordinary_route_conversion import convert
from .ordinary_route_discovery import Discovery
from .ordinary_route_execute import _admit, execute
from .ordinary_route_ownership import RouteJournal
from .replacement_cloud import Cloud
from .route_manager import RouteManagementError, RouteManager


def _observe_plan(discovery: Discovery, observation: dict, *, defer_bgp: bool) -> tuple[dict, bool]:
    policy = observation["policy"]
    unknown = False
    learned: dict[str, tuple[str, ...]]
    if defer_bgp:
        learned = {
            connection.name: () for connection in policy.connections if connection.mode == "bgp"
        }
    else:
        try:
            learned = discovery.learned(policy)
        except RouteManagementError:
            # No new dynamic destinations are admitted from an incomplete
            # snapshot. The policy and admitted ledger still protect retirement.
            unknown = True
            learned = {
                connection.name: () for connection in policy.connections if connection.mode == "bgp"
            }
    plan = ordinary_route_plan.inspect(
        project=discovery.journal.project,
        gateway=discovery.journal.gateway,
        policy=policy,
        learned=learned,
        allocations=observation["allocations"],
        active_tables=observation["active_tables"],
        routes=observation["routes"],
        ownership=discovery.journal.inventory(),
        infrastructure=observation["infrastructure"],
    )
    plan["options"] = {"force_copy": discovery.force_copy}
    return convert(
        plan,
        observation.get("conversion_requests", {}),
        required=bool(plan["requests"] or discovery.force_copy or defer_bgp and policy.dynamic),
    ), unknown


@dataclass
class PreparedRoutes:
    discovery: Discovery
    intent: dict
    preview: dict
    required: bool
    pending: dict | None
    completed: dict | None
    connectivity_unknown: bool
    publication: Any = None

    @property
    def request_sha256(self) -> str:
        return digest(self.intent)

    def review_lines(self) -> tuple[str, ...]:
        """Describe the frozen authority, including deferred BGP selection."""
        policy = self.intent["policy"]
        lines = ["reconcile ordinary VPC routes within this destination policy"]
        for connection in policy["connections"]:
            name, index = connection["name"], connection["instance_index"]
            destinations = ", ".join(connection["prefixes"])
            if connection["mode"] == "bgp":
                destinations = (
                    "subprefixes of " + destinations
                    if destinations
                    else "all eligible prefixes learned from these configured peers"
                )
                lines.append(
                    f"{name} (gateway {index}): BGP peers {', '.join(connection['peers'])}; "
                    f"destinations: {destinations}; maximum {connection['max_prefixes']} prefixes"
                )
            else:
                lines.append(f"{name} (gateway {index}): Static destinations {destinations}")
        lines.append("exclude local/VPC destinations: " + ", ".join(policy["excluded_prefixes"]))
        lines.append("target route tables: " + ", ".join(self.intent["active_tables"]))
        for _key, conversion in sorted(self.intent["conversions"].items()):
            lines.append(
                f"copy route table {conversion['source']} to a new private table; preserve "
                "unrelated routes; reassign only subnets "
                + ", ".join(sorted(conversion["subnets"]))
            )
        for _key, request in sorted(self.preview["requests"].items()):
            if request["type"] == "DeleteRouteRequest":
                before = request["before"]
                lines.append(
                    f"retire owned route {before['id']} ({before['destination']}) "
                    f"from table {before['parent_id']}: {request['reason']}"
                )
        if self.connectivity_unknown:
            lines.append("BGP evidence is incomplete; do not infer additions from this observation")
        return tuple(lines)

    def begin(self, approval: str) -> None:
        if self.publication is not None and self.publication.released():
            return
        if self.required:
            with self.discovery.journal:
                self.discovery.journal.begin_intent(self.intent, approval=approval)

    def verifies_completed(self) -> bool:
        """Recheck a lost terminal reply using its post-effect infrastructure."""
        if self.completed is None:
            return False
        if self.publication is not None and self.publication.verify_released(self.discovery):
            return True
        original = self.completed["intent"]
        if any(
            original[key] != self.intent[key]
            for key in ("project", "gateway", "policy", "allocations")
        ):
            return False
        state = self.discovery.journal.snapshot()
        assert state is not None
        self.discovery.journal.state = state
        _admit(
            Cloud(self.discovery.manager._get_client(), self.discovery.journal),
            self.completed["plan"],
            lambda: self.discovery.observe(completed=True),
        )
        return True

    def finish(self, approval: str) -> dict:
        journal = self.discovery.journal
        transaction = journal.transaction()
        if self.publication is not None and self.publication.verify_released(self.discovery):
            if (
                transaction is None
                or transaction["phase"] != "complete"
                or transaction["approval"] != approval
            ):
                raise RuntimeError("Released runtime routes lost their original deployment receipt")
            return {
                "approval": approval,
                "intent": digest(transaction["intent"]),
                "selection": digest(transaction["plan"]),
                "connectivity": "unknown" if self.connectivity_unknown else "observed",
            }
        if self.publication is not None:
            self.publication.before_routes(self)
            journal = self.discovery.journal
            transaction = journal.transaction()
        if transaction and transaction["phase"] == "pending":
            if transaction["approval"] != approval or transaction["intent"] != self.intent:
                raise RuntimeError("Routes are not bound to this apply command")
            selected = transaction["plan"]
            if selected is None:
                observation = self.discovery.observe()
                selected, self.connectivity_unknown = _observe_plan(
                    self.discovery,
                    observation,
                    defer_bgp=False,
                )
                if ordinary_route_plan.intent(selected) != self.intent:
                    raise RuntimeError("Route scope changed before post-activation selection")
        else:
            selected = self.preview
        execute(
            self.discovery.manager._get_client(),
            journal,
            selected,
            approval=approval,
            observe=self.discovery.observe,
        )
        return {
            "approval": approval,
            "intent": digest(self.intent),
            "selection": digest(selected),
            "connectivity": "unknown" if self.connectivity_unknown else "observed",
        }


def prepare(
    manager: Any,
    deployment: Any,
    local: dict,
    *,
    ssh_policy: Any,
    runtime_changes: bool,
    journal: RouteJournal | None = None,
    summarize: bool | None = None,
    force_copy: bool = False,
    publication: Any = None,
) -> PreparedRoutes:
    if deployment.vm_ha is not None:
        raise ValueError("Ordinary routes cannot bypass HA route ownership")
    journal = journal or (
        publication.preview_journal()
        if publication is not None
        else RouteJournal(manager.project_id, deployment.gateway_group.name)
    )
    transaction = journal.transaction()
    pending = transaction if transaction and transaction["phase"] == "pending" else None
    completed = transaction if transaction and transaction["phase"] == "complete" else None
    if summarize is None:
        # An ordinary apply retains the route owner's admitted policy. An
        # explicit route command may change it, but cannot change pending work.
        owner = publication.observed["authority"] if publication is not None else None
        policy = (
            pending["intent"]["policy"]
            if pending
            else owner["binding"]["policy"]
            if owner and owner["binding"]
            else {}
        )
        summarize = policy.get("summarize", False)
    discovery = Discovery(
        manager,
        deployment,
        local,
        journal,
        RouteManager(manager.project_id, auth_token=manager.auth_token, ssh_policy=ssh_policy),
        summarize=summarize,
        force_copy=force_copy,
    )
    observation = discovery.observe()
    if (
        publication is not None
        and publication.journal.pending()
        and publication.verify_released(discovery)
    ):
        if completed is None:
            raise RuntimeError("Released runtime routes lack a completed deployment journal")
        return PreparedRoutes(
            discovery,
            completed["intent"],
            completed["plan"],
            False,
            None,
            completed,
            False,
            publication,
        )
    if pending and pending["plan"] is not None:
        original = pending["intent"]
        if digest(observation["policy"].mapping()) != digest(original["policy"]) or original[
            "options"
        ] != {"force_copy": force_copy}:
            raise RuntimeError("Pending ordinary routes have changed desired policy")
        state = journal.snapshot()
        assert state is not None
        journal.state = state
        _admit(
            Cloud(manager._get_client(), journal),
            pending["plan"],
            discovery.observe,
            uncertain=True,
        )
        return PreparedRoutes(
            discovery, original, pending["plan"], True, pending, completed, False, publication
        )
    preview, unknown = _observe_plan(discovery, observation, defer_bgp=runtime_changes)
    current_intent = ordinary_route_plan.intent(preview)
    required = bool(
        preview["requests"]
        or preview["absent"]
        or pending
        or runtime_changes
        and observation["policy"].dynamic
    )
    if pending:
        original = pending["intent"]
        if any(
            original[key] != current_intent[key]
            for key in (
                "project",
                "gateway",
                "policy",
                "infrastructure",
                "allocations",
                "active_tables",
                "options",
            )
        ):
            raise RuntimeError(
                "Pending ordinary routes have changed desired or infrastructure scope"
            )
        if pending["plan"] is None and current_intent != original:
            raise RuntimeError("Unstarted ordinary route predecessor changed")
        current_intent = original
    return PreparedRoutes(
        discovery, current_intent, preview, required, pending, completed, unknown, publication
    )


def run(
    routes: RouteManager,
    deployment: Any,
    local: dict,
    *,
    summarize: bool,
    force_copy: bool,
    rollback_dir: Any,
) -> None:
    """Public route command, serialized with apply and sharing its exact owner."""
    from .apply_command import CommandJournal
    from .apply_plan import CommandPlan
    from .ordinary_command import inspect as inspect_command
    from .ordinary_route_publication import inspect as inspect_publication
    from .ssh_push import SSHPush
    from .vm_ha_lifecycle import VMHAApplyLock
    from .vm_manager import VMManager

    project = routes.project_id
    gateway = deployment.gateway_group.name
    if not project or deployment.vm_ha is not None:
        raise RouteManagementError(
            "Ordinary routing requires an exact project and ordinary gateway"
        )
    with VMHAApplyLock(project_id=project, gateway_name=gateway):
        command_journal = CommandJournal(project, gateway)
        pending = command_journal.pending()
        artifact = digest("add-routes-local")
        if pending and CommandPlan.parse(pending["plan"]).artifact_sha256 != artifact:
            raise RouteManagementError(
                "Resume the pending apply command before separate route management"
            )
        manager = VMManager(
            project,
            local.get("region"),
            auth_token=routes.auth_token,
            ssh_policy=routes._ssh_policy,
        )
        try:
            instances = tuple(deployment.iter_instance_configs())
            targets = manager.inspect_ordinary_targets(item.hostname for item in instances)
            publication = inspect_publication(
                command_journal, SSHPush(ssh_policy=routes._ssh_policy), instances, local, targets
            )
            prepared = prepare(
                manager,
                deployment,
                local,
                ssh_policy=routes._ssh_policy,
                runtime_changes=False,
                summarize=summarize,
                force_copy=force_copy,
                publication=publication,
            )
            command = inspect_command(
                command_journal,
                local={"configuration": local, "summarize": summarize, "force_copy": force_copy},
                targets=targets,
                plans={},
                attachments=None,
                attachment_pending=None,
                artifact_sha256=artifact,
                routes=prepared,
                publication=publication,
            )
            approval = command.approval
            if prepared.pending and prepared.pending["approval"] != approval:
                raise RouteManagementError("Resume routes through their original approved command")
            if command.plan.effects:
                with command_journal:
                    command.begin()
                    if prepared.required:
                        command_journal.started("routes")
                    receipt = prepared.finish(approval)
                    if prepared.required:
                        command_journal.verified("routes", receipt)
                    if publication is not None:
                        publication.finish(prepared)
                    command_journal.complete()
            else:
                prepared.finish(approval)
            if force_copy and rollback_dir is not None:
                _write_rollbacks(prepared, rollback_dir)
            if prepared.connectivity_unknown:
                raise RouteManagementError(
                    "BGP evidence is incomplete; admitted routes were preserved, but connectivity remains unknown"
                )
        finally:
            manager.close()


def _write_rollbacks(prepared: PreparedRoutes, directory: Any) -> None:
    """Exact post-cutover CAS files; never overwrite later customer subnet edits."""
    import os
    from pathlib import Path

    from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]

    from ..apply_lock import encode
    from .operator_keys import protected_directory
    from .ordinary_route_conversion import readback_matches, resolve
    from .vm_ha_cloud import clone_nebius_sdk_message

    directory = Path(directory)
    protected_directory(directory)
    current = prepared.discovery.observe()
    transaction = prepared.discovery.journal.transaction()
    if transaction is None or transaction["phase"] != "complete":
        raise RuntimeError("Rollback guidance requires verified route completion")
    for record in transaction["plan"]["requests"].values():
        if record["type"] != "UpdateSubnetRequest":
            continue
        request = resolve(record, transaction["plan"], transaction["operations"])
        observed = current["subnet_resources"][request.metadata.id]
        if not readback_matches(observed, request):
            raise RuntimeError("Subnet changed before rollback guidance could be recorded")
        rollback = resolve(dict(record, table_source=None), transaction["plan"], {})
        rollback.metadata = clone_nebius_sdk_message(observed.metadata)
        frozen = ordinary_route_plan.request_record(rollback)
        original = frozen["request"]
        projected = type(request)()
        ParseDict(original, projected.__pb2_message__)
        if ordinary_route_plan.request_record(projected)["sha256"] != frozen["sha256"]:
            raise RuntimeError(
                "Subnet cutover is verified, but rollback JSON cannot preserve unknown cloud "
                "fields; retain the completed route journal instead of applying a lossy update"
            )
        payload = encode(original)
        path = directory / ("rollback-" + digest(payload.hex())[:32] + ".json")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            from .operator_guest import read_file

            previous = read_file(path)
            if (
                previous is None
                or previous["content"].encode() != payload
                or previous["uid"] != os.getuid()
                or previous["mode"] != 0o600
            ):
                raise RuntimeError("Rollback artifact conflicts with exact cutover evidence")
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        import shlex

        print(
            "Verified subnet cutover. To restore its prior table while the subnet is unchanged: nebius vpc subnet update --file "
            + shlex.quote(str(path))
        )
