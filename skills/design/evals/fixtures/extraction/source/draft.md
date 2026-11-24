# Project catalog draft

## Requirements

- CAT-001: Users can create a project with a unique name.
- CAT-002: Users can list projects they created.
- CAT-003: Unauthorized users cannot access another user's project.

## Fixed architecture

A UI calls an API backed by authoritative persistent storage. The API owns
authorization and rejects duplicate names. No vendor or framework is selected.

## Supplied implementation phases

1. Core: CAT-001 and CAT-003.
2. Discovery: CAT-002, depending on Core.

Acceptance uses API and cross-user integration tests. Research can validate this
local product intent and source consistency without live vendor claims.
