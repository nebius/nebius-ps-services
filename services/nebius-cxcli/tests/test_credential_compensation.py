from __future__ import annotations

import json
import traceback

import pytest

from nebius_cxcli.credential_compensation import (
    CREDENTIAL_JOURNAL_ROOT_ENV,
    CallbackCredentialDeliveryAdapter,
    CredentialCompensationError,
    CredentialCompensationJournal,
    CredentialDeliveryIntent,
)


def _journal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> CredentialCompensationJournal:
    monkeypatch.setenv(CREDENTIAL_JOURNAL_ROOT_ENV, str(tmp_path / "journals"))
    return CredentialCompensationJournal(project_id="project-a", scope="test")


def _intent() -> CredentialDeliveryIntent:
    return CredentialDeliveryIntent(
        kind="test-destination",
        target_sha256="sha256:" + "a" * 64,
        marker_sha256="sha256:" + "b" * 64,
        credential_ids_sha256="sha256:" + "c" * 64,
    )


def _adapter() -> CallbackCredentialDeliveryAdapter[object]:
    return CallbackCredentialDeliveryAdapter(
        kind="test-destination",
        target="test-target",
        deliver_callback=lambda _result, _intent_value: None,
    )


def test_compensation_deletes_operation_credentials_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    journal = _journal(monkeypatch, tmp_path)
    deleted: list[tuple[str, str]] = []
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="auth-public-key",
            ownership_sha256="sha256:" + "1" * 64,
            service_account_id="serviceaccount-a",
        )
        journal.record_created(kind="auth-public-key", resource_id="publickey-a")
        journal.record_intent(
            kind="access-key",
            ownership_sha256="sha256:" + "2" * 64,
            service_account_id="serviceaccount-a",
        )
        journal.record_created(kind="access-key", resource_id="accesskey-a")
        journal.compensate(delete=lambda kind, resource_id: deleted.append((kind, resource_id)))

    assert deleted == [
        ("access-key", "accesskey-a"),
        ("auth-public-key", "publickey-a"),
    ]


def test_failed_compensation_blocks_new_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    journal = _journal(monkeypatch, tmp_path)
    sentinel = "provider-response-secret=must-not-escape"
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="static-key",
            ownership_sha256="sha256:" + "3" * 64,
            service_account_id="serviceaccount-a",
        )
        journal.record_created(kind="static-key", resource_id="statickey-a")
        with pytest.raises(CredentialCompensationError, match="remains pending") as exc_info:
            journal.compensate(
                delete=lambda _kind, _resource_id: (_ for _ in ()).throw(OSError(sentinel))
            )
        rendered_traceback = "".join(
            traceback.format_exception(
                type(exc_info.value),
                exc_info.value,
                exc_info.value.__traceback__,
            )
        )
        assert sentinel not in str(exc_info.value)
        assert sentinel not in rendered_traceback
        with pytest.raises(CredentialCompensationError, match="must finish"):
            journal.begin()


def test_journal_contains_no_delivered_secret_material(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    journal = _journal(monkeypatch, tmp_path)
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="auth-public-key",
            ownership_sha256="sha256:" + "4" * 64,
            service_account_id="serviceaccount-a",
        )
        journal.record_created(kind="auth-public-key", resource_id="publickey-a")
        journal.record_delivery_intent(_intent())
        journal.mark_delivered()
    raw = journal.path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["status"] == "delivered"
    assert "private" not in raw.lower()
    assert "token" not in raw.lower()
    assert "secret" not in raw.lower()


def test_unresolved_mutation_gap_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    journal = _journal(monkeypatch, tmp_path)
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="access-key",
            ownership_sha256="sha256:" + "5" * 64,
            service_account_id="serviceaccount-a",
        )

    resumed = _journal(monkeypatch, tmp_path)
    with resumed.locked(), pytest.raises(CredentialCompensationError, match="resolved to no"):
        resumed.recover(
            delete=lambda _kind, _resource_id: None,
            resolve=lambda _item, _operation_id: (),
            delivery=_adapter(),
        )


def test_tampered_delivered_journal_with_unresolved_intent_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    journal = _journal(monkeypatch, tmp_path)
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="access-key",
            ownership_sha256="sha256:" + "6" * 64,
            service_account_id="serviceaccount-a",
        )
    payload = json.loads(journal.path.read_text(encoding="utf-8"))
    payload["status"] = "delivered"
    journal.path.write_text(json.dumps(payload), encoding="utf-8")
    journal.path.chmod(0o600)

    with (
        pytest.raises(CredentialCompensationError, match="delivery intent is missing"),
        _journal(monkeypatch, tmp_path).locked(),
    ):
        pass


def test_delivered_credentials_cannot_be_compensated(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    journal = _journal(monkeypatch, tmp_path)
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="static-key",
            ownership_sha256="sha256:" + "7" * 64,
            service_account_id="serviceaccount-a",
        )
        journal.record_created(kind="static-key", resource_id="statickey-a")
        journal.record_delivery_intent(_intent())
        journal.mark_delivered()
        with pytest.raises(CredentialCompensationError, match="cannot be compensated"):
            journal.compensate(delete=lambda _kind, _resource_id: None)


def test_invalid_issue_inputs_fail_before_credential_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    with pytest.raises(ValueError, match="project_id and scope"):
        CredentialCompensationJournal(project_id="", scope="test")

    journal = _journal(monkeypatch, tmp_path)
    with journal.locked():
        journal.begin()
        with pytest.raises(ValueError, match="unsupported compensated credential kind"):
            journal.record_intent(
                kind="unknown",
                ownership_sha256="sha256:" + "8" * 64,
                service_account_id="serviceaccount-a",
            )
        with pytest.raises(ValueError, match="ownership evidence is incomplete"):
            journal.record_intent(
                kind="access-key",
                ownership_sha256="",
                service_account_id="serviceaccount-a",
            )
        with pytest.raises(ValueError, match="result identity is required"):
            journal.record_created(kind="access-key", resource_id=" ")
        with pytest.raises(CredentialCompensationError, match="cannot complete unresolved intent"):
            journal.mark_delivered()


def test_ambiguous_delivery_recovery_preserves_created_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    journal = _journal(monkeypatch, tmp_path)
    with journal.locked():
        journal.begin()
        journal.record_intent(
            kind="access-key",
            ownership_sha256="sha256:" + "9" * 64,
            service_account_id="serviceaccount-a",
        )
        journal.record_created(kind="access-key", resource_id="accesskey-a")
        journal.record_delivery_intent(_intent())
        journal.mark_delivery_uncertain()

    deleted: list[tuple[str, str]] = []
    resumed = _journal(monkeypatch, tmp_path)
    with resumed.locked(), pytest.raises(CredentialCompensationError, match="ambiguous"):
        resumed.recover(
            delete=lambda kind, resource_id: deleted.append((kind, resource_id)),
            resolve=lambda _item, _operation_id: (),
            delivery=_adapter(),
        )

    assert deleted == []
