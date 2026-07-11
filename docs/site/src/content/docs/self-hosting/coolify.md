---
title: Deploy With Coolify
description: Run Buzz behind Coolify's Traefik proxy with wildcard TLS.
sidebar:
  order: 6
---

Deploy Buzz as a Docker Compose application while Coolify manages the host and Traefik proxy. The repository's Coolify file connects Buzz to the external `coolify` network and stores data in `buzz_buzz-data`.

## Before You Start

You need:

- A working Coolify installation with its proxy enabled.
- A repository connection that can read the Buzz repository.
- DNS control for `buzz.example.com`.
- A Cloudflare API token with permission to edit DNS records for the zone.
- A configured GitHub OAuth app.
- A Coolify server whose Traefik proxy is dedicated to Cloudflare-managed domains.
- A backup of the current proxy configuration and a maintenance window for restarting it.

The procedure below changes Coolify's server-wide `letsencrypt` resolver from HTTP validation to Cloudflare DNS validation. This affects certificate issuance and renewal for every application using that resolver. A mistake can interrupt TLS for all applications behind the proxy.

Proceed only when this Coolify proxy is dedicated to Cloudflare-managed domains and all applications using `letsencrypt` can use the Cloudflare DNS challenge. If the proxy serves any other DNS provider or must retain HTTP validation, stop here. Deploy Buzz with [standalone Docker Compose](../docker-compose/) or design and test a separate resolver outside this guide. The repository doesn't provide a verified shared-proxy procedure.

Complete the access decision in the [Self-Hosting Overview](../overview/) before deployment. Configure upstream controls first if Buzz is for a closed group.

## Create The Application

1. Create a Docker Compose application from the Buzz repository.
2. Set the Compose file to `docker-compose.coolify.yml`.
3. Enable **Raw Docker Compose Deployment** in the application settings. The repository Compose file supplies the routing labels directly. Coolify documents this mode in [Raw Docker Compose Deployment](https://coolify.io/docs/knowledge-base/docker/compose#raw-docker-compose-deployment).
4. Leave **FQDN/Domains** empty. The Compose labels define both the base and wildcard routes.
5. Add these environment variables:

   ```text
   BUZZ_DOMAIN=buzz.example.com
   GITHUB_CLIENT_ID=your-github-client-id
   GITHUB_CLIENT_SECRET=your-github-client-secret
   ```

Do not deploy the application until the proxy is configured. This avoids exposing Buzz with an incomplete TLS setup.

## Configure Coolify's Proxy

Open **Servers > Proxy** and edit the Traefik Compose configuration there. Do not edit `/data/coolify/proxy/docker-compose.yml` directly because Coolify can replace direct changes during proxy actions or upgrades.

Copy the complete current configuration to secure off-host storage before changing it. The copy is the rollback source for the server-wide proxy.

1. Add the Cloudflare token to the Traefik service:

   ```yaml
   environment:
     - CF_DNS_API_TOKEN=your-cloudflare-api-token
   ```

2. Remove the existing `letsencrypt` HTTP challenge arguments and add these DNS challenge arguments to the Traefik service's `command` list:

   ```text
    --certificatesresolvers.letsencrypt.acme.dnschallenge=true
    --certificatesresolvers.letsencrypt.acme.dnschallenge.provider=cloudflare
    --certificatesresolvers.letsencrypt.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53
    ```

   Keep Coolify's existing `--certificatesresolvers.letsencrypt.acme.storage=/traefik/acme.json` argument. It preserves the resolver's existing ACME state.

3. Add these labels to the Traefik service:

   ```text
   traefik.http.routers.wildcard-certs.tls.certresolver=letsencrypt
   traefik.http.routers.wildcard-certs.tls.domains[0].main=buzz.example.com
   traefik.http.routers.wildcard-certs.tls.domains[0].sans=*.buzz.example.com
   ```

4. Save the proxy configuration through **Servers > Proxy**, then restart the proxy from that page.

The Buzz application labels intentionally set `tls=true` without `tls.certresolver`. Keep certificate issuance on the single proxy-level `wildcard-certs` router. Adding a resolver to the Buzz routers can create duplicate ACME challenges for the same DNS record.

Traefik requires DNS-01 validation for wildcard certificates and derives certificate names from a router's TLS domains. See Traefik's [ACME certificate resolver documentation](https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/).

If the proxy fails to restart, Buzz has no valid certificate, or another application loses TLS, paste the complete saved configuration back into **Servers > Proxy**, save it, and restart the proxy. Do not keep retrying certificate issuance against the production Let's Encrypt endpoint while the same error persists.

## Deploy And Verify Buzz

1. Deploy the application through Coolify.
2. Confirm that the application is running. The Compose file expects Coolify's external Docker network to be named `coolify`. If deployment reports that this network doesn't exist, confirm the server's network name before changing the Compose file.
3. Open the application logs and check that the server didn't report missing GitHub credentials.
4. Request the health endpoint:

   ```bash
   curl --fail --show-error https://buzz.example.com/health
   ```

5. Run the certificate inspection command in [Verify DNS, Routing, And TLS](../configure-dns-and-tls/#verify-dns-routing-and-tls) and confirm that the certificate covers `*.buzz.example.com`.
6. Open one existing application that also uses the `letsencrypt` resolver and confirm that its certificate and route still work.

If Coolify replaces the proxy settings, return to **Servers > Proxy**, restore the saved configuration there, and restart the proxy. See [Troubleshoot Self-Hosting](../../troubleshooting/self-hosting/) for routing and certificate failures.
