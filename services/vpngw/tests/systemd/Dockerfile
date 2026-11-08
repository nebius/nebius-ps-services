# Disposable regression fixture, not a gateway image. Root/PID 1 and networking
# capabilities are required to exercise actual systemd and networkd behavior.
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus netplan.io iproute2 python3-pytest python3-yaml python3-pip python3-venv sudo openssh-server curl ca-certificates \
    nftables ufw iptables iputils-ping charon-systemd strongswan-swanctl strongswan-pki libcharon-extra-plugins libstrongswan-standard-plugins \
    && rm -rf /var/lib/apt/lists/*
# Exercise the supported FRR 10.5 policy/observer contract, not Ubuntu's 8.4 package.
RUN curl -fsSL https://deb.frrouting.org/frr/keys.asc -o /usr/share/keyrings/frrouting.asc \
    && printf '%s\n' 'deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr noble frr-10.5' > /etc/apt/sources.list.d/frr.list \
    && apt-get update && apt-get install -y --no-install-recommends \
       frr=10.5.5-0~ubuntu24.04.1 frr-pythontools=10.5.5-0~ubuntu24.04.1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /tmp/fixture-pyproject.toml
RUN python3 -m venv --system-site-packages /opt/vpngw-test \
    && /opt/vpngw-test/bin/python3 -c 'import subprocess, sys, tomllib; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *tomllib.load(open("/tmp/fixture-pyproject.toml", "rb"))["project"]["dependencies"]])'
# Production observation subprocesses intentionally use /usr/bin/python3.
# Supply the same pinned upstream client to that interpreter in this fixture.
RUN printf '%s\n' 'vici==6.0.3 --hash=sha256:734f52b57e329fbd58f3bffd1305d6546be174aeb35bc4a2ef45c257e95ad924' \
    | python3 -m pip install --no-cache-dir --break-system-packages --no-deps --require-hashes -r /dev/stdin
ENV container=docker PYTHONDONTWRITEBYTECODE=1 PATH="/opt/vpngw-test/bin:${PATH}"
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
