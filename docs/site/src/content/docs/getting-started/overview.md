---
title: Overview
description: Learn how the Buzz CLI and server turn a directory into a hosted static site.
sidebar:
  order: 1
---

Buzz deploys a directory of static files to a Buzz server. Each site gets a site name and a URL such as `https://my-site.buzz.example.com`.

## Understand The Deployment Flow

The Buzz CLI compresses the files in your build directory and sends the archive to the server. The server extracts the archive and publishes its contents at the root of the site URL.

A deployment either creates a site or replaces all files in a site you own. Buzz preserves the current files if the new archive cannot be validated or published.

## Know The Main Terms

- A **Buzz server** runs the dashboard, API, and static site hosting.
- The **server URL** points the CLI to that server, such as `https://buzz.example.com`.
- A **site name** identifies one deployment and becomes the first part of its site URL.
- A **session** is created when you sign in with GitHub. It can manage every site you own.
- A **deployment token** is limited to one existing site and is intended for automation.
- A local `CNAME` file stores a Buzz site name for later CLI commands. It is not a DNS record.

## Prepare For Your First Deployment

You need:

- Access to a running Buzz server.
- A GitHub account.
- A directory of static files with an `index.html` file at its root.
- npm to install the Buzz CLI.

Continue with [Install The CLI](../install-the-cli/), then [Deploy Your First Site](../deploy-your-first-site/).
