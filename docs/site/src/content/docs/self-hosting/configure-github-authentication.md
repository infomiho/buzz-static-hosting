---
title: Configure GitHub Authentication
description: Create the GitHub OAuth app used by the dashboard and CLI.
sidebar:
  order: 4
---

Configure a GitHub OAuth app so users can sign in to the Buzz dashboard and CLI through GitHub Device Flow. Buzz requests the `read:user` scope and stores its own 30-day session after GitHub confirms the user.

## Before You Start

You need:

- The public dashboard URL you intend to use, such as `https://buzz.example.com`.
- Permission to create an OAuth app in a GitHub account or organization.
- Access to the Buzz deployment's secret environment variables.

Complete [Configure DNS And TLS](../configure-dns-and-tls/) first so the OAuth app uses the intended public dashboard URL.

## Create The OAuth App

1. In GitHub, open **Settings > Developer settings > OAuth Apps**.
2. Select **New OAuth App**. GitHub may show **Register a new application** when no OAuth apps exist yet.
3. Enter these values:

   - **Application name**: `Buzz`
   - **Homepage URL**: `https://buzz.example.com`
   - **Authorization callback URL**: `https://buzz.example.com/`

   Buzz uses Device Flow and doesn't call the callback URL, but GitHub requires this field when registering an OAuth app.

4. Select **Enable Device Flow**.
5. Select **Register application**.
6. Copy the displayed **Client ID**.
7. Select **Generate a new client secret**, then copy the secret immediately.

## Configure Buzz

Set both values on the Buzz server:

```text
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret
```

The current server startup check requires both variables, even though GitHub Device Flow exchanges use the client ID. Keep the client secret out of source control and restrict access to the deployment environment.

Restart or redeploy Buzz after changing either variable. Buzz reads environment variables when the server process starts.

## Verify Sign-In

1. Open `https://buzz.example.com`.
2. Start the GitHub sign-in.
3. Open the GitHub verification URL shown by Buzz, enter the displayed code, and authorize the OAuth app.
4. Confirm that Buzz opens the dashboard.
5. Configure the CLI's server URL and run:

   ```bash
   buzz login
   buzz whoami
   ```

   `buzz whoami` should show the GitHub account used during authorization.

By default any GitHub user who can reach the server can sign in and deploy sites. Set `BUZZ_ALLOW_REGISTRATION=false` to stop new sign-ups, or `BUZZ_ALLOWED_GITHUB_USERS` to allow only specific GitHub usernames. Review [Security](../security/) before exposing Buzz to users you don't administer.

## Roll Back Authentication Changes

If sign-in fails after replacing an OAuth app or credential, restore the previous `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, then restart or redeploy Buzz. Delete or revoke the unused OAuth app or client secret only after the previous configuration works again.
