# Cloudflare Proxy Activation

## Goal

Allow a custom hostname to remain behind Cloudflare orange-cloud while Buzz validates the edge and origin independently.

## Shipped Value

Users retain Cloudflare's proxy, caching, DDoS protection, and visitor-facing TLS while serving a Buzz static site.

## Scope

- Explicit Cloudflare mode without customer API credentials.
- Persistent TXT ownership.
- Generation-qualified exact Traefik router and acknowledged runtime state.
- Public challenge reaches the expected Buzz claim and site through Cloudflare.
- Origin TLS validates directly through `coolify-proxy:443`.
- Public TLS validates against Cloudflare.
- Cloudflare Full (strict) is required.
- Safe acknowledged removal.
- HTTP-01 forwarding through Cloudflare is a release gate proven with a controlled zone, not an assumed behavior.

## Exclusions

- Cloudflare API integration, automatic DNS or SSL configuration, arbitrary CDNs, wildcard domains, and bypassing TXT ownership.

## Implementation

- Add a Cloudflare-specific activation predicate.
- Keep edge and origin evidence and errors separate.
- Add Cloudflare-specific retry and backoff rules.
- Fail closed immediately on ownership, Cloudflare DNS, router generation, and challenge identity failures. Allow three consecutive one-minute retries for edge and origin transport failures.
- Extend dashboard, CLI, troubleshooting, and operator documentation.

## Verification

- Controlled-zone tests cover successful activation and removal.
- Test valid edge TLS with broken origin TLS.
- Test healthy origin with pending or invalid Universal SSL.
- Test challenge forwarding failure, stale cache, WAF denial, and errors 1014, 525, and 526.
- Test provider outage and stale router withdrawal behavior.
- Test Traefik and Buzz restarts while Cloudflare continues serving the hostname.
- Confirm direct domains remain unaffected.

## Acceptance Criteria

- Active status requires ownership, router acknowledgement, public challenge, origin TLS, and edge TLS.
- Cloudflare failures never fall back to direct-mode security rules.
- Closing admission prevents new claims. Withdraw existing routers before disabling the global capability or removing the provider.
- Removal stops Buzz serving before the hostname claim is released.
- Activation is independently default-off and cannot be disabled while activated Cloudflare routers remain.

## Rollback

Close admission, then withdraw Cloudflare and direct routers before disabling the global capability or removing the Traefik provider. Buzz does not change customer DNS or Cloudflare settings.

## Dependencies

- Plan 08.
