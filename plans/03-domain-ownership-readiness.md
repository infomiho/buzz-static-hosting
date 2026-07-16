# Domain Ownership Readiness

## Goal

Let a site owner register and prove control of one hostname without routing traffic to it.

## Shipped Value

Buzz can safely normalize, persist, verify, expire, and cancel custom-domain claims. The dashboard clearly labels successful claims as **Ready to route**, not active.

## Scope

- One custom hostname per site.
- Persistent TXT ownership verification.
- Full user sessions only; deployment tokens are rejected.
- Pending claims expire and do not permanently reserve a hostname.
- Verified and removing claims hold global normalized uniqueness.
- Site deletion is blocked while a verified or removing claim exists.
- Dashboard supports add, inspect, check, and cancel.
- Domain APIs and actions are unavailable unless the operator explicitly enables custom domains and readiness prerequisites pass.

## Exclusions

- Traefik routers, public challenge requests, TLS, static serving, CLI management, Cloudflare proxying, and multiple aliases.

## Implementation

- Add a minimal `PRAGMA user_version` migration runner in `server/src/server/db.py`.
- Check existing foreign-key integrity and enable foreign keys on every connection.
- Add domain claim and lifecycle persistence without cascade deletion.
- Canonicalize hostnames to lowercase ASCII with IDNA2008 and an explicitly documented UTS #46 mapping policy.
- Reject URLs, ports, IP literals, wildcards, malformed labels, local names, and Buzz-reserved hostnames.
- Generate a cryptographically random TXT verification token.
- Add session-only domain API routes and models.
- Add the ownership section to the site-detail dashboard.
- Guard `SiteStore.delete` before filesystem mutation.

## Verification

- Upgrade a copy of an existing database without data loss.
- Repeated initialization is idempotent.
- Claim races cannot acquire two verified owners.
- Pending claims expire and can be superseded by a verified claimant.
- TXT checks handle absence, mismatch, multiple values, resolver failure, and propagation.
- Hostname normalization has IDNA and boundary tests.
- Deployment tokens cannot manage claims.
- Site deletion is blocked before files or records are removed.

## Acceptance Criteria

- A site owner can prove control with `_buzz.<hostname>` TXT.
- An unverified claim cannot block a legitimate owner indefinitely.
- No verified domain is represented as publicly active.
- Canonical Buzz hosting is unaffected.
- Disabled operators see no actionable custom-domain controls; incomplete enabled setups show an unavailable diagnostic rather than accepting claims.

## Rollback

Disable new claims while preserving domain lifecycle records. Do not downgrade or delete the additive schema automatically.

Turning off the operator flag is allowed only when no routed domains exist at this stage. Later stages must use acknowledged withdrawal before disabling the provider.

## Dependencies

- Plan 01.
- Plan 02, so readiness can distinguish ownership support from routing support.
