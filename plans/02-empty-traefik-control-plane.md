# Empty Traefik Control Plane

## Goal

Prove that Buzz and Coolify's Traefik can exchange authenticated dynamic configuration safely before routing customer domains.

## Shipped Value

Operators receive a readiness check for the custom-domain infrastructure without changing public site routing or requesting certificates.

## Scope

- Add a private Buzz control listener on the `coolify` network.
- Add `BUZZ_CUSTOM_DOMAINS_ENABLED`, disabled by default, as the explicit operator opt-in.
- Give it an installation-unique network alias.
- Serve a bounded, deterministic empty Traefik HTTP-provider snapshot.
- Authenticate provider requests with a high-entropy bearer token.
- Add a separate `buzz-custom` HTTP-01 resolver.
- Add protected internal access to the required Traefik runtime API endpoints.
- Report automatically observable control-plane readiness separately from application health.

## Exclusions

- Domain records, DNS verification, dynamic routers, ACME orders, and user-facing domain controls.
- No control-plane process or proxy integration is required when the operator leaves custom domains disabled.

## Implementation

- Add environment definitions through `server/src/server/environment.py`.
- Start and stop the private listener with the application lifecycle.
- Start the listener only when custom domains are enabled and required control-plane settings are present.
- Add snapshot serialization with response-size and domain-count limits.
- Configure short provider timeouts and monitor failed or malformed polls. Traefik retains the last valid snapshot while running and has no disk cache after restart.
- Update `docker-compose.coolify.yml` with the private listener and unique alias.
- Document proxy changes under **Servers > Proxy**:
  - HTTP provider endpoint and authorization header.
  - `buzz-custom` resolver using entrypoint `http`.
  - Protected, unadvertised Traefik API entrypoint.
  - Existing Cloudflare DNS-01 resolver remains unchanged.
- Document that Coolify uses `http` and `https`; standalone uses `web` and `websecure`.

## Verification

- Unauthorized snapshot requests fail closed.
- The listener is not published or routed publicly.
- Traefik accepts an empty snapshot and reports no provider errors.
- Automated readiness detects provider polling, protected runtime API access, `buzz@docker`, and the expected HTTPS entrypoint.
- Operator checks confirm the unused `buzz-custom` resolver, durable ACME storage, and public ports 80 and 443. These cannot be proven by Traefik's runtime API before a router exercises them.
- Existing Buzz dashboard and wildcard sites remain available.
- With custom domains disabled, no listener starts and no proxy changes are required.
- Coolify proxy restart preserves the configuration.

## Acceptance Criteria

- Traefik polls Buzz privately and repeatedly accepts an empty snapshot.
- Buzz can inspect `buzz@docker` and runtime router state without `api.insecure`.
- Readiness does not claim that an unused resolver or certificate storage has been exercised.
- Custom-domain readiness failure does not fail `/health` or canonical hosting.
- Missing optional configuration cannot accidentally enable the feature.

## Rollback

Keep the snapshot empty, remove the HTTP provider and internal API route through **Servers > Proxy**, then restart Traefik. Existing Docker-provider routes remain untouched.

Do not remove the provider endpoint before Traefik has accepted the empty snapshot. A running Traefik instance retains its last valid HTTP-provider configuration when polling fails.

## Dependencies

- Plan 01 for release sequencing only.
