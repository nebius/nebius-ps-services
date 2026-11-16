import copy

from github_ai_automation.contracts import Blocked


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.revision = 0

    def read(self, key):
        value = self.data.get(key)
        return copy.deepcopy(value) if value else (None, None)

    def put(self, key, value, expected):
        current = self.read(key)[1]
        if current != expected:
            raise Blocked("STATE_CONFLICT")
        self.revision += 1
        revision = str(self.revision)
        self.data[key] = (copy.deepcopy(value), revision)
        return revision

    def scan(self, prefix):
        return sorted(k for k in self.data if k.startswith(prefix + "/"))


REQUEST_ID = "9" * 64


def general(store, repository_id=1):
    """Explicit fixture enrollment; an empty production store must stay disabled."""
    from github_ai_automation.rollout import key

    if store.read(key(repository_id))[0] is None:
        store.put(key(repository_id), {"schema_version": 1, "repository_id": repository_id,
                  "generation": 1, "phase": "general", "prs": [], "releases": []}, None)
    return store


def admitted(store, candidate, *, operation="process", capabilities=None):
    import time

    general(store, candidate.repository_id)
    record = {
        "schema_version": 2,
        "rollout_generation": 1,
        "id": REQUEST_ID,
        "status": "active",
        "operation": operation,
        "candidate": candidate.model_dump(),
        "deadline": int(time.time()) + 10000,
        "capabilities": capabilities
        or ["assess", "comment", "label", "close", "repair", "qualify", "merge"],
        "attempts": 0,
        "owned_heads": [],
        "stage": "usefulness",
        "origin": "manual",
        "epoch": "",
    }
    store.put(candidate.key + "/operation", record, None)
    return record


def stage_evidence(review):
    return {
        "usefulness": {
            "decision": "meaningful",
            "problem": "Regression",
            "benefit": "Fix behavior",
            "reviewed_paths": review.reviewed_paths,
            "evidence": ["src/a.py:1"],
            "counterevidence": "None found",
            "explanation": "Restores the documented behavior",
        },
        "review": review.model_dump(),
        "alignment": {
            "assessment": {
                "verdict": "pass",
                "reviewed_paths": review.reviewed_paths,
                "findings": [],
                "summary": "Aligned",
                "required_checks": ["structure"],
            },
            "checks": [{"id": "structure", "exit_code": 0}],
        },
    }
