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
You run bootstrap task against the existing cluster. It will bootstrap the VMs 
  with your global aws config. 

```sh
sky launch -c mycluster bootstrap-awscli-only.sky.yaml
```

---

## Features

This example demonstrates how to use a project-scoped SkyPilot configuration (`.sky.yaml`):

- Install SkyPilot and setup required Nebius credentials
- Launch Nebius clusters that only use internal (private) IPs
- Reach those nodes easily through a jump host using `ssh_proxy_command`
- Keep settings (Region/Project) scoped per project
- Make SSH key usage explicit and deterministic on the jump host
- Global AWS profile for Nebius S3: configures `~/.aws` with profiles `nebius` and region-scoped `nebius-<region>` so `sky check nebius` that read `~/.aws` work reliably. The optional bootstrap can mount your global `~/.aws` into the VM.
- Auto-load of `.envrc` on entry to the folder. `.envrc` loads only `.env` for this project.
- SSH to the cluster using `ssh cluster-name` without worrying about public IPs

 **Note:** `.env` is gitignored. Never commit your real credentials. Only `.env.placeholder` (a safe template) is tracked in the repo.

---

### Storage guidance

- Use `nebius://` storage paths in your Sky YAML for connectiong to the Nebius cloud object storage to avoid requiring AWS provider checks.
- For Nebius compute itself, SkyPilot uses `~/.nebius/credentials.json`.
- If you run the AWS CLI locally against Nebius S3, pass an explicit profile (for example: `--profile nebius-us-central1`).
- Optional bootstrap: `bootstrap-awscli-only.sky.yaml` mounts your global `~/.aws` and installs `awscli` on the VM. Use it only if your workloads execute the AWS CLI inside the VM. It does not export any env vars; use explicit `--profile` in your commands on the VM.

---

## Environment Variables

- Run the Nebius setup, first run it generates a `.env` file from `.env.placeholder`, and then you are able to enter the values in `.env` :
  ```sh
  ./nebiaus-sa-setup.sh
  ```
- So if `.env` has placeholder values (e.g., `tenant-EXAMPLE_ID`, `project-EXAMPLE_ID`), the script exits with an error. Open `./.env`, set real values for `TENANT_ID`, `PROJECT_ID`, `REGION_ID`, and `JUMP_HOST_IP`, then rerun `./nebiaus-sa-setup.sh`.

- Auto-load the `.envrc` when you cd to the folder. The setup script configures direnv and runs `direnv allow` so `.env` loads automatically. If direnv isn't available, you can enable it later; otherwise use the manual fallback below.

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
  - Add the jump host to `~/.ssh/known_hosts` (required because StrictHostKeyChecking=yes). If you have logged in one time to the jumphost from your laptop directly it's added already.

2) Make sure you have set your environment variables properly in .env file for the Region/Project and jump host IP address.

3) Launch a cluster

- From this project directory, run your normal SkyPilot workflows (examples):
  - `sky launch -c cluster7 mytask.yaml`
  - Reuse clusters with `-c <name>`

4) Connect via SSH

  `ssh <cluster-name>`

You will connect to the Head node of the cluster.

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

- If StrictHostKeyChecking blocks you, connect once directly to the jump host to add it to `known_hosts`.
- SkyPilot auto-generates SSH config under `~/.sky/generated/ssh` when you launch or start clusters. If entries look stale after changes, run a SkyPilot stop/start command to refresh them, then try SSH again.
- Switching regions: set `REGION_ID` in `.env`, rerun `./nebiaus-sa-setup.sh` to refresh profiles; for VM-side AWS CLI, always pass `--profile`.
- In this setup SkyPilot provisions Nebius instances with internal/private IPs.
- You connect to them via a proxy (jump host) using `ssh_proxy_command`.

