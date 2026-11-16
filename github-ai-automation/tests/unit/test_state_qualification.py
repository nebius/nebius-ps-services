import hashlib
import io
import threading
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from github_ai_automation.contracts import Blocked, digest
from github_ai_automation.state import S3Store
from github_ai_automation.state_qualification import qualify as run_qualification


def qualify(client, anonymous, bucket, production, trial):
    marker = {"schema": "state-isolation-sentinel/v1", "repository_id": 1}
    proof = {
        "schema": "state-sentinel-proof/v1",
        "repository_id": 1,
        "key": "github-ai-automation/qualification-sentinel.json",
        "body_sha256": digest(marker),
        "bucket_sha256": hashlib.sha256(production.encode()).hexdigest(),
    }
    return run_qualification(client, anonymous, bucket, production, trial, proof)


class VersionedS3:
    def __init__(self, *, ignore_conditions=False):
        self.rows = {}
        self.lock = threading.Lock()
        self.next_id = 0
        self.ignore_conditions = ignore_conditions

    def error(self, code):
        raise ClientError({"Error": {"Code": code}}, "test")

    def get_object(self, *, Bucket, Key):
        if Bucket != "qualification-bucket":
            self.error("AccessDenied")
        versions = self.rows.get(Key, [])
        if not versions:
            self.error("NoSuchKey")
        row = versions[-1]
        return {"Body": io.BytesIO(row["body"]), "ETag": row["id"]}

    def put_object(self, *, Key, Body, IfMatch=None, IfNoneMatch=None, **kwargs):
        with self.lock:
            versions = self.rows.setdefault(Key, [])
            if not self.ignore_conditions and (
                (IfNoneMatch and versions)
                or (IfMatch and (not versions or versions[-1]["id"] != IfMatch))
            ):
                self.error("PreconditionFailed")
            self.next_id += 1
            version = str(self.next_id)
            versions.append({"body": Body, "id": version})
            return {"ETag": version}

    def list_object_versions(self, *, Prefix, **kwargs):
        return {
            "Versions": [
                {"Key": k, "VersionId": v["id"]}
                for k, rows in self.rows.items()
                for v in rows
                if k.startswith(Prefix)
            ]
        }

    def delete_object(self, *, Key, VersionId, **kwargs):
        self.rows[Key] = [v for v in self.rows[Key] if v["id"] != VersionId]


def denied(**kwargs):
    raise ClientError({"Error": {"Code": "AccessDenied"}}, "get")


def test_storage_trial_checks_real_interface_and_deletes_only_owned_versions():
    client = VersionedS3()
    client.put_object(Key="unrelated/key", Body=b"preserve")
    result = qualify(
        client,
        SimpleNamespace(get_object=denied),
        "qualification-bucket",
        "production-bucket",
        "a" * 32,
    )
    assert len(result["checks"]) == 10 and all(result["checks"].values())
    assert (
        client.get_object(Bucket="qualification-bucket", Key="unrelated/key")["Body"].read()
        == b"preserve"
    )
    assert not client.list_object_versions(Prefix="qualification/")["Versions"]


def test_ignored_conditional_writes_fail_qualification_and_still_cleanup():
    client = VersionedS3(ignore_conditions=True)
    with pytest.raises(Blocked, match="STATE_CREATE_CONDITION_IGNORED"):
        qualify(
            client,
            SimpleNamespace(get_object=denied),
            "qualification-bucket",
            "production-bucket",
            "a" * 32,
        )
    assert not client.list_object_versions(Prefix="qualification/")["Versions"]


def test_production_bucket_cannot_be_selected_as_trial_target():
    with pytest.raises(Blocked, match="STATE_QUALIFICATION_TARGET_INVALID"):
        qualify(None, None, "same-bucket", "same-bucket", "a" * 32)


def test_missing_production_object_does_not_prove_isolation():
    client = VersionedS3()
    original = client.get_object

    def missing(**kwargs):
        if kwargs["Bucket"] == "production-bucket":
            client.error("NoSuchKey")
        return original(**kwargs)

    client.get_object = missing
    with pytest.raises(Blocked, match="STATE_ISOLATION_NOT_PROVEN"):
        qualify(
            client,
            SimpleNamespace(get_object=denied),
            "qualification-bucket",
            "production-bucket",
            "a" * 32,
        )
    assert client.next_id == 0


def test_provider_http_412_is_cas_conflict_with_nebius_error_name():
    def conflict(**kwargs):
        raise ClientError(
            {"Error": {"Code": "KeyAlreadyExists"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
            "PutObject",
        )

    store = S3Store(SimpleNamespace(put_object=conflict), "qualification-bucket")
    with pytest.raises(Blocked, match="^STATE_CONFLICT$"):
        store.put("probe", {"revision": 2}, None)


def test_rejected_preexisting_trial_preserves_all_original_versions():
    client = VersionedS3()
    key = "qualification/" + "a" * 32 + "/probe.json"
    client.put_object(Key=key, Body=b'{"previous":true}')
    with pytest.raises(Blocked, match="STATE_QUALIFICATION_TRIAL_ALREADY_STARTED"):
        qualify(
            client,
            SimpleNamespace(get_object=denied),
            "qualification-bucket",
            "production-bucket",
            "a" * 32,
        )
    assert (
        client.get_object(Bucket="qualification-bucket", Key=key)["Body"].read()
        == b'{"previous":true}'
    )


def test_existing_nonprobe_evidence_in_trial_prefix_is_preserved():
    client = VersionedS3()
    key = "qualification/" + "a" * 32 + "/earlier-evidence.json"
    client.put_object(Key=key, Body=b"{}")
    with pytest.raises(Blocked, match="STATE_QUALIFICATION_TRIAL_ALREADY_STARTED"):
        qualify(
            client,
            SimpleNamespace(get_object=denied),
            "qualification-bucket",
            "production-bucket",
            "a" * 32,
        )
    assert client.get_object(Bucket="qualification-bucket", Key=key)["Body"].read() == b"{}"


def test_prefix_specific_denial_cannot_hide_readable_production_sentinel():
    client = VersionedS3()
    original = client.get_object

    def readable(**kwargs):
        if (
            kwargs["Bucket"] == "production-bucket"
            and kwargs["Key"] == "github-ai-automation/qualification-sentinel.json"
        ):
            return {"Body": io.BytesIO(b"{}"), "ETag": "fixture"}
        return original(**kwargs)

    client.get_object = readable
    with pytest.raises(Blocked, match="STATE_ISOLATION_NOT_PROVEN"):
        qualify(
            client,
            SimpleNamespace(get_object=denied),
            "qualification-bucket",
            "production-bucket",
            "a" * 32,
        )
    assert client.next_id == 0


def test_ambiguous_owner_claim_cleans_only_its_attributable_write():
    from botocore.exceptions import ReadTimeoutError

    client = VersionedS3()
    original = client.put_object

    def lose_response(**kwargs):
        result = original(**kwargs)
        if kwargs["Key"].endswith("/owner.json"):
            raise ReadTimeoutError(endpoint_url="redacted")
        return result

    client.put_object = lose_response
    with pytest.raises(ReadTimeoutError):
        qualify(
            client,
            SimpleNamespace(get_object=denied),
            "qualification-bucket",
            "production-bucket",
            "a" * 32,
        )
    assert not client.list_object_versions(Prefix="qualification/")["Versions"]


def test_sentinel_observer_requires_exact_existing_fixture():
    from tests.support import MemoryStore

    from github_ai_automation.state_qualification import observe_sentinel

    store = MemoryStore()
    store.bucket = "production-bucket"
    store.key = lambda value: "github-ai-automation/" + value + ".json"
    with pytest.raises(Blocked, match="STATE_SENTINEL_UNPROVEN"):
        observe_sentinel(store, 1)
    store.put(
        "qualification-sentinel",
        {"schema": "state-isolation-sentinel/v1", "repository_id": 1},
        None,
    )
    assert observe_sentinel(store, 1)["key"] == "github-ai-automation/qualification-sentinel.json"
    with pytest.raises(Blocked, match="STATE_SENTINEL_UNPROVEN"):
        observe_sentinel(store, 2)


def test_foreign_sentinel_artifact_is_rejected_before_cloud_access(tmp_path, monkeypatch):
    import json

    from github_ai_automation import cli, controller

    class API:
        def pages(self, *args, **kwargs):
            return [{"name": "sentinel", "status": "completed", "conclusion": "success"}]

    monkeypatch.setattr(cli, "github", lambda: API())
    monkeypatch.setattr(
        cli,
        "state_qualification_run",
        lambda _: ({"id": 1}, {"id": 10, "run_attempt": 1, "head_sha": "a" * 40}),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("untrusted proof reached cloud configuration")

    monkeypatch.setattr(controller, "configured_store", forbidden)
    path = tmp_path / "proof.json"
    path.write_text(json.dumps({"repository_id": 2}))
    with pytest.raises(Blocked, match="STATE_SENTINEL_PRODUCER_UNTRUSTED"):
        cli.qualify_state(tmp_path / "result.json", path)
