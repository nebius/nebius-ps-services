# Disposable regression fixture, not a gateway image. Root/PID 1 and networking
# capabilities are required to exercise actual systemd and networkd behavior.
FROM ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd systemd-sysv dbus netplan.io iproute2 python3-pytest python3-yaml python3-pip sudo \
    && rm -rf /var/lib/apt/lists/*
ENV container=docker PYTHONDONTWRITEBYTECODE=1
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
