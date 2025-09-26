# Jump Host (SSH Bastion) on Nebius using cloud-init

This project provides a hardened cloud-init configuration to create a minimal SSH bastion (jump host) VM on Nebius:

- Creates a non-root admin user (`nebius-user`) with passwordless sudo
- Enforces SSH key authentication (no passwords, no root login)
- Installs and configures UFW to default-deny inbound and allow only trusted client IPs for SSH
- Enables basic security tooling: fail2ban, unattended-upgrades, auditd
- Adds a systemd timer to log when a reboot is required after security updates

The allowlist of client IPs for SSH is driven by `/etc/bastion_allowed_cidrs` (one CIDR per line). You should add your current public IPv4 as a `/32` before provisioning to avoid lockout.


## Files in this repo

- `cloud-init.sh` — cloud-init user-data to paste into Nebius during VM creation. It contains:
  - SSH hardening under `/etc/ssh/sshd_config.d/50-bastion.conf`
  - UFW rules derived from `/etc/bastion_allowed_cidrs`
  - Security services (fail2ban, auditd, unattended-upgrades)
  - A setup script `/usr/local/bin/setup-bastion.sh` executed once at boot


## Prerequisites

- A Nebius account and permission to create a VM with public IP
- Your SSH keypair on your local machine (public key at `~/.ssh/id_ed25519.pub` or `~/.ssh/id_rsa.pub`)
- Your current public IPv4 address to allow (find it with: `curl -4 -s https://ifconfig.me`)


## Prepare the cloud-init

1) Replace the placeholder example under `users.ssh_authorized_keys` in `cloud-init.sh` with YOUR public SSH key (do not paste the private key). To print your public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

2) Pre-fill your client IP allowlist so UFW will permit SSH from your location. In `cloud-init.sh`, locate the `write_files:` entry for `/etc/bastion_allowed_cidrs` and add one CIDR per line, for example:

```
203.0.113.45/32
198.51.100.0/24
```

Tip to get your current IPv4/32 quickly:

```bash
echo "$(curl -4 -s https://ifconfig.me)/32"
```


## Create the VM on Nebius (console)

- In Nebius Console, start creating a new VM instance with a public IP.
- Find the section to provide user data (cloud-init). Choose the option to paste the script.
- Paste the entire contents of `cloud-init.sh`.
- Complete the VM creation.


## First connection (from your laptop)

```bash
ssh -i ~/.ssh/id_ed25519 nebius-user@<VM_PUBLIC_IP>
```

If you get a timeout:
- Verify Nebius security group/firewall allows port 22 from your IP
- Ensure your IP/CIDR is present in `/etc/bastion_allowed_cidrs` (it’s seeded from cloud-init)


## Using the VM as an SSH Proxy (Jump Host)

- ProxyCommand (OpenSSH) for one-off hops:

```bash
ssh -J nebius-user@<JUMP_IP> target-user@<TARGET_PRIVATE_IP>
```

- ProxyJump in your laptop SSH config (`~/.ssh/config`) for convenience:

```
Host jumphost
  HostName <JUMP_IP>
  User nebius-user

Host target
  HostName <TARGET_PRIVATE_IP>
  User target-user
  ProxyJump jumphost
```

Then simply:

```bash
ssh target
```

## Admin: add a new user and key

1) Create the user on the jump host (SSH in first as `nebius-user`):

```bash
sudo adduser --disabled-password --gecos "" alice
sudo mkdir -p /home/alice/.ssh
sudo sh -c 'echo "ssh-ed25519 AAAA... alice@laptop" > /home/alice/.ssh/authorized_keys'
sudo chown -R alice:alice /home/alice/.ssh
sudo chmod 700 /home/alice/.ssh
sudo chmod 600 /home/alice/.ssh/authorized_keys
```

2) Optionally grant sudo:

```bash
sudo usermod -aG sudo alice
```


## Admin: allow new external IPs for SSH (idempotent)

The setup script is idempotent and safe to run multiple times. To allow a new client network:

1) Add the CIDR to `/etc/bastion_allowed_cidrs` (one per line):

```bash
echo "203.0.113.200/32" | sudo tee -a /etc/bastion_allowed_cidrs
```

2) Apply the change by re-running the setup script (no reboot required):

```bash
sudo bash /usr/local/bin/setup-bastion.sh
```

3) Verify UFW rules include your new source:

```bash
sudo ufw status verbose
```


## Logs and troubleshooting

- One-time setup log:

```bash
sudo tail -n 200 /var/log/bastion-setup.log
```

- Service status:

```bash
systemctl status ssh ufw fail2ban auditd
```

- Verify a user you created exists and can be granted sudo if intended:

```bash
getent passwd alice || id alice
groups alice
sudo -l -U alice || true
```

- Check UFW rules:

```bash
sudo ufw status verbose
```

- If SSH times out, verify both the Nebius SG/firewall and the VM’s UFW allow your source IP, and that the SSH daemon is listening:

```bash
sudo ss -lntp | grep :22
```


## Security notes

- SSH password auth is disabled; only keys are allowed by default.
- Root login via SSH is disabled.
- UFW defaults to deny inbound.
- fail2ban reduces brute force attempts.
- unattended-upgrades applies security updates; a systemd timer logs when a reboot is needed.


## License

This project is provided as-is; review and adapt security settings to your environment’s policies.
