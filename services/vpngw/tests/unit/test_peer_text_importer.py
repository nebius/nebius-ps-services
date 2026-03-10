from __future__ import annotations

from nebius_vpngw.peer_parsers import parse_text_document


def test_keyword_text_importer_detects_gcp_bgp_fields() -> None:
    parsed = parse_text_document(
        """
        Google Cloud HA VPN
        shared_secret = "gcp-psk"
        peerIpAddress: 169.254.10.1
        ipAddress: 169.254.10.2
        customer public ip address = 203.0.113.10
        google public ip address = 198.51.100.10
        Cloud Router ASN: 65030
        """,
        source_name="gcp-peer",
    )

    tunnel = parsed["tunnels"][0]
    assert parsed["vendor"] == "gcp"
    assert parsed["remote_asn"] == 65030
    assert tunnel["psk"] == "gcp-psk"
    assert tunnel["inner_cidr"] == "169.254.10.0/30"
    assert tunnel["local_public_ip"] == "203.0.113.10"
    assert tunnel["remote_public_ip"] == "198.51.100.10"


def test_keyword_text_importer_extracts_aws_crypto_and_public_ips() -> None:
    parsed = parse_text_document(
        """
        Amazon customer gateway configuration
        Pre-Shared Key: aws-psk
        Inside IP Addresses: 169.254.20.0/30
        Customer Gateway Inside Address: 169.254.20.1
        Virtual Private Gateway Inside Address: 169.254.20.2
        Customer Gateway outside ip address: 203.0.113.20
        Virtual Private Gateway outside ip address: 198.51.100.20
        IKE Encryption: aes256
        IKE Integrity: sha256
        DH Group: 14
        ESP Encryption: aes256
        ESP Integrity: sha256
        PFS Group: 14
        VGW ASN: 65040
        """,
        source_name="aws-peer",
    )

    tunnel = parsed["tunnels"][0]
    assert parsed["vendor"] == "aws"
    assert parsed["remote_asn"] == 65040
    assert tunnel["crypto"]["ike_proposals"] == ["aes256-sha256-modp14"]
    assert tunnel["crypto"]["esp_proposals"] == ["aes256-sha256"]
    assert tunnel["local_public_ip"] == "203.0.113.20"
    assert tunnel["remote_public_ip"] == "198.51.100.20"


def test_keyword_text_importer_extracts_azure_tunnel_metadata() -> None:
    parsed = parse_text_document(
        """
        Azure virtual network gateway
        Shared Key "azure-psk"
        169.254.30.0/30
        customer gateway ip address "203.0.113.30"
        azure vpn gateway ip address "198.51.100.30"
        customer APIPA 169.254.30.1
        azure APIPA 169.254.30.2
        Azure ASN: 65050
        IKE encryption: aes256
        integrity: sha256
        dh group: 14
        ESP encryption: aes256
        integrity: sha256
        """,
        source_name="azure-peer",
    )

    tunnel = parsed["tunnels"][0]
    assert parsed["vendor"] == "azure"
    assert parsed["remote_asn"] == 65050
    assert tunnel["psk"] == "azure-psk"
    assert tunnel["inner_cidr"] == "169.254.30.0/30"
    assert tunnel["local_public_ip"] == "203.0.113.30"
    assert tunnel["remote_public_ip"] == "198.51.100.30"


def test_keyword_text_importer_handles_cisco_snippets_without_vendor_module() -> None:
    parsed = parse_text_document(
        """
        Cisco IOS crypto isakmp
        crypto isakmp key cisco-psk address 198.51.100.40
        interface Tunnel1
         ip address 169.254.40.1 255.255.255.252
        """,
        source_name="cisco-peer",
    )

    tunnel = parsed["tunnels"][0]
    assert parsed["vendor"] == "cisco"
    assert tunnel["psk"] == "cisco-psk"
    assert tunnel["inner_cidr"] == "169.254.40.0/30"
    assert tunnel["inner_local_ip"] == "169.254.40.1"
    assert tunnel["remote_public_ip"] == "198.51.100.40"
