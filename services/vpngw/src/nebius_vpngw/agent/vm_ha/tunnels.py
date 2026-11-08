"""HA controller's durable same-owner tunnel state machine.

The caller holds the HA writer lock before entering this component. Effects must
revalidate authority and take the routing lock in that order. This component has
no cloud mutation port and cannot promote a VM.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ...ha_tunnel_policy import ConnectionPolicy, connection_policies, selections
from .models import canonical_json
from .store import CorruptStateError, _envelope, _unwrap, atomic_write_json

CAPABILITY = "connection-tunnel-selection-v1"
STATE_NAME = "tunnel-selection.json"
SCHEMA = "nebius-vpngw/ha-tunnel-selection-v1"


class Health(str, Enum):
    HEALTHY = "healthy"
    DOWN = "down"
    UNKNOWN = "unknown"


class TunnelScopeError(RuntimeError):
    """No effects were made because connection-only suspension was unproven."""


@dataclass(frozen=True)
class Authority:
    cluster: str
    owner: str
    allocation: str
    epoch: str
    incarnation: int
    generation: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(getattr(self, key), str) or not getattr(self, key)
            for key in ("cluster", "owner", "allocation", "epoch", "generation")
        ):
            raise ValueError("HA tunnel authority is incomplete")
        if type(self.incarnation) is not int or self.incarnation < 0:
            raise ValueError("HA tunnel owner incarnation is invalid")

    @property
    def owner_key(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k not in {"generation", "epoch"}}


class TunnelEffects(Protocol):
    def revalidate(self, authority: Authority) -> bool: ...
    def active(self, authority: Authority) -> bool: ...
    def candidate(self, policy: ConnectionPolicy, target: str) -> Health: ...
    def observe(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health: ...
    def suspend(self, policy: ConnectionPolicy, authority: Authority) -> None: ...
    def activate(
        self, policy: ConnectionPolicy, target: str | None, authority: Authority
    ) -> None: ...
    def verify(self, policy: ConnectionPolicy, target: str | None) -> Health: ...
    def authority_restored(
        self, policy: ConnectionPolicy, target: str, authority: Authority
    ) -> bool: ...
    def fence(self) -> None: ...


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "authority": None,
        "selected": {},
        "reasons": {},
        "health": {},
        "operation": None,
        "episode": None,
        "sequence": 0,
    }


def require_tunnel_quiescent(state_dir: Path) -> None:
    from .tunnel_requests import read_request

    request = read_request(state_dir)
    if request is not None and request["result"] == "outstanding":
        raise RuntimeError("HA tunnel request is outstanding; wait for controller convergence")
    operation = read_state(state_dir / STATE_NAME)["operation"]
    if operation is not None and operation["phase"] not in {"failed", "succeeded"}:
        raise RuntimeError("HA tunnel operation is outstanding; wait for controller convergence")


def read_state(path: Path) -> dict[str, Any]:
    import json

    try:
        value = dict(_unwrap(json.loads(path.read_text())))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as error:
        raise CorruptStateError("HA tunnel state is unreadable") from error
    if set(value) != set(empty_state()) or value["schema"] != SCHEMA:
        raise CorruptStateError("HA tunnel state has an invalid shape")
    for key in ("selected", "reasons", "health"):
        if not isinstance(value[key], dict):
            raise CorruptStateError("HA tunnel state has invalid connection records")
    for key in ("authority", "operation", "episode"):
        if value[key] is not None and not isinstance(value[key], dict):
            raise CorruptStateError("HA tunnel state has an invalid record")
    if type(value["sequence"]) is not int or value["sequence"] < 0:
        raise CorruptStateError("HA tunnel sequence is invalid")
    try:
        if value["authority"] is not None:
            Authority(**value["authority"])
        episode = value["episode"]
        if episode is not None:
            if set(episode) != {
                "owner",
                "started",
                "deadline",
                "last_observed",
                "healthy_since",
                "exhausted",
            }:
                raise ValueError
            Authority(**episode["owner"], generation="validation", epoch="validation")
            for key in ("started", "deadline", "last_observed"):
                if type(episode[key]) not in {int, float} or not math.isfinite(episode[key]):
                    raise ValueError
            if (
                episode["deadline"] - episode["started"] != 30
                or type(episode["exhausted"]) is not bool
            ):
                raise ValueError
            if episode["healthy_since"] is not None and (
                type(episode["healthy_since"]) not in {int, float}
                or not math.isfinite(episode["healthy_since"])
            ):
                raise ValueError
        operation = value["operation"]
        if operation is not None:
            if set(operation) != {
                "id",
                "connection",
                "target",
                "verb",
                "requested_verb",
                "requested_target",
                "previous",
                "phase",
                "deadline",
                "attempt_deadline",
                "successes",
                "attempted",
                "result",
                "effects_started",
            }:
                raise ValueError
            if operation["phase"] not in {
                "suspend",
                "activate",
                "restore",
                "verify",
                "verify-unchanged",
                "verify-restore",
                "failed",
                "succeeded",
            }:
                raise ValueError
            for key in ("deadline", "attempt_deadline"):
                if type(operation[key]) not in {int, float} or not math.isfinite(operation[key]):
                    raise ValueError
    except (TypeError, ValueError):
        raise CorruptStateError("HA tunnel authority, operation or episode is malformed") from None
    return value


def effective_config(
    cfg: dict[str, Any], state: dict[str, Any], authority: Authority
) -> dict[str, Any]:
    """Do not carry a selection across semantic config or ownership changes."""
    if not cfg.get("vm_ha"):
        return cfg
    effective = dict(cfg)
    effective["_ha_tunnel_selections"] = (
        dict(state["selected"]) if state["authority"] == asdict(authority) else {}
    )
    selections(effective)  # Reject corrupt/foreign connection or target records.
    operation = state["operation"]
    if (
        state["authority"] == asdict(authority)
        and operation is not None
        and operation["phase"] == "suspend"
    ):
        effective["_ha_suspended_connections"] = [operation["connection"]]
    return effective


class TunnelController:
    def __init__(
        self,
        cfg: dict[str, Any],
        path: Path,
        effects: TunnelEffects,
        after_persist: Callable[[], None] = lambda: None,
    ) -> None:
        if not cfg.get("vm_ha"):
            raise ValueError("tunnel controller requires explicit HA")
        self.cfg = cfg
        self.policies = {p.name: p for p in connection_policies(cfg)}
        self.path = path
        self.effects = effects
        self.state = read_state(path)
        self.after_persist = after_persist

    def _save(self) -> None:
        atomic_write_json(self.path, _envelope(self.state))
        self.after_persist()

    def bind(self, authority: Authority) -> None:
        previous = self.state["authority"]
        if previous == asdict(authority):
            return
        same_owner = (
            previous is not None
            and {k: v for k, v in previous.items() if k not in {"generation", "epoch"}}
            == authority.owner_key
        )
        if not same_owner:
            self.state["episode"] = None
        # Configuration changes invalidate an operation, not its consumed episode.
        if not same_owner or previous["generation"] != authority.generation:
            self.state.update(selected={}, reasons={}, health={})
            if self.state["episode"] is not None:
                self.state["episode"]["healthy_since"] = None
        self.state["authority"] = asdict(authority)
        if self.state["operation"] is not None:
            self.state["operation"].update(phase="failed", result="authority-changed")
        self._save()

    def _episode(self, now: float, authority: Authority) -> dict[str, Any]:
        episode = self.state["episode"]
        if episode is None:
            episode = {
                "owner": authority.owner_key,
                "started": now,
                "deadline": now + 30,
                "last_observed": now,
                "healthy_since": None,
                "exhausted": False,
            }
            self.state["episode"] = episode
            self._save()
        return episode

    def _pending(self) -> bool:
        op = self.state["operation"]
        return op is not None and op["phase"] not in {"succeeded", "failed"}

    def prepare_bgp_resume(
        self, authority: Authority, now: float, *, revalidate: Callable[[], bool]
    ) -> None:
        """Prepare desired policy behind the fence; the VM activation owns effects.

        The runtime admits only exact fenced ownership under writer exclusion.
        Never bind stale intent here, execute native effects, or replace another
        connection's operation. Completed selections have no expiry deadline.
        """
        if not math.isfinite(now) or self.state["authority"] != asdict(authority):
            return
        previous = deepcopy(self.state)
        op = self.state["operation"] if self._pending() else None
        changed = False
        for policy in self.policies.values():
            if policy.mode != "bgp":
                continue
            pending = op if op is not None and op["connection"] == policy.name else None
            selected = self.state["selected"].get(policy.name)
            if selected is None and pending is None:
                continue
            observations: dict[str, Health] = {}

            def candidate(
                target: str,
                policy: ConnectionPolicy = policy,
                observations: dict[str, Health] = observations,
            ) -> Health:
                if target not in observations:
                    observations[target] = self.effects.candidate(policy, target)
                return observations[target]

            release = selected is not None and candidate(selected) is Health.DOWN
            if pending is not None:
                pending["successes"] = 0
                changed = True
                expired = now >= pending["deadline"]
                if pending["verb"] == "release":
                    release = True
                elif expired or (
                    pending["verb"] != "failback" and candidate(pending["target"]) is Health.DOWN
                ):
                    pending.update(
                        verb="release", phase="activate", result="target-failed-restoring-policy"
                    )
                    release = True
                elif pending["verb"] == "failback":
                    release = release or candidate(policy.preferred) is Health.HEALTHY
            if release:
                self.state["selected"].pop(policy.name, None)
                self.state["reasons"][policy.name] = "restart-policy-restoration"
                # Native normal-policy verification has not happened yet.
                self.state["health"][policy.name] = Health.UNKNOWN.value
                changed = True
            elif selected is not None:
                # Candidate health cannot certify active route/export policy.
                self.state["health"][policy.name] = Health.UNKNOWN.value
                changed = True
        if changed:
            if revalidate():
                self._save()
            else:
                self.state = previous

    def request(
        self,
        verb: str,
        target: str | None,
        authority: Authority,
        now: float,
        *,
        operation_id: str | None = None,
    ) -> str:
        if verb not in {"failover", "failback"}:
            raise ValueError("unknown tunnel operation")
        if not self.effects.revalidate(authority):
            raise RuntimeError("HA tunnel authority is not current")
        self.bind(authority)
        pending = self.state["operation"]
        if self._pending():
            if pending["requested_verb"] == verb and (
                target is None or pending["target"] == target
            ):
                return str(pending["id"])
            raise RuntimeError("another HA tunnel operation is outstanding")
        candidates = [
            (p, name)
            for p in self.policies.values()
            if p.eligible
            for name in (
                [p.preferred]
                if verb == "failback"
                else [
                    x.name
                    for x in p.paths
                    if x.name != self.state["selected"].get(p.name, p.preferred)
                ]
            )
            if target is None or target == name
        ]
        if len(candidates) != 1:
            raise ValueError(
                "name one eligible tunnel on the current owner; selection is unavailable or ambiguous"
            )
        policy, resolved = candidates[0]
        unchanged = verb == "failback" and (
            policy.mode == "static"
            and self.state["selected"].get(policy.name, policy.preferred) == resolved
            or policy.mode == "bgp"
            and policy.name not in self.state["selected"]
        )
        episode = self.state["episode"]
        if (
            not unchanged
            and policy.mode == "static"
            and episode is not None
            and (episode["exhausted"] or now >= episode["deadline"])
        ):
            raise RuntimeError(
                "Static recovery budget is consumed; 60 seconds of continuous health is required"
            )
        if (
            policy.mode == "bgp"
            and self.effects.observe(policy, resolved, selected=False) is not Health.HEALTHY
        ):
            raise RuntimeError("manual BGP target health is not proven")
        self._begin(
            policy,
            resolved,
            verb,
            authority,
            now,
            operation_id=operation_id,
            requested_target=target,
            unchanged=unchanged,
        )
        return str(self.state["operation"]["id"])

    def _begin(
        self,
        policy: ConnectionPolicy,
        target: str,
        verb: str,
        authority: Authority,
        now: float,
        *,
        operation_id: str | None = None,
        requested_target: str | None = None,
        unchanged: bool = False,
    ) -> None:
        self.state["sequence"] += 1
        operation_id = (
            operation_id
            or hashlib.sha256(
                canonical_json(
                    {
                        "authority": asdict(authority),
                        "sequence": self.state["sequence"],
                        "connection": policy.name,
                        "target": target,
                        "verb": verb,
                    }
                ).encode()
            ).hexdigest()
        )
        self.state["operation"] = {
            "id": operation_id,
            "connection": policy.name,
            "target": target,
            "verb": verb,
            "requested_verb": verb,
            "requested_target": requested_target,
            "previous": self.state["selected"].get(policy.name, policy.preferred),
            "phase": "verify-unchanged"
            if unchanged
            else "suspend"
            if policy.mode == "static"
            else "activate",
            "deadline": now + 29,
            "attempt_deadline": now + 10,
            "successes": 0,
            "attempted": [target],
            "result": "outstanding",
            "effects_started": False,
        }
        self._save()

    def tick(self, authority: Authority, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("tunnel clock must be finite")
        if not self.effects.revalidate(authority):
            return
        self.bind(authority)
        health: dict[str, str] = {}
        for policy in self.policies.values():
            target = self.state["selected"].get(policy.name, policy.preferred)
            if policy.mode == "bgp":
                # An Established session can withdraw routes. Verify ongoing
                # received/selected/exported coverage for manual overrides too;
                # normal policy can use any healthy path, not just preferred.
                health[policy.name] = self.effects.verify(
                    policy, self.state["selected"].get(policy.name)
                ).value
            elif policy.mode == "static" and not policy.eligible:
                routes = {
                    prefix: path.name for path in policy.paths for prefix in path.destinations
                }
                observed = [
                    self.effects.observe(
                        replace(
                            policy,
                            destinations=frozenset(
                                prefix for prefix, name in routes.items() if name == path.name
                            ),
                        ),
                        path.name,
                        selected=True,
                    )
                    for path in policy.paths
                    if path.name in routes.values()
                ]
                health[policy.name] = (
                    Health.UNKNOWN
                    if not observed or Health.UNKNOWN in observed
                    else Health.DOWN
                    if Health.DOWN in observed
                    else Health.HEALTHY
                ).value
            else:
                health[policy.name] = self.effects.observe(policy, target, selected=True).value
        self.state["health"] = health
        static = list(self.policies.values())
        loss = any(health[p.name] == Health.DOWN.value for p in static)
        healthy = bool(static) and all(health[p.name] == Health.HEALTHY.value for p in static)
        if loss:
            self._episode(now, authority)
        episode = self.state["episode"]
        if episode is not None:
            # Clock rollback cannot renew the persisted budget, even across reboot.
            if now < episode["last_observed"]:
                episode["exhausted"] = True
            if now - episode["last_observed"] > 10:
                episode["healthy_since"] = None
            episode["last_observed"] = max(now, episode["last_observed"])
            observing_unchanged = (
                self._pending() and self.state["operation"]["phase"] == "verify-unchanged"
            )
            if healthy and (not self._pending() or observing_unchanged):
                if episode["healthy_since"] is None:
                    episode["healthy_since"] = now
                elif now - episode["healthy_since"] >= 60:
                    self.state["episode"] = episode = None
            else:
                episode["healthy_since"] = None
            if (
                episode is not None
                and not healthy
                and (episode["exhausted"] or now >= episode["deadline"])
            ):
                episode["exhausted"] = True
                self._save()
                # Expiry is path evidence, never gateway failure. Restore the
                # previous authority durably, then leave native retry running.
                if self._pending() and self.state["operation"]["phase"] not in {
                    "restore",
                    "verify-restore",
                    "verify-unchanged",
                }:
                    op = self.state["operation"]
                    policy = self.policies[op["connection"]]
                    if policy.mode == "bgp":
                        self.state["selected"].pop(policy.name, None)
                        op.update(verb="release", phase="activate", successes=0)
                    else:
                        op.update(phase="restore", successes=0)
                self._save()
        self._save()
        if self._pending():
            self._advance(authority, now)
            return
        if episode is not None and episode["exhausted"]:
            return
        for policy in self.policies.values():
            if not policy.eligible:
                continue
            if policy.mode == "bgp":
                if (
                    policy.name in self.state["selected"]
                    and health[policy.name] == Health.DOWN.value
                ):
                    self._begin(policy, policy.preferred, "release", authority, now)
                    # Persist release intent, then restore normal policy before
                    # the VM policy can repair a path under the stale override.
                    self._advance(authority, now)
                    return
            elif health[policy.name] == Health.DOWN.value:
                current = self.state["selected"].get(policy.name, policy.preferred)
                alternatives = [p.name for p in policy.paths if p.name != current]
                if alternatives:
                    self._begin(policy, alternatives[0], "automatic", authority, now)
                    return

    def _advance(self, authority: Authority, now: float) -> None:
        op = self.state["operation"]
        policy = self.policies[op["connection"]]
        if not self.effects.revalidate(authority):
            return
        if op["phase"] == "verify-unchanged":
            # Selection already matches intent. Verification cannot consume or
            # renew recovery authority, reload policy, or restart native SAs.
            try:
                observed = (
                    self.effects.verify(policy, None if policy.mode == "bgp" else op["target"])
                    if self.effects.active(authority)
                    else Health.UNKNOWN
                )
            except RuntimeError:
                observed = Health.UNKNOWN
            op["successes"] = op["successes"] + 1 if observed is Health.HEALTHY else 0
            if now >= op["deadline"]:
                op.update(phase="failed", result="unchanged-path-unverified")
            elif op["successes"] >= 2:
                op.update(phase="succeeded", result="verified")
            elif observed is Health.DOWN:
                op.update(phase="failed", result="unchanged-path-unverified")
            self._save()
            return
        if policy.mode == "bgp" and not self.effects.active(authority):
            return
        if policy.mode == "static":
            episode = self._episode(now, authority)
            op["deadline"] = min(op["deadline"], episode["deadline"] - 1)
        if now >= op["deadline"]:
            if policy.mode == "bgp":
                # A timed-out caller cannot cancel a partially installed policy.
                # Cleanup remains durable and outstanding until normal routing
                # is verified, even across a restart after the request deadline.
                if op["verb"] != "release":
                    self.state["selected"].pop(policy.name, None)
                    op.update(
                        verb="release",
                        phase="activate",
                        successes=0,
                        result="target-failed-restoring-policy",
                    )
                    self._save()
            elif op["phase"] not in {"restore", "verify-restore"}:
                op.update(phase="restore", successes=0, result="recovery-exhausted-restoring")
                self._save()
        if policy.mode == "bgp" and op["verb"] == "failback":
            # A restart or delayed retry must not reuse old preferred-path proof.
            if self.effects.candidate(policy, policy.preferred) is not Health.HEALTHY:
                op["successes"] = 0
                self._save()
                return
        target = op["target"]
        if policy.mode == "bgp" and op["verb"] in {"failback", "release"}:
            target = None
        try:
            if op["phase"] == "suspend":
                op["effects_started"] = True
                self._save()
                self.effects.suspend(policy, authority)
                op["phase"] = "activate"
            elif op["phase"] in {"activate", "restore"}:
                restore = op["phase"] == "restore"
                target = op["previous"] if restore else target
                if target is None:
                    self.state["selected"].pop(policy.name, None)
                else:
                    self.state["selected"][policy.name] = target
                self._save()  # All render/repair paths see intent before activation.
                self.effects.activate(policy, target, authority)
                op["phase"] = "verify-restore" if restore else "verify"
            else:
                restoring = op["phase"] == "verify-restore"
                if (
                    restoring
                    and self.state["episode"] is not None
                    and self.state["episode"]["exhausted"]
                ):
                    # Recovery health is separate from restored initiation
                    # authority. Do not continually cancel native retries while
                    # waiting for an unavailable remote endpoint to return.
                    if self.effects.authority_restored(policy, op["previous"], authority):
                        op.update(phase="failed", result="native-recovery-retained")
                    self._save()
                    return
                observed = self.effects.verify(policy, op["previous"] if restoring else target)
                if policy.mode == "bgp" and not self.effects.active(authority):
                    op["successes"] = 0
                    self._save()
                    return
                op["successes"] = op["successes"] + 1 if observed is Health.HEALTHY else 0
                if op["successes"] >= 2:
                    failed_target = op["result"] == "target-failed-restoring-policy"
                    op.update(
                        phase="failed" if restoring or failed_target else "succeeded",
                        result="previous-path-restored"
                        if restoring
                        else "normal-policy-restored"
                        if failed_target
                        else "verified",
                    )
                    self.state["reasons"][policy.name] = "restored" if restoring else op["verb"]
                elif (
                    now >= op["attempt_deadline"]
                    and not restoring
                    and observed is not Health.HEALTHY
                ):
                    self._failed_attempt(policy, now)
        except TunnelScopeError:
            if op["phase"] == "suspend":
                # The initial scope check preceded every native mutation. Do
                # not convert a rejected local request into gateway suspension.
                op.update(phase="failed", result="suspension-scope-unproven")
            else:
                self._failed_attempt(policy, now)
        except RuntimeError:
            self._failed_attempt(policy, now)
        self._save()

    def _failed_attempt(self, policy: ConnectionPolicy, now: float) -> None:
        op = self.state["operation"]
        op["successes"] = 0
        if op["verb"] == "automatic":
            alternatives = [
                p.name
                for p in policy.paths
                if p.name not in op["attempted"] and p.name != op["previous"]
            ]
            if alternatives and now + 3 < op["deadline"]:
                op.update(
                    target=alternatives[0],
                    phase="suspend",
                    attempt_deadline=min(now + 5, op["deadline"] - 2),
                )
                op["attempted"].append(alternatives[0])
                return
        if policy.mode == "bgp":
            # Failed manual target releases its override; normal policy is verified.
            op.update(
                verb="release",
                phase="activate",
                attempt_deadline=op["deadline"],
                result="target-failed-restoring-policy",
            )
        else:
            op.update(phase="restore", attempt_deadline=op["deadline"])
