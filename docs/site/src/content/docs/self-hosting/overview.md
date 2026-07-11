---
title: Self-Hosting Overview
description: Choose a supported deployment path and prepare a Buzz server.
sidebar:
  order: 1
---

Run Buzz on infrastructure you control with Docker Compose or Coolify. This section covers the public DNS, TLS, authentication, persistent data, and operational settings needed for a production installation.

## Prepare The Host

You need:

- A Linux server with Docker Engine and Docker Compose, or a Coolify server.
- A domain whose DNS records you can change. These guides use `buzz.example.com` as the Buzz domain.
- Public access to TCP port `443`. Port `80` is used by the bundled HTTP-to-HTTPS redirect.
- A GitHub account that can create an OAuth app.
- A backup destination outside the Docker host.

The bundled standalone deployment uses Cloudflare DNS for Let's Encrypt DNS-01 validation. If your DNS is hosted elsewhere, you must adapt the Traefik certificate resolver yourself. That configuration isn't provided or tested by this repository.

## Choose A Deployment Path

- Use [Docker Compose](../docker-compose/) when you manage Docker and Traefik on the host.
- Use [Coolify](../coolify/) when Coolify already manages the host and its Traefik proxy.

Both paths build the Buzz server from the repository and mount the named volume `buzz_buzz-data` at `/data`. The volume contains deployed site files, the SQLite database, users, sessions, deployment tokens, and analytics.

## Understand The Public Endpoints

Buzz routes requests by hostname:

- `https://buzz.example.com` serves the dashboard and API.
- `https://my-site.buzz.example.com` serves the site named `my-site`.

The base hostname and wildcard hostname must both resolve to the server. TLS must cover `buzz.example.com` and `*.buzz.example.com`.

## Complete The Production Setup

1. Choose the Buzz domain and [create the GitHub OAuth app](../configure-github-authentication/) for that intended URL.
2. [Configure DNS and TLS](../configure-dns-and-tls/) for the base and wildcard hostnames.
3. Deploy Buzz with [Docker Compose](../docker-compose/) or [Coolify](../coolify/) using the domain and GitHub credentials.
4. Verify HTTPS and GitHub sign-in.
5. [Create and verify a backup](../manage-data-and-backups/).
6. Review the [security boundaries](../security/) before allowing other users to sign in.

Use the [configuration reference](../../reference/configuration/) for the complete environment variable list.
