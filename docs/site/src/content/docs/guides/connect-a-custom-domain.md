---
title: Connect a Custom Domain
description: Verify a hostname, point it to Buzz, and understand each connection state.
---

Buzz keeps the permanent site URL working while you connect independently managed hostnames.

## Add the hostname

Open the site in the Buzz dashboard, select **Add custom domain**, and enter a hostname such as `www.example.com`. Do not include a scheme, port, path, or wildcard.

When automatic connections are available, Buzz detects direct DNS and Cloudflare proxying. Otherwise, choose the connection type shown by the server.

## Add the DNS records

The dashboard shows all records needed for setup:

1. Add the TXT record to prove ownership.
2. Add the displayed A and/or AAAA routing records to point the hostname to Buzz.
3. Select **Check ownership** after the TXT record propagates.

For a direct connection, keep the displayed routing records DNS-only. For Cloudflare, enable proxying, use Full (strict), and bypass cache or security rules for `/.well-known/buzz-domain-check/*`.

Pending ownership setup expires after the time shown in the dashboard. If it expires before DNS propagates, add the domain again to receive a new TXT value.

Buzz checks routing and TLS automatically after ownership is verified. DNS propagation and certificate setup can take time.

## Read the status

| Status | Meaning | What to do |
| --- | --- | --- |
| Verify ownership | Buzz is waiting for the TXT record. | Add the displayed records, then check ownership. |
| Update DNS | Ownership is verified, but DNS does not point to a supported Buzz path. | Add or correct the displayed routing records. |
| Connecting | Buzz is publishing the route and setting up TLS. | No action is needed. |
| Connected | Buzz has validated the current connection. | Visit the domain. |
| Updating | Buzz detected a supported DNS path change and is validating it. | Usually no action is needed. Public reachability still depends on DNS and provider configuration. |
| Action needed | Buzz could not validate ownership, DNS, routing, TLS, or proxy behavior. | Check DNS and proxy rules, then retry when offered. |
| Removing | Buzz is withdrawing the route. | Wait for removal to finish before reusing the hostname. |

Open **Manage domain** for technical evidence, Cloudflare diagnostics, update cancellation, or removal. These details are not required during normal setup.

## Change the connection later

Automatic claims keep the same hostname ownership and router identity when DNS changes between direct and Cloudflare paths. Buzz waits for stable DNS and validates the target before switching its effective policy.

Buzz cannot guarantee uninterrupted public reachability while DNS caches or a proxy provider still serve the previous path. Canonical Buzz hosting remains independent from custom-domain transitions.
