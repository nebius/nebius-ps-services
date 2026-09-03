<!-- project-agent-instructions:managed-v3 manifest-sha256=8d74cd21bc42e6d384493eadf93f02f34807df4aa08b197996dffad07a9d2e9b decision-sha256=42903038b3f75f2e19442b6b9297173929ee2984575e0f36758634c8789db0c8 body-sha256=451c0ce58ba66be456f83a0705d7222e14db648634a44ed4ffb23f849028f12e -->

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

- Breaking supported APIs, CLI contracts, configuration or persisted formats, or upgrade paths requires explicit approval and regression coverage. REQ-008 and REQ-015 are approved pre-adoption VM-HA exceptions; keep one canonical implementation.
- Preserve supported non-HA behavior and public interfaces except REQ-015's approved region-only rename. VM-HA is pre-adoption: add no legacy readers, aliases, dual modes, mixed-version fallbacks, or migration shims unless explicitly requested.

## Security and operations

- For VM-HA promotion, require the former Compute owner to be Stopped and confirm the shared allocation on the candidate before forwarding or route reconciliation.
