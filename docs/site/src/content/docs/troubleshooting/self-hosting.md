---
title: Troubleshoot Self-Hosting
description: Diagnose startup, DNS, TLS, routing, authentication, and data failures.
sidebar:
  order: 2
---

Start with container status and logs, then test DNS, TLS, and the health endpoint separately. This order distinguishes a server failure from a proxy or DNS failure.

## A Container Doesn't Stay Running

For standalone Docker Compose, inspect status and logs:

```bash
docker compose ps
docker compose logs server traefik
```

For Coolify, open the application's deployment and runtime logs, then check the proxy logs under **Servers > Proxy**.

Common server startup causes include:

- `GitHub OAuth not configured`: Set both `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, then restart Buzz.
- An invalid integer for a deployment limit: Restore a valid integer value and restart Buzz.
- `Could not reconcile ... deployment operation(s)`: Buzz found an unreadable or inconsistent journal under `/data/sites/.operations` and stopped to avoid publishing uncertain state.

For reconciliation failures, stop Buzz and create a cold copy of the complete data volume before investigating. Do not remove the journal or edit `data.db` independently. Buzz has no automated repair command for an operation it can't reconcile.

## The Health Endpoint Can't Be Reached

Test each layer:

1. Confirm `buzz.example.com` resolves to the public server IP:

   ```bash
   dig +short buzz.example.com
   ```

2. Confirm the server container is running and exposed to Traefik on port `8080`.
3. Confirm ports `80` and `443` are allowed by the host firewall and infrastructure firewall.
4. Request the public endpoint:

   ```bash
   curl --verbose https://buzz.example.com/health
   ```

A `421 Misdirected Request` means the request's `Host` header doesn't match `BUZZ_DOMAIN` or one of its site subdomains. Check `BUZZ_DOMAIN`, DNS, and proxy labels, then restart the server after correcting the environment.

## The Dashboard Works But Site Hostnames Don't

1. Check the wildcard record:

   ```bash
   dig +short my-site.buzz.example.com
   ```

2. Confirm it returns the same IP as `buzz.example.com`.
3. Confirm the Traefik configuration includes a wildcard `HostRegexp` route.
4. Confirm the certificate covers `*.buzz.example.com`.

The DNS record for `buzz.example.com` doesn't cover site hostnames. Add a separate `*.buzz` record as described in [Configure DNS And TLS](../../self-hosting/configure-dns-and-tls/).

## TLS Certificate Issuance Fails

Inspect Traefik logs for ACME and Cloudflare errors. Then check:

- The Cloudflare token has DNS edit permission for the correct zone.
- The token is available to Traefik as `CF_DNS_API_TOKEN`. The standalone Compose file maps `CF_API_TOKEN` to that name.
- The authoritative DNS zone is hosted by Cloudflare.
- Coolify has one proxy-level wildcard certificate router, while Buzz's routers use `tls=true` without `tls.certresolver`.
- The documented Coolify resolver change is used only on a proxy dedicated to Cloudflare-managed domains. Restore the saved proxy configuration if another application's TLS fails.
- The `buzz.example.com` and `*.buzz.example.com` records are **DNS only** for the documented setup.

Let's Encrypt issuance can also fail because of external availability or certificate rate limits. Do not repeatedly restart Traefik while the same ACME error persists.

## GitHub Sign-In Doesn't Start Or Complete

1. Confirm **Enable Device Flow** is selected in **Settings > Developer settings > OAuth Apps** for the configured app.
2. Confirm `GITHUB_CLIENT_ID` belongs to that OAuth app.
3. Confirm both GitHub variables are present in the running container, then restart Buzz.
4. Confirm the server can reach `github.com` and `api.github.com` over HTTPS.
5. Request a new code if GitHub reports that the device code expired or was denied.

The client secret is required by Buzz's startup validation even though the current Device Flow requests use the client ID.

## Data Disappears After A Redeploy

Confirm `/data` is mounted from the named volume `buzz_buzz-data`. A container filesystem without this mount is ephemeral.

```bash
docker volume inspect buzz_buzz-data
```

Do not run `docker compose down --volumes`, `docker system prune --volumes`, or `docker volume prune` during routine maintenance. In Coolify, also check whether optional unused-volume cleanup ran while Buzz was stopped. If the volume was removed, restore the latest checksum-verified backup using [Manage Data And Backups](../../self-hosting/manage-data-and-backups/). Buzz can't reconstruct `data.db` ownership, sessions, tokens, and analytics from site files alone.

## Google Search Terms Don't Appear

1. Check server startup logs for a credential-loading error.
2. Confirm `BUZZ_GSC_CREDENTIALS` contains valid service-account JSON or a readable container path.
3. Confirm the service account appears under Search Console **Settings > Users and permissions** for `sc-domain:buzz.example.com`.
4. Confirm **Google Search Console API** is enabled in the credential's Google Cloud project.
5. Allow for Search Console's reporting delay. Buzz queries a window ending two days before the current date.

An empty result can be valid for a new or low-traffic site. A dashboard error and an HTTP `502` from the search-terms endpoint indicate that the Search Console request failed.
