---
title: Deploy With Docker Compose
description: Run Buzz and its Traefik proxy on a Docker host.
sidebar:
  order: 2
---

Deploy Buzz with the repository's standalone Docker Compose file. This path runs Buzz and Traefik on the same host and obtains a wildcard certificate through Cloudflare DNS.

## Before You Start

Complete these prerequisites:

- Install Docker Engine with the Docker Compose plugin.
- Point `buzz.example.com` and `*.buzz.example.com` at the host.
- Allow inbound TCP traffic on port `443`. Allow port `80` if you want the bundled HTTP-to-HTTPS redirect.
- Create a Cloudflare API token and a GitHub OAuth app.

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
