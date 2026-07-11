---
title: Troubleshoot Deployment
description: Diagnose rejected, failed, or incomplete site deployments.
sidebar:
  order: 1
---

Use the visible CLI, CI, or HTTP symptom to find the next deployer-owned action. If the server, reverse proxy, database, or hosting volume needs inspection, send the result to the operator and use [Troubleshoot Self-Hosting](../self-hosting/).

## The `buzz` Command Is Missing

Confirm that Node.js 22 or later and npm are available, then install and verify the CLI:

```bash
node --version
npm install --global @infomiho/buzz-cli
buzz --version
```

In CI, keep the install step in the same job as the deployment. If npm succeeds but the command remains unavailable, inspect npm's global binary path or use `npx --yes @infomiho/buzz-cli@0.7.0` for that job.

## The Build Artifact Is Missing

Buzz deploys a directory, not your source project. Run the project build, then verify its entry page before deploying:

```bash
npm run build
test -f ./dist/index.html
buzz deploy ./dist --subdomain my-site
```

If the build writes elsewhere, pass that directory instead of `./dist`.

## CI Has No Deployment Secret

If `BUZZ_TOKEN` is empty, confirm that it exists in **Settings > Secrets and variables > Actions**. For an environment secret, the workflow job must name that environment and satisfy its protection rules. Secrets are not normally passed to workflows triggered from forks.

If the token value was lost, sign in as the site owner, create a replacement, update the secret, verify a deployment, and revoke the old token. See [Automate Deployments](../../guides/automate-deployments/).

## The CLI Cannot Connect Or TLS Fails

1. Confirm that `BUZZ_SERVER` or the configured server URL exactly matches the operator-provided HTTPS URL.
2. Open the server URL in a browser or request it with `curl --fail --show-error https://buzz.example.com`.
3. Retry from another network if DNS or a firewall may be blocking the request.
4. Report DNS, certificate, timeout, gateway, or proxy errors to the operator. Do not disable TLS verification to deploy.

## The Server Returns 401

The session or deployment token is missing, invalid, revoked, or belongs to another Buzz server.

1. Confirm the CLI's server URL points to `https://buzz.example.com`.
2. Run `buzz whoami` when using a session.
3. Run `buzz login` again if the session is invalid.
4. For automation, confirm that `BUZZ_TOKEN` is present and replace it with a newly created deployment token if its status is unknown.

## The Server Returns 403

Read the response message before changing credentials:

- `Deploy token is scoped to site ...` means the deployment token and requested site name differ. Deploy to the token's assigned site or create a token for the intended site.
- `Site ... is owned by another user` means another Buzz user owns that site name. Choose another site name or sign in with the GitHub account that owns it.
- `Deploy tokens cannot perform this operation` means the command requires a user session rather than a deployment token.

## The Server Rejects A Limit Or Archive

For `413` responses, the upload exceeded a configured compressed size, extracted size, request size, or entry-count limit. Remove source maps, caches, and other unnecessary build output, rebuild, and deploy again.

For `400` archive responses such as `Invalid ZIP file`, unsafe paths, encrypted entries, symbolic links, duplicate entries, or conflicting entries, deploy the final build directory with the CLI. If you use the HTTP API, create a new ZIP from the build directory instead of modifying the rejected archive.

If a required build cannot fit within the reported limit, send the complete response to the operator. Operators can diagnose proxy and Buzz limits through [Troubleshoot Self-Hosting](../self-hosting/).

## A Redeployment Fails But The Old Site Still Loads

Buzz stages and validates a deployment before replacing the published site. A rejected archive should leave the previous files and metadata in place.

1. Confirm that the old site URL still serves the expected version.
2. Correct the reported authentication, archive, or limit problem.
3. Deploy again and verify a known page over HTTP.

If the old site is unavailable or the server reports an internal error, stop retrying and send the response and time of failure to the operator.

## The Deployment Succeeds But The Site Is Wrong

1. Check the site URL printed by `buzz deploy` and confirm it names the intended site.
2. Inspect `CNAME` in the current working directory and in the deployment directory. The current working directory takes precedence.
3. Pass `--subdomain my-site` when you need to override either file.
4. Confirm the deployed directory contains `index.html` at its root.
5. Check that the CLI uploaded the build output, such as `./dist`, rather than the source directory.
6. Remove stale local build output, rebuild, and redeploy.
7. Request a versioned file or visible build marker with `curl --fail --show-error` to rule out browser caching.

A redeployment replaces the complete site. Files omitted from the new deployment are removed.
