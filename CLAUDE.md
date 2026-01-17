# Buzz

Self-hosted static site hosting with CLI deployment.

## Project Structure

```
buzz/
├── server/          # Python HTTP server
│   ├── server.py    # Main server (no dependencies)
│   ├── Dockerfile
│   ├── Dockerfile.caddy
│   └── docker-compose.yml
└── cli/             # TypeScript CLI
    └── src/cli.ts
```

## Server

Python 3.10+ with no external dependencies. Uses stdlib only (http.server, sqlite3, zipfile).

Run locally:
```bash
cd server
python server.py --port 8080 --domain localhost
```

Environment variables: `BUZZ_PORT`, `BUZZ_DOMAIN`, `BUZZ_TOKEN`, `BUZZ_DATA_DIR`

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

Commands: `deploy`, `list`, `delete`, `url`, `config`

Config stored at `~/.buzz.config.json`. Per-project subdomain stored in `CNAME` file.

## Deployment

Docker Compose with Caddy (wildcard SSL via Cloudflare DNS challenge).

Required env vars in `.env`: `BUZZ_DOMAIN`, `BUZZ_TOKEN`, `CF_API_TOKEN`, `ACME_EMAIL`
