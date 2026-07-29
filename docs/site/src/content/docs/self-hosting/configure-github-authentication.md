---
title: Configure GitHub Authentication
description: Create the GitHub OAuth app used by the dashboard and CLI.
sidebar:
  order: 4
---

Configure a GitHub OAuth app so users can sign in to the Buzz dashboard. Buzz requests the `read:user` scope and stores its own 30-day session after GitHub confirms the user.

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
    - **Authorization callback URL**: `https://buzz.example.com/dashboard/login/github/callback`

4. Select **Register application**.
5. Copy the displayed **Client ID**.
6. Select **Generate a new client secret**, then copy the secret immediately.

## Configure Buzz

Set both values on the Buzz server:

```text
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret
```

Keep the client secret out of source control and restrict access to the deployment environment.

Restart or redeploy Buzz after changing either variable. Buzz reads environment variables when the server process starts.

## Verify Sign-In

1. Open `https://buzz.example.com`.
2. Start the GitHub sign-in.
3. Authorize the OAuth app on GitHub.
4. Confirm that GitHub returns you to the Buzz dashboard.
5. Configure the CLI's server URL and run:

   ```bash
   buzz login
   buzz whoami
   ```

   `buzz login` opens Buzz in a browser for approval. Sign in through the dashboard if needed, enter the code shown by the CLI, then run `buzz whoami` to confirm the GitHub account.

By default any GitHub user who can reach the server can sign in and deploy sites. Set `BUZZ_ALLOW_REGISTRATION=false` to stop new sign-ups, or `BUZZ_ALLOWED_GITHUB_USERS` to allow only specific GitHub usernames. Review [Security](../security/) before exposing Buzz to users you don't administer.

## Roll Back Authentication Changes

If sign-in fails after replacing an OAuth app or credential, restore the previous `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, then restart or redeploy Buzz. Delete or revoke the unused OAuth app or client secret only after the previous configuration works again.
