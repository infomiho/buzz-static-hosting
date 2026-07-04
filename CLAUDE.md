# Buzz

Self-hosted static site hosting with CLI deployment.

## Project Structure

```
buzz/
├── server/                  # Python server (FastAPI)
│   ├── src/server/
│   │   ├── main.py          # Entry point
│   │   ├── app.py           # FastAPI app
│   │   ├── routes/          # API + dashboard routes
│   │   ├── templates/       # Jinja2 templates (Basecoat UI)
│   │   ├── static/          # Built CSS + JS assets
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── cookies.py       # Session cookie helpers
│   │   └── dependencies.py  # Auth dependencies
│   ├── pyproject.toml
│   ├── package.json         # Tailwind CSS + Basecoat build
│   ├── Dockerfile
│   └── docker-compose.yml
└── cli/                     # TypeScript CLI
    └── src/
        ├── cli.ts
        ├── lib.ts
        └── commands/
```

## Server

Python 3.12+ with FastAPI and uvicorn. Uses uv for dependency management.

Run locally:
```bash
cd server
uv sync
npm install && npm run css:build
uv run python -m server --dev
```

For CSS development with hot rebuild:
```bash
npm run css:watch
```

Test:
```bash
cd server
uv run pytest tests/ -v
```

Environment variables: `BUZZ_PORT`, `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `BUZZ_DATA_DIR`, `BUZZ_ANALYTICS_SECRET`, `BUZZ_GSC_CREDENTIALS`, `BUZZ_GSC_PROPERTY`

## CLI

TypeScript with Commander.js and Archiver.

Build:
```bash
cd cli
npm install
npm run build
```

Test:
```bash
cd cli
npm test
```

Link globally for development:
```bash
npm link
```

Commands: `deploy`, `list`, `delete`, `url`, `config`, `login`, `logout`, `whoami`, `tokens`

Config stored at `~/.buzz.config.json`. Per-project subdomain stored in `CNAME` file.

## Deployment

Docker Compose with Traefik v3 (wildcard SSL via Cloudflare DNS challenge).

Required env vars in `.env`: `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `CF_API_TOKEN`, `ACME_EMAIL`

Coolify production uses `docker-compose.coolify.yml`. Enable Raw Docker Compose Deployment, leave the app FQDN empty, and set `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` on the app.

Coolify proxy config must be saved through **Servers > Proxy**. Direct edits to `/data/coolify/proxy/docker-compose.yml` are not durable because Coolify stores proxy config in its DB and can rewrite that file during proxy actions or upgrades.

For wildcard certificates, Coolify's Traefik proxy should use Cloudflare DNS-01 and a single `wildcard-certs` router for `BUZZ_DOMAIN` and `*.BUZZ_DOMAIN`. Buzz app labels should set `tls=true` without `tls.certresolver`; otherwise Traefik can create duplicate ACME challenges for the same `_acme-challenge` record.

## Releasing

Automated via Release Please. Push commits to `main` using conventional format:

- `fix: description` → patch bump
- `feat: description` → minor bump
- `feat!: description` → major bump

Bot creates a release PR. Merging it triggers npm publish via OIDC trusted publishing (no token needed).

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `infomiho/buzz-static-hosting` using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

No triage labels are used for this repo. See `docs/agents/triage-labels.md`.

### Domain docs

No domain docs exist yet; skills should use `CLAUDE.md` as the current project context. See `docs/agents/domain.md`.
