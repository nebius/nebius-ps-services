"""Compose ordinary guest and attachment owners without sharing their authority."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .. import apply_approval
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Effect, Progress
from .security_group_apply import changed as attachments_changed


def guest_key(name: str) -> str:
    return "guest:" + name


def guest_effect(name: str, prepared, target: tuple[str, str]) -> Effect:
    return Effect(
        guest_key(name),
        "ordinary-runtime",
        "apply",
        target,
        apply_approval.manifest_identity(prepared.manifest),
        apply_approval.digest(prepared.observation),
        True,
    )


@dataclass
class OrdinaryCommand:
    journal: CommandJournal
    plan: CommandPlan
    resumed: bool
    satisfied: dict[str, dict]
    routes: Any = None
    intents: dict | None = None

    @property
    def approval(self) -> str:
        return self.plan.approval

    def begin(self) -> None:
        self.journal.begin(self.plan, intents=self.intents)
        for effect in self.plan.effects:
            if effect.key in self.satisfied:
                self.journal.verified(effect.key, self.satisfied[effect.key])
        if self.routes is not None:
            self.routes.begin(self.approval)


def package_manifests(
    journal: CommandJournal, *, local: dict, targets: dict, artifact: str
) -> dict:
    """Read the original package requests before consulting remote resolvers."""
    snapshot = journal.snapshot()
    transaction = snapshot["transaction"] if snapshot else None
    if transaction is None or transaction["phase"] == "complete":
        return {}
    original = CommandPlan.parse(transaction["plan"])
    if (
        original.configuration_sha256
        != apply_approval.digest({"desired": local, "targets": targets})
        or original.artifact_sha256 != artifact
        or any(
            effect.owner not in {"ordinary-runtime", "ordinary-routes", "security-groups"}
            for effect in original.effects
        )
    ):
        raise RuntimeError(
            "Pending ordinary apply requires its original targets and intent; "
            "a reviewed successor is required"
        )
    assert snapshot is not None
    return {
        effect.key.removeprefix("guest:"): {
            **deepcopy(snapshot["bindings"][effect.key]),
            "command_approval": apply_approval.bind(original.approval, original.mapping()),
        }
        for effect in original.effects
        if effect.owner == "ordinary-runtime"
    }


def inspect(
    journal: CommandJournal,
    *,
    local: dict,
    targets: dict,
    plans: dict,
    attachments: dict | None,
    attachment_pending: dict | None,
    artifact_sha256: str,
    attachment_completed: dict | None = None,
    routes: Any = None,
) -> OrdinaryCommand:
    pending = journal.pending()
    original = CommandPlan.parse(pending["plan"]) if pending else None
    approved = {effect.key: effect for effect in original.effects} if original else {}
    progress = (
        {key: Progress(value) for key, value in pending["progress"].items()} if pending else {}
    )
    effects = []
    satisfied = {}
    resumable = set()
    for name, prepared in sorted(plans.items()):
        key = guest_key(name)
        previous = approved.get(key)
        if prepared.noop:
            if previous is not None:
                assert original is not None
                completion = prepared.observation.get("runtime_completion")
                if progress[key] is Progress.STARTED and (
                    not completion
                    or completion.get("approval")
                    != apply_approval.bind(original.approval, original.mapping())
                    or completion.get("manifest") != previous.request_sha256
                ):
                    raise RuntimeError("Started guest effect lacks its exact terminal receipt")
                satisfied[key] = {"observed": apply_approval.digest(prepared.observation)}
                effects.append(previous)
            continue
        effect = guest_effect(name, prepared, targets[name])
        child = prepared.observation.get("runtime_pending")
        if child:
            if original is None or prepared.manifest.get("command_approval") != apply_approval.bind(
                original.approval, original.mapping()
            ):
                raise RuntimeError("Interrupted guest is not bound to this apply command")
            resumable.add(key)
        elif previous and progress[key] is Progress.STARTED and effect == previous:
            # The child owner observed no pending writer and the exact original
            # predecessor: interruption preceded the child's first effect.
            resumable.add(key)
        effects.append(effect)
    if routes is not None:
        key = "routes"
        previous = approved.get(key)
        if routes.required:
            effects.append(
                Effect(
                    key,
                    "ordinary-routes",
                    "reconcile",
                    tuple(sorted(targets)),
                    routes.request_sha256,
                    apply_approval.digest(routes.intent["observed"]),
                    True,
                    previous.requires if previous else tuple(effect.key for effect in effects),
                )
            )
            if routes.pending:
                if original is None or routes.pending["approval"] != original.approval:
                    raise RuntimeError("Pending routes are not bound to this apply command")
                resumable.add(key)
        elif previous is not None:
            assert original is not None
            completed = routes.completed
            if (
                not completed
                or completed["approval"] != original.approval
                or apply_approval.digest(completed["intent"]) != previous.request_sha256
                or not routes.verifies_completed()
            ):
                raise RuntimeError("Started routes lack their exact terminal receipt")
            effects.append(previous)
            satisfied[key] = {"observed": apply_approval.digest(routes.preview)}
    if attachments is not None and (attachments_changed(attachments) or attachment_pending):
        key = "attachments"
        guest_keys = tuple(effect.key for effect in effects)
        effects.append(
            Effect(
                key,
                "security-groups",
                "reconcile",
                tuple(sorted(targets)),
                apply_approval.digest(attachments),
                apply_approval.digest(attachments),
                True,
                approved[key].requires if key in approved else guest_keys,
            )
        )
        if attachment_pending:
            if (
                original is None
                or pending is None
                or attachment_pending["approval"] != original.approval
                or attachment_pending["operation_id"] != pending["execution_id"]
            ):
                raise RuntimeError("Pending attachments are not bound to this apply command")
            resumable.add(key)
        elif previous := approved.get(key):
            if progress[key] is Progress.STARTED and previous == effects[-1]:
                # Exact unchanged predecessor and no pending child prove that
                # this execution has not submitted an attachment request yet.
                resumable.add(key)
    elif "attachments" in approved and attachments is not None:
        assert original is not None
        assert pending is not None
        previous = approved["attachments"]
        if progress["attachments"] is Progress.STARTED and (
            not attachment_completed
            or attachment_completed.get("phase") != "complete"
            or attachment_completed.get("approval") != original.approval
            or attachment_completed.get("operation_id") != pending["execution_id"]
            or apply_approval.digest(attachment_completed["plan"]) != previous.request_sha256
            or {
                name: (member["compute_id"], member["desired"])
                for name, member in attachments["members"].items()
            }
            != {
                name: (member["compute_id"], member["desired"])
                for name, member in attachment_completed["plan"]["members"].items()
            }
        ):
            raise RuntimeError("Started attachments lack their exact terminal receipt")
        effects.append(previous)
        satisfied["attachments"] = {"observed": apply_approval.digest(attachments)}
    current = CommandPlan(
        journal.project,
        journal.gateway,
        apply_approval.digest({"desired": local, "targets": targets}),
        artifact_sha256,
        tuple(effects),
    )
    if original is not None:
        original.validate_resume(
            current,
            progress,
            resumable_children=frozenset(resumable),
            satisfied_children=frozenset(satisfied),
        )
        if set(approved) != {effect.key for effect in effects}:
            raise RuntimeError("Apply child inventory changed before independent verification")
    elif attachment_pending:
        raise RuntimeError("Pending attachments have no original command envelope")
    if original is not None:
        snapshot = journal.snapshot()
        assert snapshot is not None
        intents = snapshot["bindings"]
    else:
        intents = {
            effect.key: {
                key: deepcopy(value)
                for key, value in plans[effect.key.removeprefix("guest:")].manifest.items()
                if key not in {"command_approval", "ha_parent"}
            }
            for effect in effects
            if effect.owner == "ordinary-runtime"
        }
    return OrdinaryCommand(
        journal, original or current, original is not None, satisfied, routes, intents
    )
