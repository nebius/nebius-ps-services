from __future__ import annotations

CONFIG_SCHEMA_VERSION = 1
QUOTA_SCHEMA_VERSION = 1
INVITE_SCHEMA_VERSION = 1

DEFAULT_CONFIG_FILENAME = "nebius-acc.config.yaml"
DEFAULT_QUOTA_SUFFIX = "-quota.config.yaml"
DEFAULT_INVITE_SUFFIX = "-invite.config.yaml"

DEFAULT_CONFIG_TEMPLATE = f"""# nebius-acc configuration file
# Versioned schema to support validation and future changes.
version: {INVITE_SCHEMA_VERSION}

tenant_id: tenant-EXAMPLE_ID
group_name: "grp-{{project}}"
role: editor

projects:
  eu-north1:
    projectA: {{}}
    projectB: {{}}
  eu-west1:
    projectC: {{}}
    projectD: {{}}

configure_sso:
  enabled: false
  name: "corp-entra"
  sso_url: "https://login.microsoftonline.com/.../saml2"
  idp_issuer: "https://sts.windows.net/.../"
  auto_create_users: true
  active: true
  force_authn: false
  # Optional: upload federation certificate
  # cert_file: ./federation-cert.pem
  # cert_description: "Entra ID certificate"
"""

DEFAULT_QUOTA_TEMPLATE = f"""# nebius-acc quota file
# You can define both per-region and per-project quotas.
# Per-project quotas override per-region quotas when both specify the same quota+region.

version: {QUOTA_SCHEMA_VERSION}
tenant_id: tenant-EXAMPLE_ID

# Per-region quotas
regions:
  eu-north1:
    - quota: compute.disk.count
      limit: 5000
  eu-west1:
    - quota: compute.disk.count
      limit: 5000

# Per-project quotas (optional)
# projects:
#   projectA:
#     - quota: compute.disk.count
#       region: eu-north1
#       limit: 2000
#   projectB:
#     - quota: compute.disk.count
#       region: eu-north1
#       limit: 10000
"""

DEFAULT_INVITE_TEMPLATE = f"""# nebius-acc invite file
# Use this file to batch invite users by email into project groups.

version: {CONFIG_SCHEMA_VERSION}
tenant_id: tenant-EXAMPLE_ID

invites:
  projectA:
    - email: user1@example.com
    - email: user2@example.com
  projectB:
    - email: user3@example.com
"""
