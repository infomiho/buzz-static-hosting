---
title: Use Deployment Tokens
description: Create and revoke site-scoped credentials for automated deployments.
sidebar:
  order: 5
---

Use a deployment token when a script or continuous integration (CI) job needs to redeploy one existing site without an interactive GitHub session.

## Prerequisites

Sign in with `buzz login`, deploy the target site, and confirm that you own it. Only a session can create, list, or delete deployment tokens.

## Create A Deployment Token

Create a token for `my-site` and give it a name that identifies its use:

```bash
buzz tokens create my-site --name "GitHub Actions"
```

The CLI displays the token value once. Store it in the secret manager used by the deployment system. Do not commit it to the repository or place it in a project `.env` file.

## Deploy With The Token

Set `BUZZ_TOKEN` in the deployment environment, then include the assigned site name:

```bash
buzz deploy ./dist --subdomain my-site
```

A deployment token cannot deploy another site. It also cannot list or delete sites, inspect the current user, or manage deployment tokens.

## Revoke A Deployment Token

List your tokens to find the token ID:

```bash
buzz tokens list
```

Delete the token by its displayed ID:

```bash
buzz tokens delete TOKEN_ID
```

Revocation prevents future deployments with that token. It does not change the currently hosted files.

Continue with [Deploy From CI](../deploy-from-ci/) for a GitHub Actions workflow.
