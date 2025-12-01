import re


def parse(text: str) -> dict:
    """Parse GCP HA VPN config into normalized tunnel specs.

    Expected fields in GCP download config (device templates vary):
    - Remote public IP(s)
    - Shared secret (PSK)
    - VTI / link IPs (169.254.x.x) per tunnel
    - Cloud Router ASN and BGP neighbor IPs (optional)
    This parser uses regex heuristics to extract common details.
    """
    tunnels = []
    remote_asn = None

    # Extract PSKs (often shown as: shared_secret = "..." or 'IPSec Shared Secret')
    psks = re.findall(r"(?i)(shared\s*secret|ipsec shared secret)\s*[:=]\s*['\"]([^'\"]+)['\"]", text)

    # Extract inner /30 pairs (169.254.x.x/30 or local/remote IP)
    cidrs = re.findall(r"(169\.254\.\d+\.\d+/30)", text)
    local_ips = re.findall(r"(?i)(?:local|customer)\s*ip\s*[:=]\s*(169\.254\.\d+\.\d+)", text)
    remote_ips = re.findall(r"(?i)(?:remote|cloud)\s*ip\s*[:=]\s*(169\.254\.\d+\.\d+)", text)

    # Public endpoints (heuristic)
    local_pub = re.findall(r"(?i)(customer|local).*?(?:public|outside|external)\s*ip\s*address\s*[:=]\s*([0-9\.]+)", text)
    remote_pub = re.findall(r"(?i)(google|gcp|peer|remote).*?(?:public|outside|external)\s*ip\s*address\s*[:=]\s*([0-9\.]+)", text)

    # Remote ASN (Cloud Router ASN)
    m_asn = re.search(r"(?i)(remote|cloud\s*router)\s*asn\D+(\d+)", text)
    if m_asn:
        try:
            remote_asn = int(m_asn.group(2))
        except Exception:
            remote_asn = None

    # Build up to two tunnels if we have pairs
    for i in range(max(len(psks), len(local_ips), len(remote_ips), len(cidrs), 2)):
        psk = (psks[i][1] if i < len(psks) else None)
        inner_cidr = (cidrs[i] if i < len(cidrs) else None)
        il = (local_ips[i] if i < len(local_ips) else None)
        ir = (remote_ips[i] if i < len(remote_ips) else None)
        tunnels.append(
            {
                "psk": psk,
                "inner_cidr": inner_cidr,
                "inner_local_ip": il,
                "inner_remote_ip": ir,
                "local_public_ip": (local_pub[i][1] if i < len(local_pub) else None),
                "remote_public_ip": (remote_pub[i][1] if i < len(remote_pub) else None),
                "crypto": {},
            }
        )

    return {"tunnels": [t for t in tunnels if any(v for v in t.values())], "remote_asn": remote_asn}
