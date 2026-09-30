# Disposable regression fixture, not a gateway image. Root/PID 1 and networking
# capabilities are required to exercise actual systemd and networkd behavior.
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus netplan.io iproute2 python3-pytest python3-yaml python3-pip python3-venv sudo openssh-server \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /tmp/fixture-pyproject.toml
RUN python3 -m venv --system-site-packages /opt/vpngw-test \
    && /opt/vpngw-test/bin/python3 -c 'import subprocess, sys, tomllib; subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *tomllib.load(open("/tmp/fixture-pyproject.toml", "rb"))["project"]["dependencies"]])'
ENV container=docker PYTHONDONTWRITEBYTECODE=1
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
