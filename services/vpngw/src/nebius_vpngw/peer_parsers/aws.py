import re


def parse(text: str) -> dict:
    """Parse AWS Site-to-Site VPN customer gateway config into normalized tunnel specs.

    AWS templates include two tunnels, each with:
    - Pre-Shared Key
    - Inside IP addresses (169.254.x.x/30)
    - Tunnel inside local/remote IPs
    - Optional phase1/phase2 proposals (IKE/ESP)
    """
    tunnels = []
    remote_asn = None

    # PSKs: "Pre-Shared Key" : xxxx
    psks = re.findall(r"(?i)pre\s*-\s*shared\s*key\s*[:=]\s*([A-Za-z0-9_!@#$%^&*\-]+)", text)

    # Inside CIDRs: "Inside IP Addresses: 169.254.x.x/30"
    cidrs = re.findall(r"(?i)inside\s*ip\s*addresses\s*[:=]\s*(169\.254\.\d+\.\d+/30)", text)
    local_ips = re.findall(r"(?i)customer\s*gateway\s*inside\s*address\s*[:=]\s*(169\.254\.\d+\.\d+)", text)
    remote_ips = re.findall(r"(?i)virtual\s*private\s*gateway\s*inside\s*address\s*[:=]\s*(169\.254\.\d+\.\d+)", text)

    # Public endpoints
    local_pub = re.findall(r"(?i)customer\s*gateway.*?(?:outside|public)\s*ip\s*address\s*[:=]\s*([0-9\.]+)", text)
    remote_pub = re.findall(r"(?i)virtual\s*private\s*gateway.*?(?:outside|public)\s*ip\s*address\s*[:=]\s*([0-9\.]+)", text)

    # Basic crypto proposals when present
    ike_props = re.findall(r"(?i)ike\s*encryption\s*[:=]\s*([A-Za-z0-9\-]+).*?ike\s*integrity\s*[:=]\s*([A-Za-z0-9\-]+).*?dh\s*group\s*[:=]\s*(\d+)", text, re.S)
    esp_props = re.findall(r"(?i)esp\s*encryption\s*[:=]\s*([A-Za-z0-9\-]+).*?esp\s*integrity\s*[:=]\s*([A-Za-z0-9\-]+).*?pfs\s*group\s*[:=]\s*(\d+)", text, re.S)

    # Remote ASN (VGW ASN)
    m_asn = re.search(r"(?i)(virtual\s*private\s*gateway|vgw)\s*asn\D+(\d+)", text)
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
        if i < len(ike_props):
            enc, integ, dh = ike_props[i]
            crypto.setdefault("ike_proposals", []).append(f"{enc}-{integ}-modp{dh}")
        if i < len(esp_props):
            enc, integ, pfs = esp_props[i]
            crypto.setdefault("esp_proposals", []).append(f"{enc}-{integ}")
        tunnels.append(
            {
                "psk": psk,
                "inner_cidr": inner_cidr,
                "inner_local_ip": il,
                "inner_remote_ip": ir,
                "local_public_ip": (local_pub[i] if i < len(local_pub) else None),
                "remote_public_ip": (remote_pub[i] if i < len(remote_pub) else None),
                "crypto": crypto,
            }
        )

    return {"tunnels": [t for t in tunnels if any(v for v in t.values())], "remote_asn": remote_asn}
