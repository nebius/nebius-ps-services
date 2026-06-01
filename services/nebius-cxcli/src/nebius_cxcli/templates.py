"""Static templates used by nebius-cxcli."""

from __future__ import annotations

import re
from textwrap import dedent

from . import __version__

NEBIUS_PS_SERVICES_REPO = "https://github.com/nebius/nebius-ps-services.git"
NEBIUS_PROVIDER_SOURCE = "nebius/nebius"
NEBIUS_PROVIDER_VERSION = ">= 0.6.8, < 0.7.0"


def _is_stable_semver(version: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", version) is not None


def default_cli_ref() -> str:
    """Return the git ref used by generated customer workflows."""
    if _is_stable_semver(__version__):
        return f"nebius-cxcli-v{__version__}"
    return "main"


def _generated_workflow_path_glob(deployments_dir: str) -> str:
    """Return the generated-bundle glob used by customer workflow triggers."""
    normalized = deployments_dir.strip().strip("/")
    if normalized in {"", "."}:
        return "*/*/generated/**"
    return f"**/{normalized}/*/*/generated/**"


def customer_workflow_yaml(*, deployments_dir: str, discover_target: str, cli_ref: str) -> str:
    """Render the customer GitHub Actions workflow scaffold."""
    generated_path_glob = _generated_workflow_path_glob(deployments_dir)
    return (
        dedent(
            f"""
        name: Nebius Deployments

        on:
          pull_request:
            paths:
              - "{generated_path_glob}"
              - ".github/workflows/nebius-deployments.yml"
          push:
            branches: [ "main" ]
            paths:
              - "{generated_path_glob}"
              - ".github/workflows/nebius-deployments.yml"
          workflow_dispatch:

        permissions:
          contents: read

        concurrency:
          group: nebius-deployments-${{{{ github.workflow }}}}-${{{{ github.ref }}}}
          cancel-in-progress: true

        defaults:
          run:
            shell: bash

        env:
          NEBIUS_DISCOVER_TARGET: {discover_target}
          NEBIUS_CXCLI_REF: ${{{{ vars.NEBIUS_CXCLI_REF || '{cli_ref}' }}}}
          NEBIUS_CXCLI_PYTHON_VERSION: "3.12"

        jobs:
          discover:
            runs-on: ubuntu-latest
            outputs:
              discovery: ${{{{ steps.discover.outputs.discovery }}}}
              has_changes: ${{{{ steps.discover.outputs.has_changes }}}}
            steps:
              - uses: actions/checkout@v6
                with:
                  fetch-depth: 0

              - uses: actions/setup-python@v6
                with:
                  python-version: ${{{{ env.NEBIUS_CXCLI_PYTHON_VERSION }}}}
                  cache: pip

              - name: Install nebius-cxcli
                run: |
                  set -euo pipefail
                  pip install --upgrade pip
                  pip install "git+{NEBIUS_PS_SERVICES_REPO}@${{{{ env.NEBIUS_CXCLI_REF }}}}#subdirectory=services/nebius-cxcli"

              - name: Discover changed deployment projects
                id: discover
                run: |
                  set -euo pipefail
                  if [[ "${{GITHUB_EVENT_NAME}}" == "workflow_dispatch" ]]; then
                    nebius-cxcli discover --all "${{{{ env.NEBIUS_DISCOVER_TARGET }}}}" > discover.json
                  else
                    nebius-cxcli discover "${{{{ env.NEBIUS_DISCOVER_TARGET }}}}" > discover.json
                  fi
                  python - <<'PY' >> "$GITHUB_OUTPUT"
                  import json
                  from pathlib import Path
                  payload = json.loads(Path("discover.json").read_text(encoding="utf-8"))
                  include = payload.get("include", [])
                  print(f"discovery={{json.dumps(payload, separators=(',', ':'))}}")
                  print("has_changes=true" if include else "has_changes=false")
                  PY

          plan:
            if: github.event_name == 'pull_request' && needs.discover.outputs.has_changes == 'true'
            needs: [ discover ]
            runs-on: ubuntu-latest
            environment:
              name: ${{{{ matrix.github_environment }}}}
            strategy:
              fail-fast: false
              matrix: ${{{{ fromJson(needs.discover.outputs.discovery) }}}}
            steps:
              - uses: actions/checkout@v6

              - uses: actions/setup-python@v6
                with:
                  python-version: ${{{{ env.NEBIUS_CXCLI_PYTHON_VERSION }}}}
                  cache: pip

              - name: Install kubectl
                run: |
                  set -euo pipefail
                  version="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
                  os="$(uname | tr '[:upper:]' '[:lower:]')"
                  arch="$(uname -m)"
                  case "${{arch}}" in
                    x86_64|amd64) arch=amd64 ;;
                    aarch64|arm64) arch=arm64 ;;
                    *)
                      echo "Unsupported kubectl architecture: ${{arch}}"
                      exit 1
                      ;;
                  esac
                  tmpdir="$(mktemp -d)"
                  trap 'rm -rf "${{tmpdir}}"' EXIT
                  curl -fsSLo "${{tmpdir}}/kubectl" "https://dl.k8s.io/release/${{version}}/bin/${{os}}/${{arch}}/kubectl"
                  curl -fsSLo "${{tmpdir}}/kubectl.sha256" "https://dl.k8s.io/release/${{version}}/bin/${{os}}/${{arch}}/kubectl.sha256"
                  (cd "${{tmpdir}}" && echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check)
                  install -m 0755 "${{tmpdir}}/kubectl" "${{HOME}}/.local/bin/kubectl"
                  echo "${{HOME}}/.local/bin" >> "$GITHUB_PATH"
                  kubectl version --client=true

              - name: Install nebius-cxcli
                run: |
                  set -euo pipefail
                  pip install --upgrade pip
                  pip install "git+{NEBIUS_PS_SERVICES_REPO}@${{{{ env.NEBIUS_CXCLI_REF }}}}#subdirectory=services/nebius-cxcli"

              - name: Prepare Nebius service-account auth
                env:
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NEBIUS_AUTH_PRIVATE_KEY_PEM: ${{{{ secrets.NEBIUS_AUTH_PRIVATE_KEY_PEM }}}}
                  NEBIUS_S3_ACCESS_KEY_ID: ${{{{ secrets.NEBIUS_S3_ACCESS_KEY_ID }}}}
                  NEBIUS_S3_SECRET_ACCESS_KEY: ${{{{ secrets.NEBIUS_S3_SECRET_ACCESS_KEY }}}}
                run: |
                  set -euo pipefail
                  if [[ -z "${{NEBIUS_SA_ID:-}}" || -z "${{NEBIUS_AUTH_PUBLIC_KEY_ID:-}}" || -z "${{NEBIUS_AUTH_PRIVATE_KEY_PEM:-}}" || -z "${{NEBIUS_S3_ACCESS_KEY_ID:-}}" || -z "${{NEBIUS_S3_SECRET_ACCESS_KEY:-}}" ]]; then
                    echo "Missing required secrets: NEBIUS_SA_ID, NEBIUS_AUTH_PUBLIC_KEY_ID, NEBIUS_AUTH_PRIVATE_KEY_PEM, NEBIUS_S3_ACCESS_KEY_ID, NEBIUS_S3_SECRET_ACCESS_KEY"
                    exit 1
                  fi
                  KEY_PATH="${{RUNNER_TEMP}}/nebius-auth-private.pem"
                  printf '%s\\n' "${{NEBIUS_AUTH_PRIVATE_KEY_PEM}}" > "${{KEY_PATH}}"
                  chmod 600 "${{KEY_PATH}}"
                  {{
                    echo "NEBIUS_SA_ID=${{NEBIUS_SA_ID}}"
                    echo "NEBIUS_AUTH_PUBLIC_KEY_ID=${{NEBIUS_AUTH_PUBLIC_KEY_ID}}"
                    echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${{KEY_PATH}}"
                    echo "AWS_ACCESS_KEY_ID=${{NEBIUS_S3_ACCESS_KEY_ID}}"
                    echo "AWS_SECRET_ACCESS_KEY=${{NEBIUS_S3_SECRET_ACCESS_KEY}}"
                  }} >> "$GITHUB_ENV"

              - name: Validate generated artifacts and readiness
                run: |
                  set -euo pipefail
                  nebius-cxcli validate-generated --portable "${{{{ matrix.generated }}}}"

              - name: Terraform plan
                env:
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                run: |
                  set -euo pipefail
                  # If config uses infra.mysterybox payloads, set
                  # TF_VAR_mysterybox_payload_values here as a
                  # secret_name -> payload_key map.
                  nebius-cxcli terraform plan "${{{{ matrix.generated }}}}"

          apply:
            if: github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.discover.outputs.has_changes == 'true'
            needs: [ discover ]
            runs-on: ubuntu-latest
            environment:
              name: ${{{{ matrix.github_environment }}}}
            strategy:
              fail-fast: false
              matrix: ${{{{ fromJson(needs.discover.outputs.discovery) }}}}
            steps:
              - uses: actions/checkout@v6

              - uses: actions/setup-python@v6
                with:
                  python-version: ${{{{ env.NEBIUS_CXCLI_PYTHON_VERSION }}}}
                  cache: pip

              - name: Install kubectl
                run: |
                  set -euo pipefail
                  version="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
                  os="$(uname | tr '[:upper:]' '[:lower:]')"
                  arch="$(uname -m)"
                  case "${{arch}}" in
                    x86_64|amd64) arch=amd64 ;;
                    aarch64|arm64) arch=arm64 ;;
                    *)
                      echo "Unsupported kubectl architecture: ${{arch}}"
                      exit 1
                      ;;
                  esac
                  tmpdir="$(mktemp -d)"
                  trap 'rm -rf "${{tmpdir}}"' EXIT
                  curl -fsSLo "${{tmpdir}}/kubectl" "https://dl.k8s.io/release/${{version}}/bin/${{os}}/${{arch}}/kubectl"
                  curl -fsSLo "${{tmpdir}}/kubectl.sha256" "https://dl.k8s.io/release/${{version}}/bin/${{os}}/${{arch}}/kubectl.sha256"
                  (cd "${{tmpdir}}" && echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check)
                  install -m 0755 "${{tmpdir}}/kubectl" "${{HOME}}/.local/bin/kubectl"
                  echo "${{HOME}}/.local/bin" >> "$GITHUB_PATH"
                  kubectl version --client=true

              - name: Install nebius-cxcli
                run: |
                  set -euo pipefail
                  pip install --upgrade pip
                  pip install "git+{NEBIUS_PS_SERVICES_REPO}@${{{{ env.NEBIUS_CXCLI_REF }}}}#subdirectory=services/nebius-cxcli"

              - name: Prepare Nebius service-account auth
                env:
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NEBIUS_AUTH_PRIVATE_KEY_PEM: ${{{{ secrets.NEBIUS_AUTH_PRIVATE_KEY_PEM }}}}
                  NEBIUS_S3_ACCESS_KEY_ID: ${{{{ secrets.NEBIUS_S3_ACCESS_KEY_ID }}}}
                  NEBIUS_S3_SECRET_ACCESS_KEY: ${{{{ secrets.NEBIUS_S3_SECRET_ACCESS_KEY }}}}
                run: |
                  set -euo pipefail
                  if [[ -z "${{NEBIUS_SA_ID:-}}" || -z "${{NEBIUS_AUTH_PUBLIC_KEY_ID:-}}" || -z "${{NEBIUS_AUTH_PRIVATE_KEY_PEM:-}}" || -z "${{NEBIUS_S3_ACCESS_KEY_ID:-}}" || -z "${{NEBIUS_S3_SECRET_ACCESS_KEY:-}}" ]]; then
                    echo "Missing required secrets: NEBIUS_SA_ID, NEBIUS_AUTH_PUBLIC_KEY_ID, NEBIUS_AUTH_PRIVATE_KEY_PEM, NEBIUS_S3_ACCESS_KEY_ID, NEBIUS_S3_SECRET_ACCESS_KEY"
                    exit 1
                  fi
                  KEY_PATH="${{RUNNER_TEMP}}/nebius-auth-private.pem"
                  printf '%s\\n' "${{NEBIUS_AUTH_PRIVATE_KEY_PEM}}" > "${{KEY_PATH}}"
                  chmod 600 "${{KEY_PATH}}"
                  {{
                    echo "NEBIUS_SA_ID=${{NEBIUS_SA_ID}}"
                    echo "NEBIUS_AUTH_PUBLIC_KEY_ID=${{NEBIUS_AUTH_PUBLIC_KEY_ID}}"
                    echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${{KEY_PATH}}"
                    echo "AWS_ACCESS_KEY_ID=${{NEBIUS_S3_ACCESS_KEY_ID}}"
                    echo "AWS_SECRET_ACCESS_KEY=${{NEBIUS_S3_SECRET_ACCESS_KEY}}"
                  }} >> "$GITHUB_ENV"

              - name: Validate generated artifacts and readiness
                run: |
                  set -euo pipefail
                  nebius-cxcli validate-generated --portable "${{{{ matrix.generated }}}}"

              - name: Terraform apply
                env:
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                run: |
                  set -euo pipefail
                  # If config uses infra.mysterybox payloads, set
                  # TF_VAR_mysterybox_payload_values here as a
                  # secret_name -> payload_key map.
                  nebius-cxcli terraform apply "${{{{ matrix.generated }}}}"

              - name: Bootstrap/reconcile Flux
                env:
                  GITHUB_TOKEN: ${{{{ secrets.FLUX_GITHUB_TOKEN }}}}
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NEBIUS_AUTH_PRIVATE_KEY_PEM: ${{{{ secrets.NEBIUS_AUTH_PRIVATE_KEY_PEM }}}}
                run: |
                  set -euo pipefail
                  nebius-cxcli flux bootstrap "${{{{ matrix.generated }}}}"

              - name: Send deploy report email
                env:
                  SMTP_HOST: ${{{{ vars.SMTP_HOST }}}}
                  SMTP_PORT: ${{{{ vars.SMTP_PORT }}}}
                  SMTP_STARTTLS: ${{{{ vars.SMTP_STARTTLS }}}}
                  SMTP_FROM: ${{{{ vars.SMTP_FROM }}}}
                  SMTP_USERNAME: ${{{{ secrets.SMTP_USERNAME }}}}
                  SMTP_PASSWORD: ${{{{ secrets.SMTP_PASSWORD }}}}
                run: |
                  set -euo pipefail
                  nebius-cxcli email "${{{{ matrix.config }}}}"
        """
        ).strip()
        + "\n"
    )
