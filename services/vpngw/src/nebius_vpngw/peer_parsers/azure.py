import re


def parse(text: str) -> dict:
    """Parse Azure VPN Gateway sample configuration into normalized tunnel specs.

    Azure samples often include:
    - SharedKey "..."
    - IPsec/IKE policies (encryption/integrity/DH/PFS)
    - BGP neighbor addresses using APIPA (169.254.x.x)
    """
    tunnels = []
    remote_asn = None

    psks = re.findall(r"(?i)shared\s*key\s*['\"]([^'\"]+)['\"]", text)
    cidrs = re.findall(r"(169\.254\.\d+\.\d+/30)", text)
    local_ips = re.findall(r"(?i)customer.*?(169\.254\.\d+\.\d+)", text)
    remote_ips = re.findall(r"(?i)azure.*?(169\.254\.\d+\.\d+)", text)

    # Public endpoints
    local_pub = re.findall(r"(?i)(customer|local).*?(?:public|gateway)\s*ip\s*address\s*['\"]?([0-9\.]+)", text)
    remote_pub = re.findall(r"(?i)(azure|vpn\s*gateway).*?(?:public|gateway)\s*ip\s*address\s*['\"]?([0-9\.]+)", text)

    ike = re.findall(r"(?i)ike.*?encryption\s*[:=]\s*([A-Za-z0-9\-]+).*?integrity\s*[:=]\s*([A-Za-z0-9\-]+).*?dh\s*group\s*[:=]\s*(\d+)", text, re.S)
    esp = re.findall(r"(?i)ipsec|esp.*?encryption\s*[:=]\s*([A-Za-z0-9\-]+).*?integrity\s*[:=]\s*([A-Za-z0-9\-]+)", text, re.S)

    # Remote ASN (Azure VPN Gateway BGP ASN if present)
    m_asn = re.search(r"(?i)(azure|vpn\s*gateway|peer)\s*asn\D+(\d+)", text)
    if m_asn:
        try:
            remote_asn = int(m_asn.group(2))
        except Exception:
            remote_asn = None

    for i in range(max(len(psks), len(local_ips), len(remote_ips), len(cidrs), 2)):
        psk = (psks[i] if i < len(psks) else None)
        inner_cidr = (cidrs[i] if i < len(cidrs) else None)
        il = (local_ips[i] if i < len(local_ips) else None)
        ir = (remote_ips[i] if i < len(remote_ips) else None)
        crypto = {}
        if i < len(ike):
            enc, integ, dh = ike[i]
            crypto.setdefault("ike_proposals", []).append(f"{enc}-{integ}-modp{dh}")
        if i < len(esp):
            enc, integ = esp[i]
            crypto.setdefault("esp_proposals", []).append(f"{enc}-{integ}")
        tunnels.append(
            {
                "psk": psk,
                "inner_cidr": inner_cidr,
                "inner_local_ip": il,
                "inner_remote_ip": ir,
                "local_public_ip": (local_pub[i][1] if i < len(local_pub) else None),
                "remote_public_ip": (remote_pub[i][1] if i < len(remote_pub) else None),
                "crypto": crypto,
            }
        )

    return {"tunnels": [t for t in tunnels if any(v for v in t.values())], "remote_asn": remote_asn}
