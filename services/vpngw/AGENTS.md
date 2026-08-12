<!-- project-agent-instructions:managed-v3 manifest-sha256=cc67460ea999c0a6843fcd81745e7aa94d4397f6cbbaa04d494c9c358c8b9a77 decision-sha256=9fa1140fcd83c2f3cc17ce3446b8b5ff6f86e05810a6312a4d6411e9a7b0005d body-sha256=312c7188338308dd0e8fbd7f1849571c660256340ca19159c8bc46c964bc8e52 -->

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
