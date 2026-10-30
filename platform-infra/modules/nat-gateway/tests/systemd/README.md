# Isolated NAT SSH verification

From the repository root, with Docker and Terraform on `PATH`:

```bash
python3 platform-infra/modules/nat-gateway/tests/systemd/run.py
```

The development fixture builds a pinned Ubuntu 24.04 base, renders the actual
module cloud-init template with Terraform, and executes its files and bootstrap
commands in a disposable systemd container. Dependencies are installed from Ubuntu
repositories during the image build. It generates temporary test keys inside the
container; no real credentials are used or exported.

The fixture requires root and privileged networking to exercise systemd, iptables
and a private network namespace. It has private PID, cgroup and network namespaces,
read-only source mounts, no published ports, no host bus and no Docker socket.
Writable runtime state and test keys are disposable. The runner removes its exact
container and image tag on success or failure; it does not prune shared caches.
Do not run the test module directly on a gateway or the host.

Coverage includes both `ssh.service` and `ssh.socket`, key authentication,
root/password rejection, an IPv4-only listener, actual failed-public-key bans,
unban/expiry, NAT and fail2ban restarts, multiple agent identities, and rejection
of invalid effective SSH policy. The integration opt-in checks container presence,
root and systemd PID 1 before setup. Tests cannot qualify Nebius provisioning,
provider firewall attachments, workload forwarding or live route activation.

The cxcli CI verification job invokes the same runner. The image follows the
host architecture; a local ARM64 run does not establish AMD64 runtime results.
