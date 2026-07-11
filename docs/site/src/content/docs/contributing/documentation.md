---
title: Documentation
description: Write, generate, and verify the public Buzz documentation.
---

Buzz publishes its public documentation from `docs/site/` with Astro Starlight. Update documentation in the same change as user-visible behavior.

## Prerequisites

Complete the repository setup in [Development](../development/). Documentation generation imports the CLI command tree and the server application, so install dependencies in `cli/`, `server/`, and `docs/site/` before building the site.

```bash
cd cli
npm ci

cd ../server
uv sync

cd ../docs/site
npm ci
```

## Choose The Right Page

- Put a first deployment path in **Getting Started**.
- Put one task with ordered steps in **Guides**.
- Put operator procedures in **Self-Hosting**.
- Put complete, scannable behavior in **Reference**.
- Put recognizable symptoms and checks in **Troubleshooting**.
- Put repository workflows in **Contributing**.

Follow the repository's [Documentation Writing Guide](https://github.com/infomiho/buzz-static-hosting/blob/main/docs/contributing/writing-guide.md). Keep one authoritative explanation for each behavior and link to it instead of copying it into several pages.

## Edit Hand-Written Pages

Hand-written pages live under `docs/site/src/content/docs/`. Add frontmatter with a concise `title` and `description`. The Starlight sidebar discovers pages from their section directories.

Use repository-relative links for source files and relative documentation links for other pages. Check links from the generated site because Buzz is published below the `/buzz-static-hosting/` GitHub Pages base path.

## Update Generated Reference

Do not edit these outputs directly:

- `docs/site/src/content/docs/reference/cli/`
- `docs/site/src/content/docs/reference/configuration.md`
- `docs/site/public/openapi.json`
- `server/.env.example`

Change their source instead:

- CLI pages come from Commander definitions in `cli/src/`.
- Configuration and `.env.example` come from `server/src/server/environment.py`.
- The OpenAPI schema comes from the FastAPI application and its request and response models.

Regenerate all outputs from the docs directory:

```bash
cd docs/site
npm run generate
```

Review generated diffs and commit them with the source change.

## Preview And Verify

Start the local documentation server:

```bash
cd docs/site
npm run dev
```

Before opening a pull request, run the type and content checks, then create the production build:

```bash
cd docs/site
npm run check
npm run build
```

Both commands regenerate reference files first. Review `git diff` afterward to catch stale generated output.
