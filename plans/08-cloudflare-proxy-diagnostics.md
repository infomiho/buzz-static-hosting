# Cloudflare Proxy Diagnostics

## Goal

Diagnose Cloudflare orange-cloud routing and TLS safely before allowing proxied domains to activate.

## Shipped Value

Users and operators receive specific readiness failures for proxied domains instead of generic DNS or TLS errors.

## Scope

- Explicit `cloudflare` routing mode.
- Never silently infer Cloudflare mode and switch security policy; diagnostics may suggest the mode.
- Persistent TXT ownership remains mandatory.
- Validate public addresses against a versioned Cloudflare IP-range allowlist.
- Inspect public response evidence without customer Cloudflare credentials.
- Diagnose:
  - Universal SSL pending or invalid.
  - Full (strict) origin requirements.
  - HTTP-01 forwarding failures.
  - Redirect, cache, and WAF interference.
  - Cloudflare errors 1014, 525, and 526.

## Exclusions

- Active orange-cloud domains, Cloudflare API credentials, automatic DNS changes, arbitrary CDN support, and wildcard domains.

## Implementation

- Bundle a versioned Cloudflare range list with a documented update process.
- Fail closed when range data is missing, malformed, or stale beyond policy.
- Pin public connections to validated Cloudflare addresses.
- Persist structured edge diagnostics separately from origin status.
- Add dashboard and CLI guidance for Cloudflare Full (strict).

## Verification

- Tests cover stale ranges, mixed Cloudflare/non-Cloudflare answers, malformed data, redirects, cached wrong tokens, WAF denial, and representative Cloudflare errors.
- A controlled Cloudflare zone verifies credential-free diagnostics and HTTP-01 forwarding under the documented Full (strict), redirect, cache, and WAF settings.
- Direct-domain behavior remains unchanged.

## Operational Verification

On 2026-07-16, `cfdiag.miho.dev` was created as a fresh proxied claim against the `code-screenshot` site:

- TXT ownership, Cloudflare-only DNS answers, and the exact router were verified.
- The initial `526` and origin TLS failure cleared after `buzz-production` issued a fresh Let's Encrypt certificate through HTTP-01.
- Edge TLS, the HTTPS generation challenge, and origin validation became healthy without Cloudflare credentials.
- Site content remained blocked with `421`.
- Router withdrawal was acknowledged before the claim and its DNS records were removed.

## Acceptance Criteria

- Cloudflare mode cannot activate.
- Cloudflare diagnostics require both the global custom-domain opt-in and an explicit per-claim Cloudflare mode.
- Every dialed public address is validated as Cloudflare-owned.
- Diagnostics distinguish edge TLS, origin TLS, routing, and challenge failures.

## Rollback

Disable Cloudflare-mode admission. Direct domains and their lifecycle remain unaffected.

Disabling the global custom-domain capability still requires acknowledged withdrawal of all custom-domain routers.

## Dependencies

- Plan 07.
