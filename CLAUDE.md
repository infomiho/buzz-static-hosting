# Buzz

Self-hosted static site hosting with CLI deployment.

## Project Structure

- `server/` - Python 3.12+ FastAPI server, dependencies managed with uv. Jinja2 templates (`src/server/templates/`) use the Achroma visual system; `src/server/static/` holds built CSS/JS assets (Tailwind, built via the server's `package.json`).
- `cli/` - TypeScript CLI (Commander.js + Archiver), published to npm as `buzz-cli`.
- `docs/site/` - Starlight docs site. `reference/configuration.md`, `server/.env.example`, and `public/openapi.json` are generated from `server/src/server/environment.py` via `npm run generate:server` in `docs/site`; the Docs CI job fails if they are stale.
- `docs/agents/` - instructions for agent skills.

## Server

Run locally:
```bash
cd server
uv sync
npm install && npm run css:build
uv run python -m server --dev
```

`npm run css:watch` rebuilds CSS on change.

Test:
```bash
cd server
uv run pytest tests/ -v
```

Environment variables are defined in `server/src/server/environment.py`. It is the single source of truth; the config reference, `.env.example`, and OpenAPI schema are generated from it.

## CLI

Build and test:
```bash
cd cli
npm install
npm run build
npm test
```

`npm link` installs it globally for development.

Commands: `deploy`, `list`, `delete`, `url`, `config`, `login`, `logout`, `whoami`, `tokens`, `domains`. Config lives at `~/.buzz.config.json`; the per-project subdomain in a `CNAME` file. Custom domains do not change the canonical deployment identity or local `CNAME`.

## Deployment

Docker Compose with Traefik v3 (wildcard SSL via Cloudflare DNS challenge). Required `.env` vars: `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `CF_API_TOKEN`, `ACME_EMAIL`.

Coolify production uses `docker-compose.coolify.yml`. Enable Raw Docker Compose Deployment, leave the app FQDN empty, and set `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET`. Pushes to `main` auto-deploy. Environment changes take effect on restart or redeployment.

Coolify proxy config must be saved through **Servers > Proxy**. Direct edits to `/data/coolify/proxy/docker-compose.yml` are not durable; Coolify stores proxy config in its DB and rewrites the file during proxy actions or upgrades.

For wildcard certificates, Coolify's Traefik proxy should use Cloudflare DNS-01 and a single `wildcard-certs` router for `BUZZ_DOMAIN` and `*.BUZZ_DOMAIN`. Buzz app labels should set `tls=true` without `tls.certresolver`; otherwise Traefik creates duplicate ACME challenges for the same `_acme-challenge` record.

Custom domains are an optional operator capability and default to disabled through `BUZZ_CUSTOM_DOMAINS_ENABLED`. Disabled or unhealthy custom-domain infrastructure must not affect canonical Buzz hosting. Once custom-domain routers exist, disablement requires acknowledged router withdrawal before removing the Traefik provider integration.

Cloudflare claims require explicit mode and `BUZZ_CLOUDFLARE_DIAGNOSTICS_ENABLED`; serving additionally requires `BUZZ_CLOUDFLARE_ACTIVATION_ENABLED`. Persistent ownership, Cloudflare ranges, edge challenge, and origin identity failures fail closed immediately. Edge and origin transport failures receive three attempts. The bundled range snapshot fails closed after 180 days.

Staging and production custom-domain ACME resolvers need separate storage files. A valid staging certificate loaded in Traefik's global TLS store can suppress production issuance for the same hostname; remove only that staging certificate entry before the production cutover.

## Releasing

Release Please versions only the CLI (`buzz-cli` npm package); server changes ship via the Coolify auto-deploy and produce no release PR. Use conventional commits on `main`: `fix:` patch, `feat:` minor, `feat!:` major. Merging the bot's release PR publishes to npm via OIDC trusted publishing.

## Agent skills

- Issue tracker: GitHub Issues on `infomiho/buzz-static-hosting` via `gh`, see `docs/agents/issue-tracker.md`.
- No triage labels are used, see `docs/agents/triage-labels.md`.
- No domain docs exist; use this file as project context, see `docs/agents/domain.md`.
