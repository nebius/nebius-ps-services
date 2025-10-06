# Examples of SkyPilot Private Clusters via Jump Host
---

## TL;DR

Quick Start Path:

1) Install and set up

```sh
./skypilot-install.sh
source ~/venvs/skypilot-env/bin/activate
./nebiaus-sa-setup.sh
# If prompted, edit ./.env with real TENANT_ID/PROJECT_ID/REGION_ID/JUMP_HOST_IP and re-run
```

2) Verify

```sh
sky check nebius
```

3) Launch and connect

```sh
sky launch -c mycluster vmtask.yaml
ssh mycluster
```

Optional (only if your VM workload needs AWS CLI against Nebius S3):

```sh
sky launch -c mycluster bootstrap-awscli-only.sky.yaml
```

Notes:
- When you use Nebius object storage `nebius://` in YAML; you don’t need AWS config bootstrap on the VM.
- If you use AWS CLI (locally or on the VM), pass an explicit profile, for example `--profile nebius-us-central1`.

This example demonstrates how to use a project-scoped SkyPilot configuration (`.sky.yaml`) to:

- Install SkyPilot and setup required Nebius credentials
- Launch Nebius clusters that only use internal (private) IPs
- Reach those nodes easily through a jump host using `ssh_proxy_command`
- Keep provider/account/region settings (Tenant/Region/Project) neatly scoped per project
- Make SSH key usage explicit and deterministic on the jump host
- Regional isolation only: creates region-scoped AWS profiles like `nebius-us-central1`.
  - Profile-level endpoint: configures a profile-level `endpoint_url` in `~/.aws/config` for the `nebius` and `nebius-<region>` profiles. Note: this overrides non‑S3 AWS APIs for that profile (e.g., AWS STS checks may fail). That’s expected if you’re not using AWS.
  - Global AWS profile for Nebius S3: configures `~/.aws` with profiles `nebius` and region-scoped `nebius-<region>` so `sky check nebius` and tools that read `~/.aws` work reliably. The optional bootstrap can mount your global `~/.aws` into the VM; no project-local mirroring.
  - Auto-load on cd: `.envrc` loads only `.env` for this project.
The pattern is great for teams: developers can `ssh cluster-name` without worrying about public IPs, while ops teams keep connectivity controlled through a central jump host.
 **Note:** `.env` is gitignored. Never commit your real credentials. Only `.env.placeholder` (a safe template) is tracked in the repo.

### Storage guidance

- Prefer `nebius://` storage paths in your Sky YAML to avoid requiring AWS provider checks.
- For Nebius compute itself, SkyPilot uses `~/.nebius/credentials.json` and does not require AWS credentials.
- If you run the AWS CLI locally against Nebius S3, pass an explicit profile (for example: `--profile nebius-us-central1`). Setting a profile-level `endpoint_url` may cause `sky check aws` or AWS STS calls to fail; that’s expected and harmless if you’re not using AWS.
- Optional bootstrap: `bootstrap-awscli-only.sky.yaml` mounts your global `~/.aws` and installs `awscli` on the VM. Use it only if your workloads execute the AWS CLI inside the VM. It does not export any env vars; use explicit `--profile` in your commands on the VM.
To install SkyPilot with Nebius support, run the provided script (idempotent, safe to re-run):

If your workload needs to access Nebius S3 from the VM using the AWS CLI, use the optional bootstrap documented below. If your YAML only uses `nebius://` paths and you don’t call `aws` on the VM, you can skip that step.
Regional isolation for S3:
  - The setup creates a base profile `nebius` and region-specific profiles like `nebius-us-central1` in `~/.aws`.
  - Switching regions: set `REGION_ID` in `.env`, rerun `./nebiaus-sa-setup.sh` to refresh profiles, or switch to another project folder.
Check Nebius Object Storage via AWS CLI (use explicit `--profile`):
```sh
source ~/venvs/skypilot-env/bin/activate
```

After install, verify your setup:

```sh
sky check nebius
```

---

## Environment Variables

This project uses environment variables for sensitive configuration. Quick start:

- Run the Nebius setup (first run bootstraps `.env` from `.env.placeholder` with an auto-generated header, then validates values):
  ```sh
  ./nebiaus-sa-setup.sh
  ```
  - If `.env` has placeholder values (e.g., `tenant-EXAMPLE_ID`, `project-EXAMPLE_ID`), the script exits with an error. Open `./.env`, set real values for `TENANT_ID`, `PROJECT_ID`, `REGION_ID`, and `JUMP_HOST_IP`, then rerun `./nebiaus-sa-setup.sh`.

- Auto-load on cd (recommended): the setup script configures direnv and runs `direnv allow` when possible so `.env` loads automatically. If direnv isn't available, you can enable it later; otherwise use the manual fallback below.

  - Manual fallback (no direnv):
     ```sh
     source .env
     ```

**Note:** `.env` is gitignored. Never commit your real credentials. Only `.env.placeholder` (a safe template) is tracked in the repo.

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

Environment handling:
- Prefer `nebius://` paths. If you must use the AWS CLI locally, pass `--profile` explicitly (for example, `--profile nebius-us-central1`).
- For Nebius compute itself, SkyPilot uses `~/.nebius/credentials.json` and does not require AWS credentials.

3) Connect via SSH

- After a cluster is up, run:

  ssh <cluster-name>
You will connect to the Head node of the cluster.
- SkyPilot’s generated SSH config (included from `~/.sky/generated/ssh/*`) will apply the proxy and log you in to the VM’s default user (e.g., `ubuntu`).

---


## Repository contents

- `.sky.yaml` — Project-level SkyPilot config for Nebius (scopes region/project/tenant per folder).
- `.sky.yaml.template` — Template for generating `.sky.yaml` from environment variables.
- `.env` — Project secrets/config (gitignored, user-specific).
- `.env.placeholder` — Example env file to copy and fill in.
- `.envrc` — Loads `.env` on entering the directory if direnv is enabled.
- `nebiaus-sa-setup.sh` — Script to create Nebius service account, credentials, and configure AWS CLI profiles for Nebius S3 access.
- `bootstrap-awscli-only.sky.yaml` — Optional bootstrap to mount your global `~/.aws/*` into the VM and install `awscli`. Use only if your VM workload calls the AWS CLI.
- `skypilot-install.sh` — Idempotent SkyPilot install script (creates venv, installs SkyPilot).
- `generate-sky-config.sh` — Renders `.sky.yaml` from `.sky.yaml.template` using envsubst.
- `.gitignore` — Ensures secrets/configs are not committed (includes `.env`, `.sky.yaml`).

SkyPilot automatically reads `.sky.yaml` when you run `sky` commands inside this folder. It overrides user-global config for this project only, making it easy to manage per-project defaults.

---

## Tips

- If SSH tries the wrong key on the jump host, keep `-o IdentityFile=...` and `-o IdentitiesOnly=yes` in `ssh_proxy_command` (see `.sky.yaml`).
- If StrictHostKeyChecking blocks you, connect once directly to the jump host to add it to `known_hosts`.
- SkyPilot auto-generates SSH config under `~/.sky/generated/ssh` when you launch or start clusters. If entries look stale after changes, run a SkyPilot stop/start command to refresh them, then try SSH again.
- Switching regions: set `REGION_ID` in `.env`, rerun `./nebiaus-sa-setup.sh` to refresh profiles; for VM-side AWS CLI, always pass `--profile`.
  - SkyPilot provisions Nebius instances with only internal/private IPs.

  - You connect to them via a proxy (jump host) using `ssh_proxy_command`.

