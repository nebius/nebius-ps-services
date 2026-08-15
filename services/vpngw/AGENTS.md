<!-- project-agent-instructions:managed-v3 manifest-sha256=481c0a7fa6c202cf2fa6bd4fe5338d9e0d7af1a228e0e92cc04d5acea26858e2 decision-sha256=4296b7bae4c7093d93b5e5e7bac6c237c6a51bf717938c3c870d8333ed449f65 body-sha256=312c7188338308dd0e8fbd7f1849571c660256340ca19159c8bc46c964bc8e52 -->

# Project Agent Instructions

## Scope

These instructions apply to this directory and all descendants.

Project root: `services/vpngw`

Closer nested instruction files may refine these defaults for their subtree.

## Context authority

- Requirements: `docs/requirements.md`
- Design: `docs/design.md`
- Read only the sections relevant to the boundary being changed.

## Change requirements

- Breaking a supported API, CLI contract, configuration or persisted format, or upgrade path requires explicit approval, a deprecation or migration plan, and regression coverage. Keep internals on one canonical path.
- This project has existing users. Preserve supported behavior and public interfaces across changes; treat unintended compatibility breakage as a regression.

## Security and operations

- For VM-HA promotion, require the former Compute owner to be Stopped and confirm the shared allocation on the candidate before forwarding or route reconciliation.
