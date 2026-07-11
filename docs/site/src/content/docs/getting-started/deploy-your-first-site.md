---
title: Deploy Your First Site
description: Publish a static build directory and open its Buzz site URL.
sidebar:
  order: 3
---

Deploy a static build directory to a named site and confirm that the site is available.

## Prerequisites

Complete [Install The CLI](../install-the-cli/). Run the following commands from a project whose `./dist` directory contains an `index.html` file.

## Publish The Build Directory

1. Deploy `./dist` with the site name `my-site`:

   ```bash
   buzz deploy ./dist --subdomain my-site
   ```

2. Confirm that the CLI prints the site URL:

   ```text
   Deployed to https://my-site.buzz.example.com
   ```

3. Open the URL in a browser.

After a successful deployment, Buzz writes `my-site` to a `CNAME` file in the directory where you ran the command. This file stores the Buzz site name. It is not a DNS CNAME record.

Future deployments from this project can reuse the stored name:

```bash
buzz deploy ./dist
```

Read [Deploy Sites](../../guides/deploy-sites/) for file selection, redeployment behavior, and verification commands.
