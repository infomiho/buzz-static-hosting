---
title: Configure Deployment Limits
description: Bound the compressed archive, extracted site, entry count, and path length.
sidebar:
  order: 7
---

Set per-deployment limits to reject archives that require more upload bandwidth, disk space, files, or path length than you intend to accept. These limits don't provide total storage quotas or rate limiting.

## Before You Start

You need access to the Buzz server's environment variables and a deployment you can use for verification. Changing a value requires a server restart.

## Choose The Limits

Choose values based on available upload bandwidth, extraction space, filesystem capacity, and the sites you intend to host:

- `BUZZ_MAX_ARCHIVE_BYTES` limits compressed upload size and bandwidth per deployment.
- `BUZZ_MAX_SITE_BYTES` limits extracted bytes written for one site.
- `BUZZ_MAX_SITE_FILES` limits archive entries and filesystem work, including implicit directories.
- `BUZZ_MAX_ARCHIVE_PATH_BYTES` limits the UTF-8 byte length of each archive path.

Use the generated [Configuration reference](../../reference/configuration/) for the exact defaults and authoritative environment variable list.

Each individual path component is also limited to 255 UTF-8 bytes. That limit isn't configurable.

The HTTP request-body limit is the configured compressed archive limit plus 1 MiB for multipart form overhead. A reverse proxy can impose a lower request limit before Buzz receives the upload.

## Apply The Limits

1. Set non-negative integer values in the deployment environment. For example:

   ```text
   BUZZ_MAX_ARCHIVE_BYTES=104857600
   BUZZ_MAX_SITE_BYTES=209715200
   BUZZ_MAX_SITE_FILES=5000
   BUZZ_MAX_ARCHIVE_PATH_BYTES=512
   ```

2. Restart or redeploy the server.
3. Deploy a known site that is below every configured limit.
4. Attempt a deployment that exceeds the limit you changed and confirm that Buzz returns `413` for size or entry-count limits, or `400` for an overlong path.

Reducing a limit doesn't remove or resize existing sites. The new values apply when a site is deployed or redeployed.

## Recover From A Bad Value

If the server fails during startup after a limit change, inspect its logs for an invalid integer, restore the previous value, and restart it. Buzz doesn't validate that configured integers are positive, so use positive values unless you intentionally want all non-empty deployments rejected.

See [Troubleshoot Deployment](../../troubleshooting/deployment/) for rejection messages.
