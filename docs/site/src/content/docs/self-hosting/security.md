---
title: Security
description: Understand Buzz's security boundaries before exposing a server.
sidebar:
  order: 9
---

Review Buzz's current controls and unsupported protections before making a server public. Buzz is a small self-hosted service, not a security boundary for mutually untrusted tenants.

## Control Who Can Use The Server

Buzz authenticates users through a GitHub OAuth app, but it doesn't provide a GitHub organization or user allowlist. Any GitHub user who can reach the server can attempt to sign in, create sites, and consume storage.

Restrict network access at an upstream proxy, virtual private network, or firewall when the server is intended for a closed group. Buzz doesn't include rate limiting, so add and verify rate limits upstream if your threat model requires them.

## Protect Credentials

- Store `GITHUB_CLIENT_SECRET`, `BUZZ_ANALYTICS_SECRET`, `BUZZ_GSC_CREDENTIALS`, and Cloudflare API tokens outside source control.
- Restrict secret-file and deployment-console access to operators.
- Use site-scoped deployment tokens for automation instead of a user session.
- Revoke a deployment token when its workflow no longer needs access.
- Rotate any secret that appears in logs or source control.

Buzz stores hashes of session and deployment tokens in SQLite, not their raw values. Sessions expire after 30 days. Deployment tokens currently have no expiry when created through the supported flow, so revocation is an operator responsibility.

## Treat Deployed Files As Untrusted Content

Buzz validates ZIP paths, rejects symbolic links and encrypted entries, and extracts deployments into staging directories. These checks reduce archive extraction risks but don't determine whether uploaded files are safe.

Buzz doesn't scan deployments for malware, phishing content, secrets, or harmful JavaScript. A deployed site can execute active content in its visitors' browsers. Review who can deploy, monitor hosted content, and use external scanning or policy controls when required.

Sites use separate subdomains, while the dashboard session cookie is host-only, `HttpOnly`, `Secure` outside development mode, and `SameSite=Lax`. These controls don't provide a general guarantee that arbitrary hosted content is harmless.

## Limit Exposure And Resource Use

- Expose the Buzz container through HTTPS rather than publishing port `8080` directly.
- Keep the Docker socket mount read-only and limit host access. Traefik still receives broad Docker metadata access through that socket.
- Set [deployment limits](../configure-deployment-limits/) appropriate for the host.
- Monitor free disk space. Buzz has per-deployment limits, but no total storage quota, per-user quota, or maximum site count.
- Back up `/data` and test restoration using [Manage Data And Backups](../manage-data-and-backups/).
- Apply operating system, Docker, Coolify, Traefik, and Buzz updates after reviewing their release notes.

## Understand Analytics Data

Buzz records aggregate site analytics in `/data/data.db`. It can derive a daily visitor hash from the client IP address and user agent, store referrer hosts, campaign values, paths, and country headers, and retain daily aggregate records. Raw visitor hashes become eligible for pruning after two days and are removed when Buzz writes a later analytics batch.

Set a stable, random `BUZZ_ANALYTICS_SECRET`. Otherwise Buzz falls back to `GITHUB_CLIENT_SECRET`, then a process-local random value. The bundled Compose files pass this variable to the server when it is defined in the deployment environment.

Determine your own disclosure, consent, retention, and access obligations for the jurisdictions where you operate.

These controls describe current implementation behavior, not a security certification or guarantee. Assess the deployment against your own threat model before hosting sensitive or untrusted workloads.
