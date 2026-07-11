---
title: Serve Clean URLs
description: Map extensionless paths and directory paths to static HTML files.
sidebar:
  order: 3
---

Buzz can serve HTML pages without exposing `.html` or `index.html` in their URLs. Arrange the output files to match the paths you want visitors to use.

## Prerequisites

Prepare a static build directory and follow [Deploy Sites](../deploy-sites/) to publish it.

## Choose A File Layout

To serve `https://my-site.buzz.example.com/about`, include either of these files:

```text
dist/about.html
dist/about/index.html
```

To serve `https://my-site.buzz.example.com/docs/`, include:

```text
dist/docs/index.html
```

Buzz checks an extensionless path in this order:

1. An exact file match.
2. The path with `.html` appended.
3. `index.html` inside the path.

A path ending in `/` maps directly to `index.html` inside that directory.

An extensionless file shadows the corresponding HTML file. For example, `dist/about` is served before `dist/about.html` and uses `application/octet-stream`, so do not include both forms for an HTML route.

## Add A Not Found Page

Place `404.html` at the root of the build directory to return custom content with HTTP status `404` when no file matches:

```text
dist/404.html
```

A site containing `200.html` uses that file as a fallback before Buzz checks for `404.html`. Use `200.html` only when the site needs client-side routing. Read [Deploy A Single-Page App](../deploy-a-single-page-app/) for that behavior.
