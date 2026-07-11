---
title: Troubleshoot Deployment
description: Diagnose rejected, failed, or incomplete site deployments.
sidebar:
  order: 1
---

Use the response status and message to identify why a deployment failed. Check authentication and the archive before changing server limits.

## The Server Returns 401

The session or deployment token is missing, expired, revoked, or for another Buzz server.

1. Confirm the CLI's server URL points to `https://buzz.example.com`.
2. Run `buzz whoami` when using a session.
3. Run `buzz login` again if the session is invalid.
4. For automation, confirm the deployment token secret is present and hasn't been deleted with its site.

## The Server Returns 403

Read the response message:

- `Deploy token is scoped to site ...` means the deployment token and requested site name differ. Deploy to the token's assigned site or create a token for the intended site.
- `Site ... is owned by another user` means another Buzz user owns that site name. Choose another site name or sign in as the owner.
- `Deploy tokens cannot perform this operation` means the command requires a user session rather than a deployment token.

Do not bypass ownership by editing `data.db`. Site files and related records must remain consistent.

## The Server Returns 413

The upload exceeded one of Buzz's configured limits. The response identifies the rejected condition:

- `Request body exceeds the configured deployment limit`: The complete multipart request exceeded the archive limit plus its 1 MiB allowance.
- `ZIP exceeds the ... compressed upload limit`: The ZIP file is too large.
- `Site exceeds the ... deployed size limit`: The extracted files are too large.
- `Site archive contains more than ... entries`: Files, explicit directories, and implicit directories exceed the entry limit.

Remove unnecessary build output or change the relevant setting in [Configure Deployment Limits](../../self-hosting/configure-deployment-limits/). A reverse proxy can return its own `413` before Buzz; compare the response body and inspect proxy logs if Buzz's message is absent.

## The Server Returns 400

Buzz rejects malformed or unsafe archives with messages such as:

- `Invalid ZIP file`
- `ZIP entry path is too long`
- `ZIP contains path traversal entry`
- `ZIP contains encrypted entry`
- `ZIP contains symbolic link entry`
- `ZIP contains duplicate entries`
- `ZIP contains conflicting entries`

Create a new ZIP from the final build directory rather than modifying the rejected archive. Buzz accepts regular files and directories, not symbolic links or encrypted ZIP entries.

## A Redeployment Fails But The Old Site Still Loads

Buzz stages and validates a deployment before replacing the published site. A rejected archive should leave the previous files and metadata in place.

1. Confirm the old site URL still serves the expected version.
2. Inspect the server logs for the deployment error.
3. Correct the archive or limit and deploy again.

If startup fails with an unresolved deployment operation, do not delete files under `/data/sites/.operations` without understanding the matching site and database state. Preserve the complete `/data` volume and investigate from a copy. See [Troubleshoot Self-Hosting](../self-hosting/).

## The Deployment Succeeds But The Site Is Wrong

1. Check the site URL printed by `buzz deploy` and confirm it names the intended site.
2. Inspect `CNAME` in the current working directory and in the deployment directory. The current working directory takes precedence.
3. Pass `--subdomain my-site` when you need to override either file.
4. Confirm the deployed directory contains `index.html` at its root.
5. Check that the CLI uploaded the build output, such as `./dist`, rather than the source directory.
6. Redeploy after removing stale local build output.

A redeployment replaces the complete site. Files omitted from the new deployment are removed.
