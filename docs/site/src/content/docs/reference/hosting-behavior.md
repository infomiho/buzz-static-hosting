---
title: Hosting Behavior
description: How Buzz maps site requests to deployed files and fallback pages.
---

Buzz serves each deployed site from its site hostname. This reference describes file lookup, fallback status codes, methods, and content types.

## Site Hostnames

With `BUZZ_DOMAIN=buzz.example.com`, the control host is `buzz.example.com` and a site named `my-site` is served from `my-site.buzz.example.com`.

Requests to a site hostname are isolated from control routes. For example, `/health` on a site hostname looks for that site's content instead of returning the Buzz server health response.

Site hostnames accept `GET` and `HEAD`. Other methods return `405 Method Not Allowed` with `Allow: GET, HEAD`.

## File Lookup Order

Buzz decodes the URL path, rejects paths that escape the site directory, and checks candidates in this order:

1. The exact requested file.
2. If the path ends in `/`, `index.html` inside that directory.
3. If the resulting path does not end in `.html`, the same path with `.html` appended.
4. If the resulting path does not end in `.html`, `index.html` below that path.
5. The site's root `200.html` fallback.
6. The site's root `404.html` page.
7. Buzz's plain-text `404 Not Found` response.

The first matching file is returned. Buzz does not redirect a clean URL to the underlying HTML file.

Common paths resolve as follows:

| Request | Candidate |
| --- | --- |
| `/` | `/index.html` |
| `/about` | `/about`, then `/about.html`, then `/about/index.html` |
| `/docs/` | `/docs/index.html` |
| `/assets/app.js` | `/assets/app.js` first |

Query parameters do not change file lookup. A request for `/assets/app.js?v=2` serves `/assets/app.js` when it exists.

## Single-Page Application Fallback

Add `200.html` at the root of a deployed site to handle client-side routes. Buzz serves it only after the requested file and clean-URL candidates do not exist.

The fallback response has status `200`, so a browser can load the application and let its router interpret the original URL. Because `200.html` is checked before `404.html`, a site with both files uses `200.html` for every otherwise unmatched path.

## Not Found Responses

If no requested file or `200.html` fallback matches, Buzz checks for `404.html` at the site root. A custom page is returned with status `404` and content type `text/html`.

If the site has no custom page, Buzz returns plain text with status `404`. A hostname without a deployed site also returns `404`.

## Content Types

Buzz chooses a response content type from the served file's extension:

| Extension | Content Type |
| --- | --- |
| `.html` | `text/html` |
| `.css` | `text/css` |
| `.js` | `application/javascript` |
| `.json` | `application/json` |
| `.png` | `image/png` |
| `.jpg`, `.jpeg` | `image/jpeg` |
| `.gif` | `image/gif` |
| `.svg` | `image/svg+xml` |
| `.ico` | `image/x-icon` |
| `.txt` | `text/plain` |
| `.xml` | `application/xml` |

Files with any other extension use `application/octet-stream`.
