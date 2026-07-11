---
title: Deploy Sites
description: Package, publish, and replace static site files with the Buzz CLI.
sidebar:
  order: 1
---

Deploy a static build directory or replace an existing site's files. Buzz publishes the contents of the directory, not the directory itself.

## Prerequisites

Install the CLI, configure a server, and sign in as described in [Install The CLI](../../getting-started/install-the-cli/). Build your site into `./dist` before deploying it.

## Deploy A Build

Run the deployment from your project directory:

```bash
buzz deploy ./dist --subdomain my-site
```

The CLI includes dotfiles, but excludes these paths from the archive:

- `.git`
- `node_modules`
- `.vscode`
- `.idea`
- `.DS_Store` files
- `.env` and `.env.*` files

Keep `index.html` at the root of `./dist` when it should serve at the root site URL.

## Replace A Site

Deploy the new build with the same site name:

```bash
buzz deploy ./dist --subdomain my-site
```

A successful redeployment replaces the complete previous file set. Files omitted from the new build are removed from the hosted site. If validation or publishing fails, Buzz leaves the previous deployment in place.

Only the owner can replace a site. A deployment token can replace only the site to which it is scoped.

## Verify The Deployment

Request a known page from the deployed site:

```bash
curl --fail --show-error https://my-site.buzz.example.com/
```

This confirms that the server returns hosted content. The remaining commands inspect local or ownership metadata.

Show the site URL stored for the current project:

```bash
buzz url
```

List the sites owned by the signed-in user:

```bash
buzz list
```

For naming behavior, read [Choose A Site Name](../choose-a-site-name/). For automation, read [Deploy From CI](../deploy-from-ci/).
