import re


def parse(text: str) -> dict:
    """Parse Cisco IOS/ASA sample configuration into normalized tunnel specs.

    Common elements:
    - crypto isakmp key <psk> address <ip>
    - ip address <169.254.x.x> <255.255.255.252> on VTI
    - tunnel source/destination public IPs
    """
    tunnels = []

    psks = re.findall(r"(?i)crypto\s+isakmp\s+key\s+([^\s]+)\s+address\s+([0-9\.]+)", text)
    local_ips = re.findall(r"(?i)ip\s+address\s+(169\.254\.\d+\.\d+)\s+255\.255\.255\.252", text)
    # Derive CIDR from local IP if present
    cidrs = [f"{ip}/30" for ip in local_ips]

    for i in range(max(len(psks), len(local_ips), 2)):
        psk = (psks[i][0] if i < len(psks) else None)
        inner_cidr = (cidrs[i] if i < len(cidrs) else None)
        il = (local_ips[i] if i < len(local_ips) else None)
        tunnels.append(
            {
                "psk": psk,
                "inner_cidr": inner_cidr,
                "inner_local_ip": il,
                "inner_remote_ip": None,
                "crypto": {},
            }
        )

    return {"tunnels": [t for t in tunnels if any(v for v in t.values())]}
