# Buzz

Self-hosted static site hosting. Deploy sites with a single command.

## Usage

Deploy a directory:

```bash
buzz deploy ./dist
```

On first deploy, a random subdomain is assigned (e.g., `happy-cloud-1234`) and saved to a `CNAME` file in the directory. Subsequent deploys to the same directory update the existing site.

Deploy to a specific subdomain:

```bash
buzz deploy ./dist my-site
# Deploys to https://my-site.static.yourdomain.com
```

Other commands:

```bash
buzz url              # Print URL for current directory (reads CNAME)
buzz list             # List all deployed sites
buzz delete my-site   # Delete a site
```

## Requirements

- A VPS with Docker installed
- A domain with DNS managed by Cloudflare
- A Cloudflare API token

## Server Setup

### 1. DNS Records

Add two A records pointing to your server IP:

| Type | Name | Content |
|------|------|---------|
| A | static | your.server.ip |
| A | *.static | your.server.ip |

This gives you `static.yourdomain.com` as the main endpoint and `*.static.yourdomain.com` for deployed sites.

### 2. Cloudflare API Token

Wildcard SSL certificates require DNS validation. Caddy handles this automatically but needs a Cloudflare API token.

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Click "Create Token"
3. Use the "Edit zone DNS" template
4. Under "Zone Resources", select your domain
5. Create and copy the token

### 3. Deploy

Copy the `server/` directory to your VPS and configure:

```bash
cd server
cp .env.example .env
```

Edit `.env`:

```
BUZZ_DOMAIN=static.yourdomain.com
BUZZ_TOKEN=generate-a-random-secret-token
CF_API_TOKEN=your-cloudflare-api-token
ACME_EMAIL=your-email@example.com
```

- `BUZZ_DOMAIN` - your subdomain (matches DNS setup)
- `BUZZ_TOKEN` - secret token for API authentication (generate with `openssl rand -hex 32`)
- `CF_API_TOKEN` - Cloudflare API token from step 2
- `ACME_EMAIL` - email for Let's Encrypt certificate notifications

Start the server:

```bash
docker compose up -d
```

Caddy will automatically obtain SSL certificates on first request.

## CLI Setup

```bash
cd cli
npm install
npm run build
npm link
```

Configure the CLI with your server URL and token:

```bash
buzz config server https://static.yourdomain.com
buzz config token your-secret-token
```

Configuration is stored at `~/.buzz.config.json`.

## How it works

1. CLI zips the directory and uploads to the server
2. Server extracts files to a subdomain directory
3. Caddy routes requests based on Host header to the Python server
4. Python server serves static files with support for clean URLs (`/about` serves `/about.html`)
5. SQLite stores site metadata (name, size, creation date)
