# Disposable fixture only: no host networking, credentials or published ports.
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus iproute2 iptables openssh-server openssh-client \
    sudo fail2ban python3-systemd python3-pytest python3-yaml cloud-init \
    && rm -rf /var/lib/apt/lists/*
ENV container=docker PYTHONDONTWRITEBYTECODE=1
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
