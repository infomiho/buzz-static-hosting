# CLI Domain Management

## Goal

Provide CLI parity for the supported direct custom-domain lifecycle.

## Shipped Value

Logged-in users can manage domains without opening the dashboard while deployment tokens remain limited to deployment.

## Scope

Add:

```text
buzz domains list [site]
buzz domains add <site> <domain>
buzz domains check <site> <domain>
buzz domains remove <site> <domain> [--yes]
```

Commands display ownership, routing, router, origin TLS, public TLS, and removal states with actionable errors.

When the operator has not enabled custom domains, commands report that the server does not offer the capability and make no mutation request.

## Exclusions

- Changing the project `CNAME`, returning custom domains from `buzz url`, primary domains, DNS mutation, Cloudflare activation, and JSON output unless separately approved.

## Implementation

- Add a domain command group under `cli/src/commands/`.
- Add typed domain API requests and responses.
- Require a full login session and explain deployment-token rejection.
- Print exact TXT and routing instructions.
- Confirm removal unless `--yes` is supplied.
- Generate the CLI reference documentation.

## Verification

- Tests cover command registration, requests, every lifecycle state, failures, and confirmation behavior.
- Regression tests prove `buzz deploy`, `buzz url`, and local `CNAME` remain canonical.
- CLI build, tests, and generated documentation checks pass.

## Acceptance Criteria

- CLI and dashboard expose the same domain state and safe actions.
- Commands never imply that Buzz changes customer DNS.
- Deployment tokens cannot manage domains.
- The CLI distinguishes operator-disabled, enabled-but-unready, and ready servers.

## Rollback

Hide or remove CLI commands without changing server state. Existing domains remain manageable through the dashboard and API.

## Dependencies

- Plan 06.
