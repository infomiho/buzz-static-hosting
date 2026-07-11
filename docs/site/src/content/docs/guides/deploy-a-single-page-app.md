---
title: Deploy A Single-Page App
description: Configure Buzz to return an application shell for client-side routes.
sidebar:
  order: 4
---

Deploy a single-page application (SPA) so direct requests to client-side routes load the application shell.

## Prerequisites

Build the application into `./dist`. Configure the application to use URL paths and asset URLs that work from the site root.

## Add The Application Fallback

1. Keep the main application shell at `dist/index.html`.

2. Copy the same shell to `dist/200.html`. For example, on macOS or Linux:

   ```bash
   cp ./dist/index.html ./dist/200.html
   ```

3. Deploy the build:

   ```bash
   buzz deploy ./dist --subdomain my-site
   ```

4. Open a client-side route directly, such as `https://my-site.buzz.example.com/account`.

Buzz first checks for an exact file, an `.html` file, or a directory `index.html`. If none exists, it returns `200.html` with HTTP status `200`. The application's router then handles the requested path.

The fallback also applies to missing asset paths. A request for an absent script or image can receive the HTML in `200.html` with status `200`. Confirm that asset URLs point to files included in the build. While `200.html` is present, unmatched paths do not reach a custom `404.html` page.
