---
title: Configure DNS And TLS
description: Route the Buzz domain and every site hostname with a wildcard certificate.
sidebar:
  order: 4
---

Route the Buzz dashboard and all site hostnames to one server, then issue a certificate that covers both hostname forms. The bundled deployments use Cloudflare DNS and Let's Encrypt DNS-01 validation.

## Before You Start

You need:

- A public server IP address.
- A Cloudflare-managed DNS zone for `example.com`.
- Permission to edit the zone's DNS records and API tokens.
- TCP port `443` open to the server. Port `80` is used only for the bundled HTTP-to-HTTPS redirect.

## Point Both Hostname Forms At The Server

In Cloudflare, open **DNS > Records** and select **Add record**. Create these records in the `example.com` zone:

- Set **Type** to **A**, **Name** to `buzz`, **IPv4 address** to the server IP, and **Proxy status** to **DNS only**.
- Set **Type** to **A**, **Name** to `*.buzz`, **IPv4 address** to the server IP, and **Proxy status** to **DNS only**.

The wildcard record doesn't cover `buzz.example.com`, so both records are required. Use equivalent `AAAA` records only when the server has working public IPv6 routing.

Verify both records from outside the server's local network:

```bash
dig +short buzz.example.com
dig +short my-site.buzz.example.com
```

Both commands should return the public server IP.

## Create A DNS Challenge Token

In Cloudflare, open **My Profile > API Tokens**, select **Create Token**, and use the **Edit zone DNS** template. Restrict **Zone Resources** to the `example.com` zone. The resulting token must be able to create and remove the DNS TXT record used for `_acme-challenge.buzz.example.com`.

Set this token as:

- `CF_API_TOKEN` in the standalone Docker Compose `.env` file.
- `CF_DNS_API_TOKEN` on Coolify's Traefik proxy.

Treat the token as a secret. Revoke and replace it if it appears in logs, shell history, or source control.

## Issue The Wildcard Certificate

- For Docker Compose, set `ACME_EMAIL` and start the stack. The bundled Traefik service requests `buzz.example.com` and `*.buzz.example.com` from Let's Encrypt.
- For Coolify, configure the proxy-level wildcard router in [Deploy With Coolify](../coolify/).

DNS-01 validation doesn't require Cloudflare's HTTP proxy. These guides keep both records at **DNS only** so requests reach the configured Traefik instance directly.

## Verify DNS, Routing, And TLS

Check the dashboard endpoint:

```bash
curl --fail --show-error https://buzz.example.com/health
```

Inspect the certificate presented for a site hostname:

```bash
openssl s_client -connect my-site.buzz.example.com:443 -servername my-site.buzz.example.com </dev/null 2>/dev/null | openssl x509 -noout -issuer -dates -ext subjectAltName
```

The subject alternative names should include `*.buzz.example.com`. A site that hasn't been deployed can return `404`; that still verifies DNS, routing, and TLS if the certificate is valid.

Certificate issuance depends on Cloudflare and Let's Encrypt. Buzz doesn't control their availability or issuance limits. See [Troubleshoot Self-Hosting](../../troubleshooting/self-hosting/) if Traefik can't obtain the certificate.
