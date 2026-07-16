---
title: Deploy Your First Site
description: Install Buzz, sign in, publish a static build directory, and verify the site.
sidebar:
  order: 1
---

Install the Buzz CLI, connect it to a Buzz server, and deploy your first static site. The CLI packages your build directory and sends it to the server, which publishes the files at a site URL.

## Prerequisites

You need:

- Node.js 22 or later and npm.
- A GitHub account.
- A server URL supplied by your Buzz server operator, such as `https://buzz.example.com`.
- A project whose `./dist/index.html` file is ready to publish.

## Install The CLI

1. Install the CLI globally:

   ```bash
   npm install --global @infomiho/buzz-cli
   ```

2. Confirm that the command is available:

   ```bash
   buzz --version
   ```

## Connect To The Server

1. Replace the example URL with the server URL supplied by your operator:

   ```bash
   buzz config server https://buzz.example.com
   ```

2. Start GitHub sign-in:

   ```bash
   buzz login
   ```

3. Open the URL printed by the CLI, enter the displayed code, and approve access.

4. Verify the resulting session:

   ```bash
   buzz whoami
   ```

## Deploy The Site

`my-site` is a replaceable example site name. Choose a globally unique name on your Buzz server using lowercase letters, numbers, and hyphens.

1. Confirm that the entry page exists:

   ```bash
   test -f ./dist/index.html
   ```

2. Deploy the build directory with your chosen site name:

   ```bash
   buzz deploy ./dist --subdomain my-site
   ```

3. Confirm that the CLI prints the site URL:

   ```text
   Deployed to https://my-site.buzz.example.com
   ```

   Your site's Buzz domain may differ from `buzz.example.com`.

4. Open the printed URL, then verify it over HTTP:

   ```bash
   curl --fail --show-error https://my-site.buzz.example.com/
   ```

If another user owns the site, choose a different name. If you own it through another GitHub account, sign out, sign in with that account, confirm it with `buzz whoami`, and retry.

## Redeploy The Site

After a successful deployment, Buzz writes `my-site` to a `CNAME` file in the directory where you ran the command. This file stores the Buzz site name. It is not a DNS CNAME record.

Build the site again, then redeploy from the same project:

```bash
buzz deploy ./dist
```

A successful redeployment completely replaces the previous file set. Files absent from the new build are removed. If Buzz cannot validate or publish the new deployment, the previous deployment remains available.

Read [Deploy Sites](../../guides/deploy-sites/) for archive exclusions and advanced deployment behavior. To keep a name stable across different working directories, read [Choose A Site Name](../../guides/choose-a-site-name/).
