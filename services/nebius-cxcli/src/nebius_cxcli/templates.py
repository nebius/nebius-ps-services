"""Static templates used by nebius-cxcli."""

from __future__ import annotations

import re
from textwrap import dedent

from . import __version__

NEBIUS_PS_SERVICES_REPO = "https://github.com/nebius/nebius-ps-services.git"
NEBIUS_PROVIDER_SOURCE = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"
NEBIUS_PROVIDER_VERSION = ">= 0.5.55"


def _is_stable_semver(version: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", version) is not None


def default_cli_ref() -> str:
    """Return the git ref used by generated customer workflows."""
    if _is_stable_semver(__version__):
        return f"nebius-cxcli-v{__version__}"
    return "main"


def default_platform_infra_source() -> str:
    """Return the default git source for platform-infra stack."""
    ref = default_cli_ref()
    return f"git::{NEBIUS_PS_SERVICES_REPO}//platform-infra/stacks/customer-platform?ref={ref}"


DEFAULT_PLATFORM_INFRA_SOURCE = default_platform_infra_source()


def customer_workflow_yaml(*, deployments_dir: str, discover_target: str, cli_ref: str) -> str:
    """Render the customer GitHub Actions workflow scaffold."""
    return (
        dedent(
            f"""
        name: Nebius Deployments

        on:
          pull_request:
            paths:
              - "**/{deployments_dir}/**/config.yaml"
          push:
            branches: [ "main" ]
            paths:
              - "**/{deployments_dir}/**/config.yaml"

        env:
          NEBIUS_DISCOVER_TARGET: {discover_target}
          NEBIUS_CXCLI_REF: {cli_ref}

        jobs:
          discover:
            runs-on: ubuntu-latest
            outputs:
              discovery: ${{{{ steps.discover.outputs.discovery }}}}
            steps:
              - uses: actions/checkout@v4
                with:
                  fetch-depth: 0

              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12"

              - name: Install nebius-cxcli
                run: |
                  pip install --upgrade pip
                  pip install "git+{NEBIUS_PS_SERVICES_REPO}@${{{{ env.NEBIUS_CXCLI_REF }}}}#subdirectory=services/nebius-cxcli"

              - name: Discover changed configs
                id: discover
                run: |
                  nebius-cxcli discover "${{{{ env.NEBIUS_DISCOVER_TARGET }}}}" > discover.json
                  echo "discovery=$(cat discover.json)" >> "$GITHUB_OUTPUT"

          plan:
            if: github.event_name == 'pull_request'
            needs: [ discover ]
            runs-on: ubuntu-latest
            strategy:
              fail-fast: false
              matrix: ${{{{ fromJson(needs.discover.outputs.discovery) }}}}
            steps:
              - uses: actions/checkout@v4

              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12"

              - name: Install nebius-cxcli
                run: |
                  pip install --upgrade pip
                  pip install "git+{NEBIUS_PS_SERVICES_REPO}@${{{{ env.NEBIUS_CXCLI_REF }}}}#subdirectory=services/nebius-cxcli"

              - name: Prepare Nebius service-account auth
                env:
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NEBIUS_AUTH_PRIVATE_KEY_PEM: ${{{{ secrets.NEBIUS_AUTH_PRIVATE_KEY_PEM }}}}
                run: |
                  set -euo pipefail
                  if [[ -z "${{NEBIUS_SA_ID:-}}" || -z "${{NEBIUS_AUTH_PUBLIC_KEY_ID:-}}" || -z "${{NEBIUS_AUTH_PRIVATE_KEY_PEM:-}}" ]]; then
                    echo "Missing required secrets: NEBIUS_SA_ID, NEBIUS_AUTH_PUBLIC_KEY_ID, NEBIUS_AUTH_PRIVATE_KEY_PEM"
                    exit 1
                  fi
                  KEY_PATH="${{RUNNER_TEMP}}/nebius-auth-private.pem"
                  printf '%s\\n' "${{NEBIUS_AUTH_PRIVATE_KEY_PEM}}" > "${{KEY_PATH}}"
                  chmod 600 "${{KEY_PATH}}"
                  echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${{KEY_PATH}}" >> "$GITHUB_ENV"

              - name: Validate and render
                run: |
                  nebius-cxcli validate --strict "${{{{ matrix.config }}}}"
                  nebius-cxcli render "${{{{ matrix.config }}}}"

              - name: Terraform plan
                env:
                  AWS_ACCESS_KEY_ID: ${{{{ secrets.NEBIUS_S3_ACCESS_KEY_ID }}}}
                  AWS_SECRET_ACCESS_KEY: ${{{{ secrets.NEBIUS_S3_SECRET_ACCESS_KEY }}}}
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                run: |
                  # If config uses infra.mysterybox secrets, expose each
                  # required value_from_env variable in this step.
                  nebius-cxcli terraform plan "${{{{ matrix.config }}}}"

          apply:
            if: github.event_name == 'push' && github.ref == 'refs/heads/main'
            needs: [ discover ]
            runs-on: ubuntu-latest
            strategy:
              fail-fast: false
              matrix: ${{{{ fromJson(needs.discover.outputs.discovery) }}}}
            steps:
              - uses: actions/checkout@v4

              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12"

              - name: Install nebius-cxcli
                run: |
                  pip install --upgrade pip
                  pip install "git+{NEBIUS_PS_SERVICES_REPO}@${{{{ env.NEBIUS_CXCLI_REF }}}}#subdirectory=services/nebius-cxcli"

              - name: Prepare Nebius service-account auth
                env:
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NEBIUS_AUTH_PRIVATE_KEY_PEM: ${{{{ secrets.NEBIUS_AUTH_PRIVATE_KEY_PEM }}}}
                run: |
                  set -euo pipefail
                  if [[ -z "${{NEBIUS_SA_ID:-}}" || -z "${{NEBIUS_AUTH_PUBLIC_KEY_ID:-}}" || -z "${{NEBIUS_AUTH_PRIVATE_KEY_PEM:-}}" ]]; then
                    echo "Missing required secrets: NEBIUS_SA_ID, NEBIUS_AUTH_PUBLIC_KEY_ID, NEBIUS_AUTH_PRIVATE_KEY_PEM"
                    exit 1
                  fi
                  KEY_PATH="${{RUNNER_TEMP}}/nebius-auth-private.pem"
                  printf '%s\\n' "${{NEBIUS_AUTH_PRIVATE_KEY_PEM}}" > "${{KEY_PATH}}"
                  chmod 600 "${{KEY_PATH}}"
                  echo "NEBIUS_AUTH_PRIVATE_KEY_FILE=${{KEY_PATH}}" >> "$GITHUB_ENV"

              - name: Validate and render
                run: |
                  nebius-cxcli validate --strict "${{{{ matrix.config }}}}"
                  nebius-cxcli render "${{{{ matrix.config }}}}"

              - name: Terraform apply
                env:
                  AWS_ACCESS_KEY_ID: ${{{{ secrets.NEBIUS_S3_ACCESS_KEY_ID }}}}
                  AWS_SECRET_ACCESS_KEY: ${{{{ secrets.NEBIUS_S3_SECRET_ACCESS_KEY }}}}
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                run: |
                  # If config uses infra.mysterybox secrets, expose each
                  # required value_from_env variable in this step.
                  nebius-cxcli terraform apply "${{{{ matrix.config }}}}"

              - name: Install Flux CLI
                run: |
                  curl -s https://fluxcd.io/install.sh | sudo bash
                  flux --version

              - name: Install Nebius CLI
                run: |
                  curl -sSL https://storage.eu-north1.nebius.cloud/cli/install.sh | bash
                  echo "${{{{ env.HOME }}}}/.nebius/bin" >> "$GITHUB_PATH"
                  nebius version --full

              - name: Configure kubeconfig for Flux
                env:
                  CONFIG_PATH: ${{{{ matrix.config }}}}
                  NB_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NB_AUTHKEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NB_AUTH_PRIVATE_KEY_FILE: ${{{{ env.NEBIUS_AUTH_PRIVATE_KEY_FILE }}}}
                run: |
                  set -euo pipefail
                  if [[ -z "${{NB_SA_ID:-}}" || -z "${{NB_AUTHKEY_ID:-}}" || -z "${{NB_AUTH_PRIVATE_KEY_FILE:-}}" ]]; then
                    echo "Missing required values: NEBIUS_SA_ID, NEBIUS_AUTH_PUBLIC_KEY_ID, NEBIUS_AUTH_PRIVATE_KEY_FILE"
                    exit 1
                  fi

                  PROJECT_ID="$(python - <<'PY'
                  import os
                  from pathlib import Path
                  import yaml

                  cfg = Path(os.environ["CONFIG_PATH"])
                  payload = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {{}}
                  print(payload["client_info"]["nebius"]["project_id"])
                  PY
                  )"

                  PROFILE="ci-sa-${{GITHUB_RUN_ID}}-${{GITHUB_RUN_ATTEMPT}}"
                  nebius profile create \\
                    --profile "${{PROFILE}}" \\
                    --endpoint api.nebius.cloud \\
                    --service-account-id "${{NB_SA_ID}}" \\
                    --public-key-id "${{NB_AUTHKEY_ID}}" \\
                    --private-key-file "${{NB_AUTH_PRIVATE_KEY_FILE}}" \\
                    --parent-id "${{PROJECT_ID}}"

                  INFRA_DIR="$(dirname "${{CONFIG_PATH}}")/generated/infra"
                  CLUSTER_ID="$(terraform -chdir="${{INFRA_DIR}}" output -raw mk8s_cluster_id)"
                  nebius mk8s cluster get-credentials --id "${{CLUSTER_ID}}" --external --profile "${{PROFILE}}"
                  kubectl cluster-info

              - name: Bootstrap/reconcile Flux
                env:
                  GITHUB_TOKEN: ${{{{ secrets.FLUX_GITHUB_TOKEN }}}}
                  NEBIUS_SA_ID: ${{{{ secrets.NEBIUS_SA_ID }}}}
                  NEBIUS_AUTH_PUBLIC_KEY_ID: ${{{{ secrets.NEBIUS_AUTH_PUBLIC_KEY_ID }}}}
                  NEBIUS_AUTH_PRIVATE_KEY_PEM: ${{{{ secrets.NEBIUS_AUTH_PRIVATE_KEY_PEM }}}}
                  NEBIUS_ENDPOINT: api.nebius.cloud:443
                run: |
                  nebius-cxcli flux bootstrap "${{{{ matrix.config }}}}"

              - name: Inventory outputs
                env:
                  AWS_ACCESS_KEY_ID: ${{{{ secrets.NEBIUS_S3_ACCESS_KEY_ID }}}}
                  AWS_SECRET_ACCESS_KEY: ${{{{ secrets.NEBIUS_S3_SECRET_ACCESS_KEY }}}}
                  SMTP_PASSWORD: ${{{{ secrets.SMTP_PASSWORD }}}}
                run: |
                  nebius-cxcli inventory write "${{{{ matrix.config }}}}"
                  nebius-cxcli inventory upload "${{{{ matrix.config }}}}"
                  nebius-cxcli email "${{{{ matrix.config }}}}"
        """
        ).strip()
        + "\n"
    )
