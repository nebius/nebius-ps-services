import pytest

from nebius_vpngw.ipsec_observation import child_ready


def sas(name="edge", state="INSTALLED", ifid="00000064", protocol="ESP"):
    return (
        f"list-sa event {{{name} {{uniqueid=1 state=ESTABLISHED child-sas {{child-1 {{"
        f"name={name} state={state} protocol={protocol} if-id-in={ifid} if-id-out={ifid}"
        "}}}} list-sas reply {}"
    )


@pytest.mark.parametrize("state", ["INSTALLING", "REKEYING", "DELETING"])
def test_child_must_be_installed(state):
    assert not child_ready(sas(state=state), "edge", 100)


def test_exact_identity_protocol_and_both_hex_ids():
    assert child_ready(sas(), "edge", 100)
    assert not child_ready(sas(name="edge-backup"), "edge", 100)
    assert not child_ready(sas(protocol="AH"), "edge", 100)
    assert not child_ready(sas().replace("if-id-out=00000064", "if-id-out=00000065"), "edge", 100)
    assert child_ready(sas(ifid="00000100"), "edge", 256)
    assert not child_ready(sas(ifid="00000100"), "edge", 100)


def test_ike_only_and_empty_are_definitively_down():
    assert not child_ready(
        "list-sa event {edge {uniqueid=1 state=ESTABLISHED}} list-sas reply {}", "edge", 100
    )
    assert not child_ready("list-sas reply {}", "edge", 100)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "list-sa event {",
        sas().replace("list-sas reply {}", ""),
        sas().replace("uniqueid=1", "uniqueid=1 uniqueid=2"),
        sas().replace("if-id-in=00000064", "if-id-in=bad-value"),
    ],
)
def test_incomplete_or_ambiguous_observation_is_unknown(raw):
    with pytest.raises(ValueError):
        child_ready(raw, "edge", 100)


def test_installed_rekey_replacement_is_ready():
    raw = sas(state="DELETING").replace(" list-sas reply {}", "")
    raw += sas().replace("uniqueid=1", "uniqueid=2")
    assert child_ready(raw, "edge", 100)


@pytest.mark.parametrize(
    "raw",
    [
        "list-sas reply {success=no errmsg=unavailable}",
        "list-sa event {edge {uniqueid=1}} list-sas reply {}",
        sas().replace("state=INSTALLED ", ""),
    ],
)
def test_error_reply_or_missing_critical_state_is_unknown(raw):
    with pytest.raises(ValueError):
        child_ready(raw, "edge", 100)
