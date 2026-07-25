---
title: Protect Sites With Buzz Access
description: Restrict a whole site or matching paths to the site owner.
sidebar:
  order: 7
---

Buzz Access uses the normal Buzz login to let only the site owner view protected pages.

## Protect A Site

Run this command from a project containing its Buzz `CNAME` file:

```bash
buzz access enable
```

To publish a new site without briefly exposing it, enable Access as part of its first deployment:

```bash
buzz deploy ./dist --access
```

Protection remains enabled across later deployments. A deployment token can update files but cannot change Buzz Access.

## Protect Matching Paths

Pass one or more path patterns:

```bash
buzz access enable \
  --include '/admin/**' \
  --include '/reports/*'
```

For the first deployment:

```bash
buzz deploy ./dist --access \
  --include '/admin/**' \
  --include '/reports/*'
```

Patterns use these rules:

- Literal path segments match exactly.
- `*` matches exactly one path segment.
- `**` matches zero or more path segments.
- Wildcards must occupy a complete segment.
- Query strings do not affect matching.

For example, `/admin/**` protects `/admin` and `/admin/users`, but not `/administrator`.

Path patterns match requested URLs, not build folders. Files loaded from public paths remain public. Protect the entire site when all of its content must require login.

## Inspect Or Disable Access

```bash
buzz access status
buzz access disable
```

You can manage the same settings from the site's **Buzz Access** card in the dashboard.

Disabling Access makes the affected pages public immediately. Changing or disabling the policy invalidates existing Access sessions.

## Caching Limitation

Buzz prevents protected responses from being cached. Enabling Access cannot remove copies that were downloaded, indexed, handled by a service worker, or cached by another proxy before protection was enabled.
