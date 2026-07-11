---
title: Deploy From CI
description: Redeploy a Buzz site from a GitHub Actions workflow.
sidebar:
  order: 6
---

Deploy a built site from continuous integration (CI) by giving the job a site-scoped deployment token.

## Prerequisites

Create the site and a deployment token by following [Use Deployment Tokens](../use-deployment-tokens/). The workflow must build static files into `./dist`.

## Configure GitHub Actions

1. Add an Actions repository secret named `BUZZ_TOKEN` containing the deployment token.

2. Add an Actions repository variable named `BUZZ_SERVER` with the server URL `https://buzz.example.com`.

3. Create `.github/workflows/deploy.yml`:

   ```yaml
   name: Deploy

   on:
     push:
       branches: [main]

   jobs:
     deploy:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v7
         - uses: actions/setup-node@v6
           with:
             node-version: 24
             cache: npm
         - run: npm ci
         - run: npm run build
         - run: npm install --global @infomiho/buzz-cli
         - run: buzz deploy ./dist --subdomain my-site
           env:
             BUZZ_SERVER: ${{ vars.BUZZ_SERVER }}
             BUZZ_TOKEN: ${{ secrets.BUZZ_TOKEN }}
   ```

This example assumes an npm project whose build script writes `./dist`. Change the setup and build steps to match the project, but keep the Buzz server, deployment token, build directory, and site name available to the deployment command.

## Verify The Workflow

Push a commit to `main`, then inspect the **Deploy** workflow run. A successful deployment step prints:

```text
Deployed to https://my-site.buzz.example.com
```

The deployment token is valid only for `my-site`, so the `--subdomain my-site` option must match its assigned site.
