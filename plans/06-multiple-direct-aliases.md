# Multiple Direct Aliases

## Goal

Allow a site to serve several independently managed direct custom hostnames.

## Shipped Value

Users can attach multiple domains to one deployment and remove one without affecting the others or the permanent Buzz URL.

## Scope

- Multiple exact direct-DNS aliases per site.
- Independent ownership, routing, TLS, diagnostics, and removal state.
- Per-site, per-user, and server-wide limits.
- Analytics continue to aggregate under the canonical site.

## Exclusions

- Primary-domain selection, alias redirects, wildcard domains, Cloudflare proxying, and hostname-level analytics.

## Implementation

- Remove the one-domain-per-site admission rule.
- Reconcile and snapshot each claim independently.
- Add limits and actionable quota responses.
- Update dashboard lists and deletion warnings.
- Treat referrers between aliases of the same site as internal.

## Verification

- Two aliases serve identical deployed content.
- Removing one alias leaves all others active.
- Concurrent mutation and limit tests pass.
- Snapshot ordering remains deterministic with many aliases.
- Site deletion remains blocked until every alias has completed withdrawal.

## Acceptance Criteria

- Alias lifecycle failures are isolated.
- No alias becomes the deployment identity or changes local `CNAME`.
- Analytics remain site-centric.
- Disabled installations expose no alias admission or reconciliation work.

## Rollback

Disable adding aliases while retaining existing active ones. Remove aliases independently through the normal lifecycle.

Do not turn off the global operator flag until every alias has completed acknowledged withdrawal.

## Dependencies

- Plan 05.
