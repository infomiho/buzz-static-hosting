---
title: Connect Google Search Console
description: Show Google search terms for Buzz sites in the dashboard.
sidebar:
  order: 8
---

Connect a Google service account to a Search Console domain property so site owners can view recent Google search terms in the dashboard. Buzz uses read-only API access and filters the shared property by each site URL.

## Before You Start

You need:

- A verified Search Console domain property for `buzz.example.com`.
- Owner permission on that property so you can add a user.
- A Google Cloud project where you can enable APIs and create a service account key.
- Access to Buzz's secret environment variables.

A domain property is the intended setup because it includes `my-site.buzz.example.com` and every other site hostname. Buzz has one global `BUZZ_GSC_PROPERTY` setting, so separate URL-prefix properties don't cover all sites.

## Create The Service Account

1. In Google Cloud Console, select the project that will own the credentials.
2. Open **APIs & Services > Library**, find **Google Search Console API**, and select **Enable**.
3. Open **IAM & Admin > Service Accounts** and select **Create service account**.
4. Create the service account without granting project roles. Search Console access is granted separately.
5. Open the service account, select **Keys > Add key > Create new key**, choose **JSON**, and select **Create**.

The downloaded JSON contains a private key. Store it as a secret and delete unused keys from the service account.

## Grant Search Console Access

1. Open the `sc-domain:buzz.example.com` property in Search Console.
2. Open **Settings > Users and permissions**.
3. Select **Add user**.
4. Enter the service account's `client_email` from the JSON key.
5. Grant **Restricted** permission and save.

Buzz requests the `webmasters.readonly` API scope and reads Performance data. The service account doesn't need to be an owner. If Google returns a permission error, confirm that the account was added to the correct domain property before granting broader access.

## Configure Buzz

For Coolify, set the complete service-account JSON as the secret value of `BUZZ_GSC_CREDENTIALS` and set the property explicitly:

```text
BUZZ_GSC_CREDENTIALS={"type":"service_account","client_email":"buzz@example-project.iam.gserviceaccount.com","private_key":"..."}
BUZZ_GSC_PROPERTY=sc-domain:buzz.example.com
```

For standalone Docker Compose, store the downloaded key as `server/gsc-key.json`, restrict it with `chmod 600`, and keep it out of source control. Create `server/compose.gsc.yml`:

```yaml
services:
  server:
    environment:
      BUZZ_GSC_CREDENTIALS: /run/secrets/gsc-key.json
    volumes:
      - ./gsc-key.json:/run/secrets/gsc-key.json:ro
```

Apply the override:

```bash
docker compose -f docker-compose.yml -f compose.gsc.yml up -d
```

When `BUZZ_GSC_PROPERTY` is unset, Buzz defaults to `sc-domain:<BUZZ_DOMAIN>`. Setting it explicitly makes the intended property clear.

Restart or redeploy Buzz after changing the settings. Invalid or unreadable credentials disable search terms and produce an error in the server log; they don't stop the server.

## Verify Search Terms

1. Sign in to the dashboard.
2. Open a site with Search Console data.
3. Expand its analytics details and find **Google search terms**.

Buzz requests up to ten query terms from a 30-day window ending two days before the current date. Search Console can return no rows for a new or low-traffic site, so an empty list doesn't prove that configuration failed. Check the server logs for credential or API errors and confirm the service account appears in **Settings > Users and permissions**.
