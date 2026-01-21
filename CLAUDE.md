# Buzz

Self-hosted static site hosting with CLI deployment.

## Project Structure

```
buzz/
├── server/                  # Python server (FastAPI)
│   ├── src/server/
│   │   ├── main.py          # Entry point
│   │   ├── app.py           # FastAPI app
│   │   ├── routes/          # API routes
│   │   ├── config.py
│   │   ├── db.py
│   │   └── dependencies.py  # Auth dependencies
│   ├── pyproject.toml
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
uv run python -m server --dev
```

Environment variables: `BUZZ_PORT`, `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `BUZZ_DATA_DIR`

## CLI

TypeScript with Commander.js and Archiver.

Build:
```bash
cd cli
npm install
npm run build
```

Link globally for development:
```bash
npm link
```

Commands: `deploy`, `list`, `delete`, `url`, `config`, `login`, `logout`, `whoami`, `tokens`

Config stored at `~/.buzz.config.json`. Per-project subdomain stored in `CNAME` file.

## Deployment

Docker Compose with Caddy (wildcard SSL via Cloudflare DNS challenge).

Required env vars in `.env`: `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `CF_API_TOKEN`, `ACME_EMAIL`

## Releasing

Automated via Release Please. Push commits to `main` using conventional format:

- `fix: description` → patch bump
- `feat: description` → minor bump
- `feat!: description` → major bump

Bot creates a release PR. Merging it triggers npm publish via OIDC trusted publishing (no token needed).
