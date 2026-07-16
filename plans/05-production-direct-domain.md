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
  - Public challenge reaches the expected claim and site.
  - Origin TLS is valid through `coolify-proxy:443` with custom SNI and Host.
  - Public TLS is valid for the hostname.
- Custom hosts serve static `GET` and `HEAD` only.
- Analytics resolve to the canonical site identity.
- Dashboard supports the complete lifecycle.

## Exclusions

- Cloudflare orange-cloud, arbitrary CDNs, multiple aliases, primary redirects, wildcard domains, CLI management, and custom-domain Search Console data.

## Implementation

- Add explicit direct routing mode.
- Query authoritative DNS and at least two independent recursive resolvers across repeated checks.
- Resolve once, validate every address, and pin public probe connections.
- Reject private, loopback, link-local, metadata, and unexpected ingress addresses.
- Disable redirects unless a bounded, revalidated policy is explicitly required.
- Connect origin probes directly to `coolify-proxy:443` through Docker DNS with custom SNI and Host. Do not depend on public-IP hairpin routing from the container.
- Validate public and origin certificate chains, SANs, and expiration.
- Add the verified/routable hostname map to `server/src/server/app.py`.
- Preserve control-host isolation and canonical analytics.
- Add DNS, router, origin TLS, and public TLS statuses to the dashboard.
- Roll out against Let’s Encrypt staging before enabling production issuance.

## Verification

- Tests cover DNS rebinding, mixed public/private answers, unexpected AAAA, redirects, timeouts, oversized responses, invalid chains, wrong SANs, expired certificates, and wrong challenge tokens.
- Custom hosts cannot access health, dashboard, authentication, API, or OpenAPI routes.
- Non-GET/HEAD methods return `405`.
- Canonical and custom hostnames serve the same files and analytics site identity.
- A controlled production hostname survives restart, redeploy, renewal setup, removal, and re-addition.
- Public reachability of ports 80 and 443 is verified independently from the container-local origin probe.

## Acceptance Criteria

- Buzz can accurately claim support for direct custom domains.
- Operators who leave custom domains disabled receive unchanged canonical Buzz behavior and no custom-domain ACME activity.
- A domain is never shown as active before origin and public TLS succeed.
- Canonical Buzz URLs remain available throughout the lifecycle.
- Failed custom-domain infrastructure does not interrupt canonical hosting.

## Rollback

Close admission, withdraw routes through the acknowledged tombstone flow, and preserve ACME storage and lifecycle history. Existing certificates are not manually deleted or revoked.

After all generated routers are confirmed absent, the operator may disable custom domains without affecting canonical Buzz hosting.

## Dependencies

- Plan 04.
