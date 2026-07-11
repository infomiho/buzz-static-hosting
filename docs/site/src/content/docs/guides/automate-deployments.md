---
title: Automate Deployments
description: Deploy one Buzz site safely from GitHub Actions or the HTTP API.
sidebar:
  order: 5
---

Automate redeployment of an existing site with a site-scoped deployment token. This guide provides a GitHub Actions workflow and the equivalent direct HTTP request.

## Prerequisites

You need:

- A site deployed through the [Buzz CLI](../../getting-started/deploy-your-first-site/).
- An active session for the site's owner, verified with `buzz whoami`.
- A build that writes `./dist/index.html`.
- Permission to configure GitHub Actions secrets and variables for the repository.

## Choose A Credential

Use your session for interactive CLI work. A session can manage every site owned by your GitHub account.

Use a deployment token for continuous integration (CI) or another unattended process. It can deploy only to its assigned site and cannot list or delete sites, identify the current user, or manage tokens. Deployment tokens currently have no automatic expiry, so revoke them when they are no longer needed.

## Create A Deployment Token

1. Create a token for the existing site. Give it a name that identifies where it is stored:

   ```bash
   buzz tokens create my-site --name "GitHub production"
   ```

2. Store the displayed value immediately. Buzz displays the token only once. Do not commit it or put it in a project `.env` file.

3. Confirm that the token appears in your inventory:

   ```bash
   buzz tokens list
   ```

## Configure GitHub Actions

Store `BUZZ_TOKEN` as a GitHub environment secret when deployments need environment approvals, branch restrictions, or separately controlled production access. Create an environment named `production` under **Settings > Environments**, then add the secret there.

If those controls are not needed, add `BUZZ_TOKEN` under **Settings > Secrets and variables > Actions > Secrets > Repository secrets** and remove `environment: production` from the workflow.

Add these as repository variables under **Settings > Secrets and variables > Actions > Variables**, or add them to the `production` environment under **Settings > Environments**:

- `BUZZ_SERVER`: The operator-provided server URL, such as `https://buzz.example.com`.
- `BUZZ_SITE_URL`: The complete public site URL, such as `https://my-site.buzz.example.com`.

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy

on:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v6
        with:
          node-version: 22
          cache: npm
      - run: npm ci
      - run: npm run build
      - name: Verify build output
        run: test -f ./dist/index.html
      - run: npm install --global @infomiho/buzz-cli@0.7.0
      - name: Deploy site
        run: buzz deploy ./dist --subdomain my-site
        env:
          BUZZ_SERVER: ${{ vars.BUZZ_SERVER }}
          BUZZ_TOKEN: ${{ secrets.BUZZ_TOKEN }}
      - name: Verify deployed site
        run: curl --fail --show-error --retry 3 "$BUZZ_SITE_URL/"
        env:
          BUZZ_SITE_URL: ${{ vars.BUZZ_SITE_URL }}
```

This workflow pins the current CLI version from `cli/package.json` so a future release cannot change deployment behavior without a reviewed workflow update. Change the build commands if your project does not use npm, but keep the `./dist/index.html` assertion, explicit `--subdomain my-site` target, and HTTP verification.

Push a commit to `main`, inspect the **Deploy** workflow run, and open the verified site URL. The deployment step should print `Deployed to https://my-site.buzz.example.com`.

## Rotate Or Revoke A Token

Rotate a token without interrupting deployments:

1. Create a new token with a distinct name.
2. Replace `BUZZ_TOKEN` in GitHub with the new value.
3. Run the workflow and verify the site over HTTP.
4. Run `buzz tokens list` and confirm that the new token's **LAST USED** value reflects the workflow run.
5. Revoke the old token by its displayed ID:

   ```bash
   buzz tokens delete TOKEN_ID
   ```

Revocation blocks future deployments with that token but does not change the hosted files. If a CI deployment starts failing after rotation, confirm that the job can access the selected environment, that `BUZZ_TOKEN` is present in that environment or repository, and that the explicit site name matches the token's site. Create another token with the owning session if the value was lost because Buzz cannot display it again.

## Deploy Through The HTTP API

Use the HTTP API directly when your automation cannot install Node.js or the Buzz CLI. The automation must create a ZIP archive whose root contains the site's files. The deployment token requires an `X-Subdomain` header matching its assigned site.

Set clear environment values, create the archive from the contents of `./dist`, and upload it:

```bash
export BUZZ_SERVER="https://buzz.example.com"
export BUZZ_SITE="my-site"
export BUZZ_TOKEN="buzz_deploy_replace_with_secret_value"
export SITE_ARCHIVE="/tmp/buzz-site.zip"

test -f ./dist/index.html
(cd ./dist && zip -q -r "$SITE_ARCHIVE" .)
curl --fail-with-body --show-error \
  --request POST \
  --header "Authorization: Bearer $BUZZ_TOKEN" \
  --header "X-Subdomain: $BUZZ_SITE" \
  --form "file=@$SITE_ARCHIVE;type=application/zip" \
  "$BUZZ_SERVER/deploy"
```

Do not set the multipart `Content-Type` header manually. `curl --form` adds the required boundary. Confirm that the JSON response contains the expected `url`, then verify it:

```bash
curl --fail --show-error https://my-site.buzz.example.com/
```

For rejected deployments, follow [Troubleshoot Deployment](../../troubleshooting/deployment/). The generated [HTTP API reference](../../reference/http-api/) documents the endpoint schema.
