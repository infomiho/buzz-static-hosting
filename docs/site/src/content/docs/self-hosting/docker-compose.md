---
title: Deploy With Docker Compose
description: Run Buzz and its Traefik proxy on a Docker host.
sidebar:
  order: 5
---

Deploy Buzz with the repository's standalone Docker Compose file. This path runs Buzz and Traefik on the same host and obtains a wildcard certificate through Cloudflare DNS.

## Before You Start

Complete these prerequisites:

- Install Docker Engine with the Docker Compose plugin.
- Point `buzz.example.com` and `*.buzz.example.com` at the host.
- Allow inbound TCP traffic on port `443`. Port `80` is also required when the optional custom-domain HTTP-01 resolver is enabled; otherwise it is used only for the bundled HTTP-to-HTTPS redirect.
- Create a Cloudflare API token and a GitHub OAuth app.
- Complete the access decision in the [Self-Hosting Overview](../overview/). Configure upstream controls before deployment if Buzz is for a closed group.

Follow [Configure DNS And TLS](../configure-dns-and-tls/) and [Configure GitHub Authentication](../configure-github-authentication/) for the required credentials.

## Configure The Deployment

1. Clone the repository and open the server directory:

   ```bash
   git clone https://github.com/infomiho/buzz-static-hosting.git buzz
   cd buzz/server
   ```

2. Create the environment file and restrict access to it:

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

3. Set at least these values in `.env`:

   ```text
   BUZZ_DOMAIN=buzz.example.com
   GITHUB_CLIENT_ID=your-github-client-id
   GITHUB_CLIENT_SECRET=your-github-client-secret
   CF_API_TOKEN=your-cloudflare-api-token
   ACME_EMAIL=admin@example.com
   ```

   Keep `.env` out of source control. Set `ACME_EMAIL` to an address that can receive Let's Encrypt notices.

## Start Buzz

Build and start the services:

```bash
docker compose up -d --build
```

The Compose project creates two named volumes:

- `buzz_buzz-data` stores all Buzz data at `/data`.
- `buzz_traefik-certs` stores Traefik's ACME state.

Do not remove either volume during routine updates.

## Verify The Deployment

1. Confirm that both containers are running:

   ```bash
   docker compose ps
   ```

2. Check the server through Traefik:

   ```bash
   curl --fail --show-error https://buzz.example.com/health
   ```

   The response is:

   ```json
   {"status":"ok"}
   ```

3. Open `https://buzz.example.com` and start a GitHub sign-in.

## Prepare The Optional Custom Domain Control Plane

Custom domains are disabled by default. Skip this section when the operator does not want Buzz to manage custom domains.

To enable the private control plane, generate a random token:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Add these values to `.env`:

```text
BUZZ_CUSTOM_DOMAINS_ENABLED=true
BUZZ_TRAEFIK_CONTROL_TOKEN=replace-with-the-generated-token
```

Recreate the services:

```bash
docker compose up -d --build
```

The bundled Compose configuration then enables Traefik's HTTP provider, which polls the private Buzz listener on port `8081`. The port is exposed only to the Compose network. It also prepares:

- The `buzz-custom` ACME resolver using HTTP-01 on entrypoint `web`.
- A protected Traefik runtime API on the private `buzz-admin` entrypoint.
- Runtime checks for entrypoint `websecure` and service `buzz@docker`.

Check the private readiness response:

```bash
docker compose exec server uv run python -c 'import json,os,urllib.request; token=os.environ["BUZZ_TRAEFIK_CONTROL_TOKEN"]; request=urllib.request.Request("http://localhost:8081/ready",headers={"Authorization":f"Bearer {token}"}); print(json.dumps(json.load(urllib.request.urlopen(request)),indent=2))'
```

This stage can confirm provider polling, runtime API access, `buzz@docker`, and the HTTPS entrypoint. It cannot prove that the unused ACME resolver can issue certificates. Certificate issuance and ACME storage are exercised later with a staging hostname.

To exercise staging-only exact-host routing, add this value and recreate the services:

```text
BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED=true
```

The bundled `buzz-custom` resolver defaults to Let's Encrypt's staging directory through `BUZZ_CUSTOM_DOMAIN_ACME_CA_SERVER`. Add and TXT-verify a hostname from its site detail page, wait for router acknowledgement, then open the displayed verification URL. Only that reserved verification path is available on the custom hostname in this stage.

Set `BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED=false` to withdraw staging routers. Wait for acknowledged withdrawal before disabling the custom-domain control plane. Do not change `BUZZ_CUSTOM_DOMAIN_ACME_CA_SERVER` to production until the production custom-domain stage is implemented and verified.

If a container exits or TLS isn't issued, inspect the logs:

```bash
docker compose logs server traefik
```

See [Troubleshoot Self-Hosting](../../troubleshooting/self-hosting/) for common causes.

## Update Or Roll Back

Create a [cold backup](../manage-data-and-backups/) before an update. Then update and rebuild:

```bash
git pull --ff-only
docker compose up -d --build
```

To roll back application code, check out the previous known-good revision and run `docker compose up -d --build` again. Code rollback doesn't reverse database changes. Buzz doesn't currently provide database migration rollback tooling, so retain a pre-update backup.
