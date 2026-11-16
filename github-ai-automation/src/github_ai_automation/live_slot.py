"""A live trial retains its global reservation until independently verified cleanup."""

from .contracts import Blocked, Candidate
from .controller import Heartbeat
from .live import recover_cleanup, require_cleanup
from .state import acquire


class CombinedLease:
    def __init__(self, *leases):
        self.leases = leases

    def assert_owned(self):
        for lease in self.leases:
            lease.assert_owned()


def reserve(store, repository_id, candidate, run_id, attempt, lease):
    key = f"repositories/{repository_id}/live_slot"
    previous, revision = store.read(key)
    if previous and previous["status"] != "clean":
        raise Blocked("LIVE_TRIAL_RESERVED")
    value = {
        "status": "occupied",
        "candidate": candidate.model_dump(),
        "run_id": run_id,
        "attempt": attempt,
    }
    lease.assert_owned()
    store.put(key, value, revision)
    return value


def clean(store, repository_id, reservation, lease):
    key = f"repositories/{repository_id}/live_slot"
    current, revision = store.read(key)
    if current != reservation:
        raise Blocked("LIVE_RESERVATION_CHANGED")
    require_cleanup(store, Candidate.model_validate(current["candidate"]))
    lease.assert_owned()
    store.put(key, {**current, "status": "clean"}, revision)


def recover(api, store, root, policy, repository_id):
    value, _ = store.read(f"repositories/{repository_id}/live_slot")
    if not value or value["status"] == "clean":
        return False
    run = api.request(f"actions/runs/{value['run_id']}/attempts/{value['attempt']}")
    if (
        run["repository"]["id"] != repository_id
        or run["run_attempt"] != value["attempt"]
        or run["path"] != ".github/workflows/github-ai-automation.yml"
    ):
        raise Blocked("LIVE_OWNER_UNTRUSTED")
    if run["status"] != "completed":
        raise Blocked("LIVE_OWNER_NOT_TERMINAL")
    candidate = Candidate.model_validate(value["candidate"])
    global_lease = acquire(store, f"repositories/{repository_id}/live_lock")
    try:
        lease = acquire(store, candidate.key + "/lock")
        with Heartbeat(global_lease), Heartbeat(lease):
            try:
                combined = CombinedLease(global_lease, lease)
                combined.assert_owned()
                current, _ = store.read(f"repositories/{repository_id}/live_slot")
                if current != value:
                    raise Blocked("LIVE_RESERVATION_CHANGED")
                recover_cleanup(
                    candidate=candidate,
                    policy=policy,
                    trusted_root=root,
                    context={"environment": "github-ai-live-test"},
                    lease=combined,
                    store=store,
                )
                clean(store, repository_id, value, combined)
                return True
            finally:
                lease.release()
    finally:
        global_lease.release()
