# Explicit Deployment Identity

## Goal

Make the Buzz site name an explicit API value so future custom URLs cannot change deployment identity.

## Shipped Value

Deployments reliably preserve the canonical Buzz site name regardless of the URL returned by the server.

## Scope

- Add `name` to the deployment response.
- Make the CLI use `name` instead of parsing the first hostname label.
- Keep deployment output, `buzz url`, and the local `CNAME` file canonical.
- Update generated API documentation.

## Exclusions

- Custom-domain storage, routing, DNS, TLS, dashboard, and CLI commands.
- This stage does not enable custom domains or change the operator's default-disabled feature state.

## Implementation

- Extend `DeploymentResponse` in `server/src/server/api_models.py`.
- Return the deployed site name from `server/src/server/routes/sites.py`.
- Update response parsing in `cli/src/deploy.ts`.
- Remove hostname-based site-name inference.
- Regenerate `docs/site/public/openapi.json`.

## Verification

- Server tests assert that deployment returns `{name, url}`.
- CLI tests assert that `name` is written to `CNAME` even when `url` has an unrelated hostname shape.
- Server and CLI test suites pass.
- Generated documentation freshness checks pass.

## Acceptance Criteria

- The CLI never derives site identity from a URL.
- Existing canonical URLs and project `CNAME` behavior are unchanged.
- The API change is additive for existing clients.
- Installations that never enable custom domains receive the same explicit identity improvement.

## Rollback

Older clients ignore the additive response field. Reverting the CLI remains safe while deployment URLs stay canonical.

## Dependencies

None.
