# Example of SkyPilot Private Clusters via Jump Host

This example demonstrates how to use a project-scoped SkyPilot configuration (`.sky.yaml`) to:

- Launch Nebius clusters that only use internal (private) IPs
- Reach those nodes easily through a jump host using `ssh_proxy_command`
- Keep provider/account/region settings (Tenant/Region/Project) neatly scoped per project
- Make SSH key usage explicit and deterministic on the jump host

The pattern is great for teams: developers can `ssh cluster-name` without worrying about public IPs, while ops teams keep connectivity controlled through a central jump host.

---

## Repository contents

- `.sky.yaml` — Project-level SkyPilot config for Nebius.

SkyPilot automatically reads this file when you run `sky` commands inside this folder. It overrides user-global config for this project only, making it easy to manage per-project defaults.

---

## How it works

### 1) Private VMs on Nebius

In `.sky.yaml`, we set:

- `use_internal_ips: true`
  - SkyPilot provisions Nebius instances with only internal/private IPs.
  - You connect to them via a proxy (jump host) using `ssh_proxy_command`.

- `use_static_ip_address: false`
  - Do not assign public IP address.

This combination ensures nodes are not exposed with public addresses. The jump host is the single ingress path.

### 2) Easy SSH via jump host

- `ssh_proxy_command` is passed to SSH as `-o ProxyCommand`. We set it to use an existing jump host and the user’s explicit SSH key:

  - `-W %h:%p` forwards stdin/stdout to the destination host and port (SSH proxy).
  - `-o IdentityFile=~/.ssh/id_ed25519` selects the exact key to use for the jump host.
  - `-o IdentitiesOnly=yes` prevents OpenSSH from trying other keys (deterministic behavior).
  - `StrictHostKeyChecking=yes` ensures the jump host’s key must be in `~/.ssh/known_hosts`.

SkyPilot then writes a local SSH config include so you can `ssh <cluster-name>` directly:

- `~/.ssh/config` includes `~/.sky/generated/ssh/*`
- SkyPilot generates per-cluster `Host` entries there, which embed the ProxyCommand.

### 3) Per-project provider settings

The `.sky.yaml` captures Nebius-specific context:

- `tenant_id` — Nebius tenant (account) identifier
- `region_configs.<region>.project_id` — Nebius project ID used in that region
- `domain` — Nebius API endpoint

- Scopes credentials and region choices per project
- Reduces CLI flag usage and copy/paste between teammates
- Allows consistent behavior across all `sky` commands run in this folder

### 4) Users and identities: jump host vs. target VMs

- Jump host: users log in as their own accounts (e.g., `nebius-user`), so you can audit who connected to the jump.
- Target VMs: everyone lands as `ubuntu` (the image’s default user). The ProxyCommand only controls the jump hop; the final user is independent.

This is a common and acceptable pattern:

- You can audit who reached the jump host based on their usernames and key fingerprints.
- On the target VMs, you can still attribute access by SSH key fingerprints even if the login user is `ubuntu`.

---

## File: `.sky.yaml` (annotated)

Key fields used in this example:

- `nebius.tenant_id` and `nebius.domain` — Your Nebius account context and API endpoint.
- `nebius.use_internal_ips: true` — Only internal IPs; no public IPs on nodes.
- `nebius.use_static_ip_address: false` — Only relevant if public IPs are enabled; ignored here.
- `nebius.ssh_proxy_command` — Proxy through a jump host with an explicit key.
- `nebius.region_configs` — The specific Nebius region and project id.

---

## Usage

1) Prepare the jump host

- Ensure the jump host is reachable at `<JUMP_PUBLIC_IP>` and you can log in with your chosen key:
  - Place your key at `~/.ssh/id_ed25519` (or set the path you use in `.sky.yaml`).
  - Add the jump host to `~/.ssh/known_hosts` (required because StrictHostKeyChecking=yes). If you have logged in one time to it, it's added already.

2) Launch a cluster

- From this project directory, run your normal SkyPilot workflows (examples):
  - `sky launch -c cluster7 mytask.yaml`
  - Reuse clusters with `-c <name>`

3) Connect via SSH

- After a cluster is up, run:

  ssh <cluster-name>
You will connect to the Head node of the cluster.
- SkyPilot’s generated SSH config (included from `~/.sky/generated/ssh/*`) will apply the proxy and log you in to the VM’s default user (e.g., `ubuntu`).

---

## Tips and troubleshooting

- If SSH tries the wrong key on the jump host, keep `-o IdentityFile=...` and `-o IdentitiesOnly=yes` in `ssh_proxy_command` as shown here.
- If StrictHostKeyChecking blocks you, connect once directly to the jump host to add it to `known_hosts`:

  ssh nebius-user@<JUMP_PUBLIC_IP> -o IdentityFile=~/.ssh/id_ed25519 -o IdentitiesOnly=yes

- `sky status -v` refreshes local SSH config entries if you change cluster state.
- Username on targets: to change from `ubuntu`, you must ensure that user exists on the VM image or is created during provisioning; then set `nebius.ssh_user` accordingly.

---

## Security notes (multi-user setups)

- Keep per-user accounts on the jump host; avoid shared accounts there.
- Use distinct SSH keys per user; include helpful comments in public keys (e.g., `alice@laptop-2025`).
- Consider enabling `LogLevel VERBOSE` and `SyslogFacility AUTHPRIV` in sshd on jump and targets for better key fingerprint logging.
- Disable SSH agent forwarding if not needed; restrict port forwarding on the jump host as appropriate.

