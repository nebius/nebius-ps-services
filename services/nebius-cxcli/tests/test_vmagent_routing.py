import copy

import pytest

from nebius_cxcli.vmagent_routing import replace_destinations

LOCAL = "http://internal-victoriametrics-service:8428/api/v1/write"
REMOTE = "https://external-vm.example.com/api/v1/write"


def test_both_to_local_removes_external_url_and_all_destination_auth():
    spec = {
        "remoteWrite": [
            {
                "url": REMOTE,
                "bearerTokenSecret": {"name": "remote", "key": "token"},
                "tlsConfig": {"serverName": "external"},
                "inlineUrlRelabelConfig": [{"action": "drop", "regex": "secret"}],
            },
            {"url": LOCAL},
        ],
        "extraArgs": {
            "remoteWrite.url": REMOTE,
            "remoteWrite.bearerTokenFile": "/remote/token,",
            "remoteWrite.tlsCAFile": "/remote/ca,",
            "remoteWrite.basicAuth.username": "remote,",
            "remoteWrite.urlRelabelConfig": "/remote/relabel,",
            "remoteWrite.queues": "2",
            "remoteWrite.flushInterval": "2s",
            "remoteWrite.tmpDataPath": "/var/lib/vmagent",
            "promscrape.streamParse": "true",
        },
    }
    replace_destinations(spec, [{"url": LOCAL}])
    assert spec["remoteWrite"] == [{"url": LOCAL}]
    assert REMOTE not in str(spec)
    assert "/remote/" not in str(spec)
    assert spec["extraArgs"] == {
        "remoteWrite.queues": "2",
        "remoteWrite.flushInterval": "2s",
        "remoteWrite.tmpDataPath": "/var/lib/vmagent",
        "promscrape.streamParse": "true",
    }
    before = copy.deepcopy(spec)
    replace_destinations(spec, [{"url": LOCAL}])
    assert spec == before


@pytest.mark.parametrize(
    "destinations", [[{"url": LOCAL}], [{"url": REMOTE}], [{"url": LOCAL}, {"url": REMOTE}]]
)
def test_exact_local_remote_both_destinations(destinations):
    spec = {"remoteWrite": [{"url": REMOTE}, {"url": LOCAL}]}
    replace_destinations(spec, destinations)
    assert spec["remoteWrite"] == destinations
    assert not any("disable" in key for key in spec)


def test_positional_tuning_cannot_follow_wrong_destination():
    spec = {
        "remoteWrite": [{"url": REMOTE}, {"url": LOCAL}],
        "extraArgs": {"remoteWrite.queues": "2,8"},
    }
    with pytest.raises(ValueError, match="explicit remapping"):
        replace_destinations(spec, [{"url": LOCAL}])


def test_existing_external_first_queue_cannot_be_silently_orphaned():
    from nebius_cxcli.vmagent_routing import assert_queue_identity_preserved

    with pytest.raises(RuntimeError, match="queue handoff"):
        assert_queue_identity_preserved([REMOTE, LOCAL], [LOCAL])
    assert_queue_identity_preserved([LOCAL, REMOTE], [LOCAL])
    assert_queue_identity_preserved([], [LOCAL])
