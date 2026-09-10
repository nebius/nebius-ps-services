"""Observe mounted native passive policy and collect fresh native execution proof."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from .soperator_checks_policy import checks_digest
from .soperator_passive_policy import passive_phase_overrides
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

if TYPE_CHECKING:
    from .soperator_checks import SoperatorChecksExecution

import hashlib
import re
from pathlib import Path

_PROBE_SOURCE = Path(__file__).with_name("soperator_passive_probe.py").read_text()
_VERDICT_SOURCE = Path(__file__).with_name("soperator_checks_verdict.py").read_text()
# Both modules use only the standard library. Inject the canonical validator
# without installing cxcli on workers or maintaining a second report parser.
_PROBE = (
    "native_verdict = {}\n"
    f"exec({_VERDICT_SOURCE!r}, native_verdict)\n"
    "native_probe = {'__name__': __name__, '__package__': None, "
    "'health_verdict': native_verdict['health_verdict']}\n"
    f"exec({_PROBE_SOURCE!r}, native_probe)"
)


class PassivePending(RuntimeError):
    """Await the next native periodic run without repeating completed evidence."""


class PassiveDiagnostics:
    def __init__(self, checks: SoperatorChecksExecution) -> None:
        self.checks = checks

    @property
    def state(self) -> dict[str, Any]:
        return self.checks.state.setdefault("passive", {})

    def _expected(self, *, paused: bool) -> dict[str, Any]:
        policy = self.checks.policy.passive
        entries = copy.deepcopy(policy["entries"])
        if paused:
            overrides = passive_phase_overrides(
                policy,
                self.checks.state["reservation"],
                fresh_install=self.checks.state.get("freshInstall", False),
            )
            for name, row in overrides.items():
                if row.get("enabled") is False:
                    entries.pop(name)
                else:
                    entries[name] = json.loads(row["customConfig"])
        return {
            "scheduler": policy["scheduler"],
            "scripts": [
                name
                for name in policy["scripts"]
                if not (
                    paused
                    and self.checks.state.get("freshInstall")
                    and name in policy["diagnostics"]
                )
            ],
            "hashes": {
                name: hashlib.sha256(text.rstrip("\n").encode()).hexdigest()
                for name, text in policy["scripts"].items()
                if not (
                    paused
                    and self.checks.state.get("freshInstall")
                    and name in policy["diagnostics"]
                )
            },
            "config": [entries[name] for name in sorted(entries)],
            "diagnostics": [entries[name] for name in policy["diagnostics"] if name in entries],
            "diagnosticScripts": {
                entries[name]["command"]: name for name in policy["diagnostics"] if name in entries
            },
            "proofRoles": {
                name: policy["proofRoles"][name]
                for name in policy["diagnostics"]
                if name in entries
            },
        }

    def _report_limitations(self, result: Mapping[str, Any]) -> None:
        for row in result.get("verdicts", []):
            if row.get("proofRole") != "supporting-only":
                continue
            script = row["script"]
            reported = self.state.setdefault("supportingLimitations", {})
            if script in reported:
                continue
            limitation = row["limitation"]
            self.checks.emit(
                f"Passive diagnostic {script}: supporting native evidence only; {limitation}. "
                "This is not a measured PASS. Required measurements and fresh active "
                "acceptance remain mandatory."
            )
            reported[script] = limitation
            self.checks._save()

    def _observe(self, worker: str, expected: dict[str, Any], mode: str) -> dict[str, Any]:
        pod = self.checks._get("pod", worker)
        metadata = pod.get("metadata", {})
        statuses = pod.get("status", {}).get("containerStatuses", [])
        container = [row for row in statuses if row.get("name") == "slurmd"]
        if not metadata.get("uid") or len(container) != 1 or not container[0].get("ready"):
            raise RuntimeError("passive worker identity/readiness is unavailable")
        identity = {
            "uid": metadata["uid"],
            "container": container[0].get("containerID"),
            "restarts": container[0].get("restartCount"),
            "node": pod["spec"]["nodeName"],
        }
        self.checks.authority()
        result = self.checks.kube(
            [
                "exec",
                "-n",
                "soperator",
                worker,
                "-c",
                "slurmd",
                "--",
                "python3",
                "-c",
                _PROBE,
                json.dumps(expected),
                mode,
            ],
            None,
        )
        if result.get("worker") != worker:
            raise RuntimeError("passive evidence belongs to a different worker")
        after = self.checks._get("pod", worker)
        after_container = [
            row
            for row in after.get("status", {}).get("containerStatuses", [])
            if row.get("name") == "slurmd"
        ]
        if after.get("metadata", {}).get("uid") != identity["uid"] or after_container != container:
            raise RuntimeError("passive worker changed during observation")
        return {**result, "identity": identity}

    @staticmethod
    def _compact(result: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in result.items() if key not in {"hashes", "config"}}

    def _worker_path(self, worker: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", worker) or worker in {".", ".."}:
            raise RuntimeError("invalid passive worker receipt identity")
        return self.checks.path.parent / (self.checks.path.stem + "-passive") / (worker + ".json")

    def _worker_receipt(
        self,
        worker: str,
        digest: str,
        result: dict[str, Any] | None = None,
        *,
        invalidate: bool = False,
    ) -> dict[str, Any] | None:
        path = self._worker_path(worker)
        binding = {"operation": self.checks.state["operation"], "policy": digest, "worker": worker}
        if result is not None or invalidate:
            self.checks.authority()
            evidence = self._compact(result) if result is not None and not invalidate else None
            write_owner_only_json(path, {"binding": binding, "evidence": evidence})
            return evidence
        if not path.exists():
            return None
        payload = read_owner_only_json(path, label="passive worker acceptance")
        if not isinstance(payload, dict) or payload.get("binding") != binding:
            raise RuntimeError("passive worker receipt belongs to a different execution")
        evidence = payload.get("evidence")
        if evidence is not None and not isinstance(evidence, dict):
            raise RuntimeError("invalid passive worker evidence")
        return evidence

    def _scheduler(self) -> None:
        desired = self.checks.policy.passive.get("scheduler", {})
        live = dict(
            re.findall(
                r"(?m)^\s*(HealthCheckInterval|HealthCheckProgram|HealthCheckNodeState|Prolog|Epilog)\s*=\s*(\S+)",
                self.checks.slurm("scontrol show config"),
            )
        )
        if any(live.get(key) != value for key, value in desired.items()):
            raise RuntimeError(
                "desired passive scheduler or operational hook wiring has not converged"
            )

    def _fallback_expected(self) -> dict[str, Any]:
        cm = self.checks._get("configmap", "slurm-scripts")
        data = cm.get("data", {})
        if self.state.get("customSource"):
            frozen = self.state.setdefault("sourceFallbackData", copy.deepcopy(data))
            self.checks._save()
        else:
            frozen = self.checks.policy.passive.get("opaque")
        if not isinstance(frozen, dict) or data != frozen:
            raise RuntimeError(
                "opaque passive configuration differs from its frozen desired contract"
            )
        config = json.loads(data.get("checks.json", "null"))
        if not cm.get("metadata", {}).get("uid") or not isinstance(config, list):
            raise RuntimeError("fallback passive configuration is unavailable")
        reservation = self.checks.state.get("reservation")
        if reservation and any(
            reservation in row.get("skip_for_reservation_prefixes", []) for row in config
        ):
            raise RuntimeError("cannot claim enabled fallback while owned suppression remains")
        scripts = {name: value for name, value in data.items() if name != "checks.json"}
        if not scripts:
            raise RuntimeError("fallback passive scripts are unavailable")
        return {
            "scripts": list(scripts),
            "hashes": {
                name: hashlib.sha256(value.rstrip("\n").encode()).hexdigest()
                for name, value in scripts.items()
            },
            "config": config,
            "diagnostics": [],
            "configUid": cm["metadata"]["uid"],
        }

    def verify(
        self, *, paused: bool, fresh: bool = False, scheduler: bool = True
    ) -> dict[str, Any]:
        policy = self.checks.policy.passive
        fallback = (
            not policy.get("supported")
            or self.state.get("status") == "enabled-fallback"
            or self.state.get("fallbackIntent", False)
        )
        if fallback and paused:
            raise RuntimeError("unsupported passive policy cannot be declared paused")
        if scheduler and policy.get("supported"):
            self._scheduler()
        expected = (
            self._fallback_expected()
            if not policy.get("supported") or self.state.get("customSource")
            else self._expected(paused=paused)
        )
        inventory = self.checks._node_inventory()
        if paused:
            self.checks._verify_isolation()
            self.checks._reservation(self.checks.state["reservation"])
        digest = checks_digest(expected)
        old = self.state.get("acceptance", {})
        results = {}
        waiting = False
        periodic = policy.get("scheduler", {}).get("HealthCheckInterval", "0") != "0"
        # Bound transport to 16 workers; persist successful workers individually
        # so interruption never requires blindly repeating the whole cluster.
        with ThreadPoolExecutor(max_workers=min(16, len(inventory))) as pool:
            pending = {}
            for worker, resources in inventory.items():
                detail = {
                    **expected,
                    "resources": resources,
                    "reservation": self.checks.state.get("reservation", ""),
                    "baseline": self.state.get("baseline", {}).get(worker),
                }
                prior = (
                    old.get("workers", {}).get(worker) if old.get("policy") == digest else None
                ) or (self._worker_receipt(worker, digest) if fresh else None)
                mode = (
                    "paused"
                    if paused
                    else "accept"
                    if fresh and periodic and not fallback and prior is None
                    else "observe"
                )
                pending[pool.submit(self._observe, worker, detail, mode)] = (worker, detail, prior)
            for future in as_completed(pending):
                worker, detail, prior = pending[future]
                result = future.result()
                if result["hashes"] != expected["hashes"] or result["config"] != expected["config"]:
                    raise RuntimeError("effective passive policy has not converged")
                if paused and (result["running"] or not result.get("suppressed")):
                    raise RuntimeError("existing passive execution has not quiesced")
                baseline = detail.get("baseline")
                if (
                    fresh
                    and baseline
                    and (
                        result["identity"] != baseline["identity"]
                        or result["boot"] != baseline["boot"]
                    )
                ):
                    replacement = self._observe(worker, detail, "baseline")
                    self.state["baseline"][worker] = self._compact(replacement)
                    old.get("workers", {}).pop(worker, None)
                    self.checks._save()
                    self._worker_receipt(worker, digest, invalidate=True)
                    waiting = True
                    continue
                if result.get("pending"):
                    waiting = True
                    continue
                if fresh and prior is not None:
                    if result["identity"] == prior["identity"] and result["boot"] == prior["boot"]:
                        result = prior
                    else:
                        # A prior attempt may have saved the replacement baseline
                        # before invalidating its sidecar. Keep that boundary.
                        old.get("workers", {}).pop(worker, None)
                        self.checks._save()
                        self._worker_receipt(worker, digest, invalidate=True)
                        result = self._observe(
                            worker, detail, "observe" if fallback or not periodic else "accept"
                        )
                        if result.get("pending"):
                            waiting = True
                            continue
                result = self._compact(result)
                if fresh:
                    self._report_limitations(result)
                results[worker] = result
                if fresh and result != prior:
                    self._worker_receipt(worker, digest, result)
        if waiting:
            raise PassivePending("waiting for fresh native periodic passive evidence")
        self.state.update(
            status="enabled-fallback"
            if fallback
            else "accepted"
            if fresh
            else "paused"
            if paused
            else "restored",
            policy=digest,
            workers=results,
        )
        if fresh:
            self.state["acceptance"] = {
                "status": "enabled-fallback" if fallback else "accepted",
                "policy": digest,
                "workers": results,
            }
        if fallback:
            self.state.setdefault(
                "reason", policy.get("reason", "unsupported passive execution contract")
            )
            self.checks.emit(self.state["reason"])
        self.state.pop("fallbackIntent", None)
        self.checks._save()
        return self.state

    def begin_acceptance(self) -> None:
        workers = self.checks._node_inventory()
        baseline = dict(self.state.get("baseline", {}))
        missing = workers.keys() - baseline.keys()
        if not missing:
            return
        expected = (
            self._expected(paused=False)
            if self.checks.policy.passive.get("supported")
            else self._fallback_expected()
        )
        waiting = False
        with ThreadPoolExecutor(max_workers=min(16, len(missing))) as pool:
            pending = {
                pool.submit(self._observe, worker, expected, "baseline"): worker
                for worker in missing
            }
            for future in as_completed(pending):
                result = future.result()
                if (
                    result["hashes"] != expected["hashes"]
                    or result["config"] != expected["config"]
                    or result["running"]
                ):
                    waiting = True
                    continue
                baseline[pending[future]] = self._compact(result)
        self.state["baseline"] = baseline
        self.checks._save()
        if waiting:
            raise PassivePending("waiting for restored passive policy and native runner quiescence")

    def accept_ready(self) -> bool:
        try:
            self.begin_acceptance()
            self.verify(paused=False, fresh=True)
        except PassivePending:
            return False
        return True

    def collect_job(self, entry: dict[str, Any]) -> None:
        if (
            not self.checks.policy.passive.get("supported")
            or self.state.get("status") == "enabled-fallback"
        ):
            return
        if entry.get("passiveEvidence"):
            return
        job_id = entry["slurmIds"][0]
        raw = self.checks.slurm("scontrol show job " + job_id + " -o")
        live_job = dict(re.findall(r"(?:^|\s)(\w+)=(\S+)", raw))
        attempt = live_job.get("Restarts", "")
        if (
            live_job.get("JobId") != job_id
            or live_job.get("JobState") != "COMPLETED"
            or not attempt.isdecimal()
        ):
            raise RuntimeError("native passive acceptance lacks an exact completed Slurm attempt")
        workers = entry["slurmResult"]["nodes"]
        inventory = self.checks.state["acceptance"]["resources"]
        if not workers or any(worker not in inventory for worker in workers):
            raise RuntimeError("native passive job allocation is outside the worker inventory")
        evidence = {}
        for worker in workers:
            expected = {
                **self._expected(paused=False),
                "resources": inventory[worker],
                "baseline": self.state["baseline"][worker],
                "job": job_id,
                "attempt": attempt,
            }
            contexts = {}
            for context in ("prolog", "epilog"):
                result = self._observe(worker, {**expected, "context": context}, "job")
                if result.get("pending"):
                    raise PassivePending("waiting for native acceptance job hook evidence")
                if result["hashes"] != expected["hashes"] or result["config"] != expected["config"]:
                    raise RuntimeError("native passive job contract changed")
                baseline = expected["baseline"]
                if result["identity"] != baseline["identity"] or result["boot"] != baseline["boot"]:
                    raise RuntimeError(
                        "native passive job worker changed since acceptance admission"
                    )
                self._report_limitations(result)
                contexts[context] = self._compact(result)
            evidence[worker] = contexts
        if self.checks.slurm("scontrol show job " + job_id + " -o") != raw:
            raise RuntimeError("native passive Slurm attempt changed during observation")
        entry["passiveEvidence"] = {"job": job_id, "attempt": attempt, "workers": evidence}
        self.checks._save()

    def verify_acceptance(self, *, sealed: bool) -> None:
        proof = self.state.get("acceptance", {})
        if proof.get("status") not in {"accepted", "enabled-fallback"}:
            raise RuntimeError(
                "fresh passive acceptance or verified enabled fallback is incomplete"
            )
        if set(proof.get("workers", {})) != set(self.checks.state["acceptance"]["workers"]):
            raise RuntimeError("passive acceptance does not cover the complete worker inventory")
        if not sealed:
            self.verify(paused=False, fresh=True)

    def _source_restore(self, cm: Mapping[str, Any]) -> None:
        intent = self.state["sourceIntent"]
        if cm.get("metadata", {}).get("uid") != intent["uid"]:
            raise RuntimeError("source passive configuration ownership changed")
        actual = json.loads(cm.get("data", {}).get("checks.json", "null"))
        if actual not in (intent["before"], intent["after"]):
            raise RuntimeError(
                "source passive configuration independently changed; restoration ownership is unknown"
            )
        self.checks._patch(
            "configmap",
            "slurm-scripts",
            "soperator",
            {"data": {"checks.json": json.dumps(intent["before"])}},
            uid=intent["uid"],
        )
        self.state["fallbackIntent"] = True
        self.state["customSource"] = True
        self.state["reason"] = (
            "passive suppression unavailable; restored diagnostics and continuing enabled"
        )
        self.checks._save()
        self.verify(paused=False, scheduler=False)
        intent["restored"] = True
        self.checks._save()

    def pause_source(self) -> dict[str, Any]:
        if not self.checks.policy.passive.get("supported"):
            self.state["customSource"] = True
            return self.verify(paused=False, scheduler=False)
        self.checks._verify_isolation()
        reservation = self.checks._reservation(self.checks.state["reservation"])
        if reservation["users"] != ["root"]:
            raise RuntimeError("passive pause requires isolated maintenance")
        cm = self.checks._get("configmap", "slurm-scripts")
        uid = cm.get("metadata", {}).get("uid")
        if not uid:
            raise RuntimeError("native passive configuration is unavailable")
        before = self._expected(paused=False)
        after = self._expected(paused=True)
        actual = json.loads(cm.get("data", {}).get("checks.json", "null"))
        actual_hashes = {
            name: hashlib.sha256(cm.get("data", {}).get(name, "").rstrip("\n").encode()).hexdigest()
            for name in before["scripts"]
        }
        intent = self.state.get("sourceIntent")
        if intent and (
            intent.get("uid") != uid
            or intent.get("before") != before["config"]
            or intent.get("after") != after["config"]
        ):
            raise RuntimeError("source passive suppression binding changed")
        if intent and (
            intent.get("restored")
            or self.state.get("status") == "enabled-fallback"
            or self.state.get("fallbackIntent")
        ):
            self._source_restore(cm)
            return self.state
        if actual_hashes != before["hashes"] or actual not in (before["config"], after["config"]):
            if intent:
                self._source_restore(cm)
            else:
                self.state.update(
                    fallbackIntent=True,
                    customSource=True,
                    reason="custom source passive execution; keeping diagnostics enabled",
                )
                self.verify(paused=False, scheduler=False)
            return self.state
        if intent is None:
            if actual != before["config"]:
                raise RuntimeError("unowned passive suppression already exists")
            self.state["sourceIntent"] = {
                "uid": uid,
                "before": before["config"],
                "after": after["config"],
                "hashes": before["hashes"],
            }
            self.checks._save()
        self.checks._patch(
            "configmap",
            "slurm-scripts",
            "soperator",
            {"data": {"checks.json": json.dumps(after["config"])}},
            uid=uid,
        )
        try:
            return self.verify(paused=True, scheduler=False)
        except RuntimeError:
            self._source_restore(self.checks._get("configmap", "slurm-scripts"))
            return self.state
