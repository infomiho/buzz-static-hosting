# Staging Routing And Withdrawal

## Goal

Prove exact router publication, public challenge routing, and acknowledged withdrawal without enabling production custom domains.

## Shipped Value

Operators can exercise the complete distributed routing lifecycle against a staging environment and safely recover from stale provider state.

## Scope

- Let’s Encrypt staging or a nonproduction deployment only.
- One TXT-verified hostname per site.
- Generation-qualified exact `Host()` routers.
- Runtime evidence of generation-qualified router presence and absence. Traefik exposes no provider snapshot revision.
- Buzz-owned unguessable public challenge path.
- Removal tombstones retain hostname uniqueness until withdrawal is observed.

## Exclusions

- Production certificates, general static-site serving through the custom host, Cloudflare proxying, CLI controls, and multiple aliases.

## Implementation

- Project eligible claims into deterministic complete Traefik snapshots.
- Use Coolify entrypoint `https`, resolver `buzz-custom`, and service `buzz@docker`.
- Encode claim identity and generation in router names.
- Add a runtime API client that verifies the generation-qualified router's effective rule, service, resolver, entrypoint, status, and errors.
- Reserve and intercept the challenge path before static file lookup.
- Reconcile continuously at a bounded, jittered cadence and retain the latest actionable error.
- Exclude removing claims immediately, stop application dispatch immediately, and retain tombstones until their generated routers are absent.
- Add provider and reconciliation transition logging without secrets.

## Verification

- Golden tests cover deterministic snapshots and safe rule generation.
- Pending, expired, and removing claims never appear in snapshots.
- A challenge token cannot be served from another hostname or site.
- Traefik restart, malformed snapshots, failed polling, stale runtime state, and removal retries are exercised.
- An end-to-end staging hostname proves publication, challenge response, withdrawal, and safe reuse.

## Acceptance Criteria

- Router activation is acknowledged from Traefik runtime state rather than elapsed time.
- Public challenge responses identify the intended claim and canonical site.
- Removal does not release uniqueness until the exact generated router is absent and the hostname no longer reaches Buzz's challenge response.
- Provider failure cannot expose dashboard or API routes on a custom host.
- Admission remains closed unless the operator flag is enabled and control-plane readiness passes.

## Rollback

Stop admitting routes, publish an empty snapshot, confirm every Buzz-generated router is absent, and only then disable the provider. Preserve claims and tombstones.

Setting `BUZZ_CUSTOM_DOMAINS_ENABLED=false` is the final step, not the withdrawal mechanism.

## Dependencies

- Plan 02.
- Plan 03.
