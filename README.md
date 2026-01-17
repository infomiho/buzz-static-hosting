# Buzz

Self-hosted static site hosting. Deploy sites with a single command.

## Setup

### Server

Requires Docker. Copy the `server/` directory to your VPS.

```bash
cd server
cp .env.example .env
# Edit .env with your domain, token, and Cloudflare API token
docker compose up -d
```

DNS: Point `yourdomain.com` and `*.yourdomain.com` to your server IP.

### CLI

```bash
cd cli
npm install
npm run build
npm link
```

Configure:

```bash
buzz config server https://yourdomain.com
buzz config token your-secret-token
```

## Usage

Deploy a directory:

```bash
buzz deploy ./dist
```

First deploy creates a random subdomain and saves it to `CNAME`. Subsequent deploys to the same directory update the existing site.

Deploy to a specific subdomain:

```bash
buzz deploy ./dist my-site
```

Other commands:

```bash
buzz url      # Show URL for current directory
buzz list     # List all sites
buzz delete <subdomain>
```

## How it works

- Server receives ZIP uploads and extracts them to subdomain directories
- Caddy handles SSL certificates automatically (wildcard certs via Cloudflare DNS)
- Sites are served based on the Host header
- SQLite tracks site metadata
