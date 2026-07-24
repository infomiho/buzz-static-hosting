---
title: Deploy Your First Site
description: Get a Buzz server, sign the CLI in, and publish a build directory.
sidebar:
  order: 1
---

Buzz is two parts: a server that hosts your sites, and a CLI that uploads directories to it. This page gets you from nothing to a live site URL.

## Get A Buzz Server

There's no hosted Buzz to sign up for, so a server has to exist first. Two ways to get one:

- Run your own. Follow [Self-Hosting Overview](../../self-hosting/overview/), then come back with your Buzz domain. You do this once.
- Use someone else's. Ask whoever operates it for the server URL, such as `https://buzz.example.com`.

The rest of this page takes a couple of minutes.

## Install The CLI

You need Node.js 22 or later, npm, and a GitHub account.

```bash
npm install --global @infomiho/buzz-cli
buzz --version
```

## Sign In

Point the CLI at your server, then sign in:

```bash
buzz config server https://buzz.example.com
buzz login
```

`buzz login` opens your browser and prints a code. Enter the code, approve access, and the CLI saves the session to `~/.buzz.config.json`. Confirm it worked:

```bash
buzz whoami
```

## Deploy

Point `buzz deploy` at the directory that contains `index.html`:

```bash
buzz deploy ./dist
```

The CLI zips the directory, uploads it, and prints the site URL:

```text
Deployed to https://calm-hub-4821.buzz.example.com
```

Buzz picked that site name because you didn't supply one. To choose it yourself, use lowercase letters, numbers, and hyphens:

```bash
buzz deploy ./dist --subdomain my-site
```

Site names are unique across a Buzz server. If someone else already owns the name, pick another. If you own it under a different GitHub account, run `buzz logout`, sign in with that account, and deploy again.

## Redeploy

A successful deployment writes the site name to a `CNAME` file in the directory you ran the command from. It's not a DNS CNAME record; it tells the CLI which site this project belongs to. Rebuild and run the same command to publish again:

```bash
buzz deploy ./dist
```

Each redeployment replaces the entire file set, so files missing from the new build disappear from the site. If Buzz can't validate or publish the new upload, the previous deployment keeps serving.

## Next Steps

- [Deploy Sites](../../guides/deploy-sites/) covers archive exclusions and the rest of the deployment behavior.
- [Choose A Site Name](../../guides/choose-a-site-name/) keeps one name stable across machines and directories.
- [Automate Deployments](../../guides/automate-deployments/) deploys from CI with a deployment token.
