---
title: Development
description: Run the Buzz server and CLI locally, then verify changes.
---

Use this setup to change the server, CLI, or dashboard in the Buzz repository.

## Prerequisites

Install:

- Git
- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 or later with npm

Clone the repository, then run the commands in the sections for the components you are changing.

## Run The Server

Install the Python and frontend dependencies, build the dashboard CSS, and start the server in development mode:

```bash
cd server
uv sync
npm ci
npm run css:build
uv run python -m server --dev --reload
```

Open `http://localhost:8080`. Development mode bypasses GitHub authentication and serves deployed sites at `http://<site-name>.localhost:8080`.

Run the CSS watcher in another terminal while changing templates or styles:

```bash
cd server
npm run css:watch
```

## Run The CLI

Install dependencies, build the executable, and link it into your npm global binaries:

```bash
cd cli
npm ci
npm run build
npm link
```

Create a small site outside the CLI build directory:

```bash
mkdir -p /tmp/buzz-site
printf '<h1>Buzz development site</h1>\n' > /tmp/buzz-site/index.html
buzz --server http://localhost:8080 --token dev deploy /tmp/buzz-site --subdomain my-site
```

The explicit server and token options avoid saved CLI configuration. Development mode bypasses server authentication, but the CLI still requires a non-empty token before deployment.

Use `npm run dev` in `cli/` to rebuild the CLI when source files change.

## Run Tests

Run the server tests:

```bash
cd server
uv run pytest tests/ -v
```

Run the CLI tests and production build:

```bash
cd cli
npm test
npm run build
```

If your change affects public behavior, also update and build the [documentation](../documentation/).

## Prepare A Change

- Keep server behavior and its tests in the same change.
- Keep CLI command definitions as the source of truth for command syntax.
- Add user-visible behavior to the appropriate guide or reference page.
- Use a Conventional Commit message because it controls [CLI releases](../releases/).
