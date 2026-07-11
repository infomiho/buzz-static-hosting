---
title: Deploy With Coolify
description: Run Buzz behind Coolify's Traefik proxy with wildcard TLS.
sidebar:
  order: 3
---

Deploy Buzz as a Docker Compose application while Coolify manages the host and Traefik proxy. The repository's Coolify file connects Buzz to the external `coolify` network and stores data in `buzz_buzz-data`.

## Before You Start

You need:

- A working Coolify installation with its proxy enabled.
- A repository connection that can read the Buzz repository.
- DNS control for `buzz.example.com`.
- A Cloudflare API token with permission to edit DNS records for the zone.
- A configured GitHub OAuth app.

The Coolify procedure below changes the server-wide `letsencrypt` resolver from HTTP validation to Cloudflare DNS validation. Every application using that resolver is affected. Use these steps only when the proxy is dedicated to Cloudflare-managed domains. Otherwise, add a separate DNS resolver for Buzz and point only the `wildcard-certs` router to it. Back up the proxy configuration before editing it.

## Create The Application

1. Create a Docker Compose application from the Buzz repository.
2. Set the Compose file to `docker-compose.coolify.yml`.
3. Enable **Raw Docker Compose Deployment** in the application settings. This prevents Coolify's YAML processing from breaking `${BUZZ_DOMAIN}` substitution in Traefik labels.
4. Leave **FQDN/Domains** empty. The Compose labels define both the base and wildcard routes.
5. Add these environment variables:

   ```text
   BUZZ_DOMAIN=buzz.example.com
   GITHUB_CLIENT_ID=your-github-client-id
   GITHUB_CLIENT_SECRET=your-github-client-secret
   ```

6. Deploy the application.

The Compose file expects Coolify's external Docker network to be named `coolify`. If the deployment reports that this network doesn't exist, confirm the network name on the server before changing the Compose file.

## Configure Coolify's Proxy

Open **Servers > Proxy** and edit the Traefik Compose configuration there. Do not edit `/data/coolify/proxy/docker-compose.yml` directly because Coolify can replace direct changes during proxy actions or upgrades.

1. Add the Cloudflare token to the Traefik service:

   ```yaml
   environment:
     - CF_DNS_API_TOKEN=your-cloudflare-api-token
   ```

2. Replace the Let's Encrypt HTTP challenge arguments with DNS challenge arguments:

   ```text
   --certificatesresolvers.letsencrypt.acme.dnschallenge=true
   --certificatesresolvers.letsencrypt.acme.dnschallenge.provider=cloudflare
   --certificatesresolvers.letsencrypt.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53
   --certificatesresolvers.letsencrypt.acme.dnschallenge.delaybeforecheck=60
   ```

3. Add these labels to the Traefik service:

   ```text
   traefik.http.routers.wildcard-certs.tls.certresolver=letsencrypt
   traefik.http.routers.wildcard-certs.tls.domains[0].main=buzz.example.com
   traefik.http.routers.wildcard-certs.tls.domains[0].sans=*.buzz.example.com
   ```

4. Save the proxy configuration through **Servers > Proxy**, then restart the proxy.

The Buzz application labels intentionally set `tls=true` without `tls.certresolver`. Keep certificate issuance on the single proxy-level `wildcard-certs` router. Adding a resolver to the Buzz routers can create duplicate ACME challenges for the same DNS record.

If another application stops renewing certificates, restore the proxy backup through **Servers > Proxy**, restart the proxy, and configure a separate DNS resolver before retrying the Buzz certificate.

## Verify The Deployment

1. Confirm that the application is running in Coolify.
2. Open the application logs and check that the server didn't report missing GitHub credentials.
3. Request the health endpoint:

   ```bash
   curl --fail --show-error https://buzz.example.com/health
   ```

4. Verify the wildcard certificate with the commands in [Configure DNS And TLS](../configure-dns-and-tls/).

If Coolify replaces the proxy settings, return to **Servers > Proxy**, restore the saved configuration there, and restart the proxy. See [Troubleshoot Self-Hosting](../../troubleshooting/self-hosting/) for routing and certificate failures.
