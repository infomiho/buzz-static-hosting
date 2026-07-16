---
title: Understand Analytics
description: Learn what Buzz records for hosted sites and how dashboard metrics are calculated.
sidebar:
  order: 7
---

Buzz records server-side traffic metrics for hosted sites. Your pages do not need an analytics script.

## View Site Analytics

Sign in to the dashboard at the Buzz server URL, select a site, and find **Analytics** on the site detail page.

The dashboard shows:

- **Views**, **Visitors**, **Bandwidth**, and **404s** for all recorded dates.
- A chart of views and visitors for the last 30 days.
- A **Breakdown** of top pages, external referrer hosts, campaigns, and countries from the last 30 days.

Country data appears only when the request includes a supported country header from the hosting proxy. Campaigns combine available `utm_source`, `utm_medium`, and `utm_campaign` values.

Analytics remain attached to the canonical site when it has custom-domain aliases. Navigation between the permanent Buzz hostname and any alias of the same site is treated as internal traffic rather than an external referrer.

## Understand What Buzz Counts

Buzz records a view for a successful `GET` request served as HTML. It records a 404 when a request that appears to expect a page returns HTTP status `404`. Buzz uses request headers and the path suffix to distinguish these requests from assets.

**Bandwidth** adds the full response-file sizes for recorded HTML views and page-like 404s. It excludes static assets, protocol overhead, and partial-transfer differences, so it is not total network traffic.

Buzz excludes requests when:

- The request is not a `GET` request.
- The browser sends `DNT: 1` or `Sec-GPC: 1`.
- The request is marked as prefetch or prerender traffic.
- The user agent matches Buzz's bot filter.

## Understand Visitor Privacy

Buzz estimates daily visitors from a hash of the site name, date, client IP address, and user agent. The hash uses the server's analytics secret. Buzz does not store the source IP address in analytics tables.

Visitor hashes become eligible for pruning after two days. Pruning occurs when Buzz writes a later analytics batch, so an inactive server can retain old hashes until another batch is written. Daily aggregate counts remain. The all-time **Visitors** value adds the daily visitor counts, so one person visiting on two dates can contribute two visitors.

Deleting a site also deletes its analytics records.
