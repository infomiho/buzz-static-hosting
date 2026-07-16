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

## Prepare The Optional Custom Domain Control Plane

Custom domains are disabled by default. Skip this section when the operator does not want Buzz to manage custom domains. The existing dashboard and wildcard site hosting do not require these changes.

1. Generate a high-entropy control token and keep it out of source control:

   ```bash
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

2. Add these variables to the Buzz application and redeploy it:

   ```text
   BUZZ_CUSTOM_DOMAINS_ENABLED=true
   BUZZ_TRAEFIK_CONTROL_TOKEN=replace-with-the-generated-token
   ```

   The application starts a private listener on port `8081` with the network alias `buzz-traefik-control`. The port is not published or attached to a public Traefik router.

3. Return to **Servers > Proxy** and add these arguments to the Traefik service's `command` list. Replace both token placeholders with the same generated token and replace the email placeholder:

   ```text
   --providers.http=true
   --providers.http.endpoint=http://buzz-traefik-control:8081/traefik
   --providers.http.headers.Authorization=Bearer replace-with-the-generated-token
   --providers.http.pollInterval=5s
   --providers.http.pollTimeout=2s
   --providers.http.maxResponseBodySize=1048576
   --api=true
   --entrypoints.buzz-admin.address=:8082
   --certificatesresolvers.buzz-custom.acme.email=admin@example.com
   --certificatesresolvers.buzz-custom.acme.storage=/traefik/acme.json
   --certificatesresolvers.buzz-custom.acme.httpchallenge.entrypoint=http
   --certificatesresolvers.buzz-custom.acme.caserver=https://acme-staging-v02.api.letsencrypt.org/directory
   ```

   Keep the existing Cloudflare DNS-01 `letsencrypt` resolver. The new `buzz-custom` resolver is separate and uses Coolify's `http` entrypoint for individual customer hostnames.

4. Add these labels to the Traefik service, replacing the token placeholder again:

   ```text
   traefik.enable=true
   traefik.http.routers.buzz-runtime-api.rule=PathPrefix(`/api`) && Header(`Authorization`, `Bearer replace-with-the-generated-token`)
   traefik.http.routers.buzz-runtime-api.entrypoints=buzz-admin
   traefik.http.routers.buzz-runtime-api.service=api@internal
   ```

   The `buzz-admin` entrypoint is not published on the host. Its authorization rule still prevents other containers on the shared Docker network from reading Traefik runtime state.

5. Save and restart the proxy through **Servers > Proxy**. Do not use **Reset proxy configuration**, which replaces custom proxy settings with Coolify defaults.

6. Open a terminal for the Buzz container and inspect the private readiness response:

   ```bash
   uv run python -c 'import json,os,urllib.request; token=os.environ["BUZZ_TRAEFIK_CONTROL_TOKEN"]; request=urllib.request.Request("http://localhost:8081/ready",headers={"Authorization":f"Bearer {token}"}); print(json.dumps(json.load(urllib.request.urlopen(request)),indent=2))'
   ```

The automated checks cover provider polling, protected runtime API access, entrypoint `https`, and service `buzz@docker`. They do not claim that the unused resolver or ACME storage has issued a certificate. Exercise those later with a staging hostname before production custom domains are admitted.

Back up the modified proxy configuration and `/data/coolify/proxy`. A proxy restart loses the HTTP provider's in-memory snapshot until it polls Buzz again. A failed poll on a running proxy leaves the previous valid snapshot active.

### Exercise Staging Routing

Keep the `buzz-custom` resolver on Let's Encrypt's staging directory during this step. Staging certificates are not trusted by browsers and must not be presented as production custom-domain support.

1. Add this variable to the Buzz application and redeploy it:

   ```text
   BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED=true
   BUZZ_CUSTOM_DOMAIN_INGRESS_IPS=your-public-ingress-ip
   BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED=true
   ```

2. Open a site's detail page, add a custom hostname, publish the displayed TXT record, and select **Check now**.
3. After ownership verifies, Buzz publishes one exact generation-qualified `Host()` router through the HTTP provider.
4. Wait for the dashboard to report that Traefik acknowledged the router.
5. Open the staging verification URL displayed by Buzz. The request records that the public hostname reached the expected Buzz site. No other path on that custom hostname is served during this stage.

After creating the controlled test claim, set `BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED=false` and redeploy Buzz to close admission while validation continues.

To stop staging routing, set `BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED=false` and redeploy Buzz. Buzz immediately emits an empty custom-domain snapshot and stops serving challenge paths. Keep the provider configured until every claim reports that its router was withdrawn. Turning off `BUZZ_CUSTOM_DOMAINS_ENABLED` or deleting the provider first is not a withdrawal mechanism because a running Traefik instance retains its last valid snapshot after polling failures.

### Enable Direct Production Domains

Complete the staging publication, challenge, withdrawal, reuse, and proxy-restart checks before enabling production certificates.

1. Add a separate production resolver to **Servers > Proxy** so staging certificate history remains isolated:

   ```text
   --certificatesresolvers.buzz-production.acme.email=admin@example.com
   --certificatesresolvers.buzz-production.acme.storage=/traefik/acme-production.json
   --certificatesresolvers.buzz-production.acme.httpchallenge.entrypoint=http
   ```

2. Save and restart the proxy, then configure Buzz and redeploy it:

   ```text
   BUZZ_TRAEFIK_CERT_RESOLVER=buzz-production
   BUZZ_CUSTOM_DOMAIN_INGRESS_IPS=your-public-ingress-ip
   BUZZ_CUSTOM_DOMAIN_ORIGIN_HOST=coolify-proxy
   BUZZ_CUSTOM_DOMAIN_ROUTING_ENABLED=true
   BUZZ_MAX_CUSTOM_DOMAINS_PER_SITE=5
   BUZZ_MAX_CUSTOM_DOMAINS_PER_USER=20
   BUZZ_MAX_CUSTOM_DOMAINS_SERVER_WIDE=1000
   ```

   Replace the example ingress address with every public IPv4 or IPv6 address that reaches this proxy. Every DNS answer for a direct custom hostname must be public and present in this allowlist. Keep staging and production resolvers in separate ACME storage files; an existing staging certificate in Traefik's global TLS store can suppress production issuance for the same hostname.

3. Keep `BUZZ_CUSTOM_DOMAIN_ADMISSION_ENABLED=false` during controlled rollout. Set it to `true` only when site owners should be able to create new claims. The three positive quota settings limit pending and verified aliases per site, per user, and across the server. An alias awaiting acknowledged withdrawal continues to consume quota.

Buzz marks each routed hostname active only after its DNS answers match the ingress allowlist and a trusted TLS request through `coolify-proxy:443` returns the exact generation challenge. Multiple aliases can serve one deployment, but each retains independent ownership, routing, TLS, diagnostics, and removal state. Custom-host requests and same-site alias referrers resolve through the canonical Buzz site identity. Cloudflare-proxied hostnames remain unsupported.

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
