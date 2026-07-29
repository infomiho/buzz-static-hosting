---
title: Make A Site Private
description: Restrict a site to its owner and selected GitHub users.
sidebar:
  order: 7
---

A site is either public or private. A private site is visible to its owner and any GitHub users the owner adds. Protection covers the whole site: every path, every asset.

## Make A Site Private

Run this from a project containing its Buzz `CNAME` file:

```bash
buzz access private
```

To publish a new site without briefly exposing it, make it private as part of its first deployment:

```bash
buzz deploy ./dist --private
```

The site stays private across later deployments. A deployment token can update files but cannot change whether a site is private.

## Check Or Change Visibility

```bash
buzz access
buzz access public
```

`buzz access` prints the current state. `buzz access public` asks for confirmation first, since it exposes every file. Pass `-y` to skip the prompt.

In the dashboard, use the visibility button next to the site's address. The site list shows the same state in its **Visibility** column.

## Share A Private Site

Open the site in the dashboard, choose **Private**, then select **Manage access**. Enter an exact GitHub username and confirm the account. Buzz does not send invitations; share the site URL yourself.

Readers sign in with GitHub. They can view the shared site but cannot manage it or use the Buzz dashboard. Removal takes effect on their next request.

Making a site public takes effect immediately, ends every Access session, and clears the reader list. If you make the site private later, it starts owner-only.

## Why Whole Sites

Buzz has no per-path protection because a static site serves one file under several URLs: a page built to `admin.html` answers at both `/admin` and `/admin.html`, and one built to `admin/index.html` answers at `/admin`, `/admin/`, and `/admin/index.html`. A rule naming one of those leaves the others open. The same applies to what a page loads, since a protected page whose content lives in `/assets/app-a1b2c3.js` gains nothing if that asset is public.

## Caching Limitation

Buzz prevents responses from a private site from being cached, but making a site private cannot recall copies downloaded, indexed, handled by a service worker, or cached by another proxy while it was public. Treat anything a site served while public as already distributed.
