# Production Direct Domain

## Goal

Ship one production custom hostname per site when DNS resolves directly to the Coolify ingress.

## Shipped Value

A site owner can attach a domain, prove ownership, obtain HTTPS, serve the static site, inspect status, and remove the domain safely.

## Scope

- One direct-DNS hostname per site.
- Every observed DNS answer must be public and belong to the configured ingress allowlist; exact equality with the full allowlist is not required.
- Unsupported address families fail closed.
- Domain activation requires:
  - TXT ownership verified.
  - Router acknowledged.
  - Every resolved address belongs to the configured public ingress allowlist.
  - Trusted origin TLS through `coolify-proxy:443` with custom SNI and Host.
  - The origin challenge identifies the expected claim, generation, and site.
- Custom hosts serve static `GET` and `HEAD` only.
- Analytics resolve to the canonical site identity.
- Dashboard supports the complete lifecycle.

## Exclusions

- Cloudflare orange-cloud, arbitrary CDNs, multiple aliases, primary redirects, wildcard domains, CLI management, and custom-domain Search Console data.

## Implementation

- Keep direct routing as the only supported custom-domain mode.
- Resolve once and validate every address against an operator-owned ingress allowlist.
- Reject private, loopback, link-local, metadata, and unexpected ingress addresses.
- Connect a bounded, non-redirecting origin probe directly to `coolify-proxy:443` through Docker DNS with custom SNI and Host.
- Use the platform trust store to validate the certificate chain, SAN, and validity.
- Latch activation for the current route generation only after the exact challenge response succeeds.
- Add the verified/routable hostname map to `server/src/server/app.py`.
- Preserve control-host isolation and canonical analytics.
- Expose one actionable activation result rather than a transient status matrix.
- Roll out against Let’s Encrypt staging before enabling production issuance.

## Verification

- Tests cover mixed public/private answers, unexpected AAAA, redirects, timeouts, oversized responses, invalid chains, wrong SANs, expired certificates, and wrong challenge tokens.
- Custom hosts cannot access health, dashboard, authentication, API, or OpenAPI routes.
- Non-GET/HEAD methods return `405`.
- Canonical and custom hostnames serve the same files and analytics site identity.
- A controlled production hostname survives restart, redeploy, renewal setup, removal, and re-addition.
- Public reachability of ports 80 and 443 is verified during controlled rollout.

## Acceptance Criteria

- Buzz can accurately claim support for direct custom domains.
- Operators who leave custom domains disabled receive unchanged canonical Buzz behavior and no custom-domain ACME activity.
- A domain is never shown as active before trusted origin TLS and exact challenge validation succeed.
- Canonical Buzz URLs remain available throughout the lifecycle.
- Failed custom-domain infrastructure does not interrupt canonical hosting.

## Rollback

Close admission, withdraw routes through the acknowledged tombstone flow, and preserve ACME storage and lifecycle history. Existing certificates are not manually deleted or revoked.

After all generated routers are confirmed absent, the operator may disable custom domains without affecting canonical Buzz hosting.

## Dependencies

- Plan 04.
