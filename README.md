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
- A GitHub OAuth app for dashboard login

## Server Setup

### 1. DNS Records

Add two A records pointing to your server IP:

| Type | Name | Content |
|------|------|---------|
| A | static | your.server.ip |
| A | *.static | your.server.ip |

This gives you `static.yourdomain.com` as the main endpoint and `*.static.yourdomain.com` for deployed sites.

### 2. Cloudflare API Token

Wildcard SSL certificates require DNS validation. Traefik handles this with Cloudflare DNS-01 and needs a Cloudflare API token.

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/profile/api-tokens)
2. Click "Create Token"
3. Use the "Edit zone DNS" template
4. Under "Zone Resources", select your domain
5. Create and copy the token

### 3. GitHub OAuth

Create a GitHub OAuth app and set the callback URL to:

```text
https://static.yourdomain.com/auth/github/callback
```

### 4. Deploy

Copy the `server/` directory to your VPS and configure:

```bash
cd server
cp .env.example .env
```

Edit `.env`:

```
BUZZ_DOMAIN=static.yourdomain.com
GITHUB_CLIENT_ID=your-github-client-id
GITHUB_CLIENT_SECRET=your-github-client-secret
CF_API_TOKEN=your-cloudflare-api-token
ACME_EMAIL=your-email@example.com
```

- `BUZZ_DOMAIN` - your subdomain (matches DNS setup)
- `GITHUB_CLIENT_ID` - GitHub OAuth app client ID
- `GITHUB_CLIENT_SECRET` - GitHub OAuth app client secret
- `CF_API_TOKEN` - Cloudflare API token from step 2
- `ACME_EMAIL` - email for Let's Encrypt certificate notifications

Start the server:

```bash
docker compose up -d
```

Traefik will automatically obtain the wildcard SSL certificate on first request.

### 5. Google search terms (optional)

Google strips search keywords from referrers; the Search Console API is the only way to get them. With this set up, Buzz shows top Google search terms per site on the analytics page.

1. In [Google Search Console](https://search.google.com/search-console), add a **Domain** property for your Buzz domain (e.g. `static.yourdomain.com`). It covers all site subdomains, including future ones.
2. In the verification dialog, switch **Instructions for** to **Any DNS provider**, copy the TXT value, add it as a TXT record for your Buzz subdomain (name `static` in this example), then press **Verify**. Don't use the provider-specific **Start verification** flow; it adds the record at the zone apex and fails for subdomain properties.
3. In [Google Cloud Console](https://console.cloud.google.com), create a project, enable the **Google Search Console API**, create a **service account** (no roles), and download a JSON key for it.
4. In Search Console, under the property's **Settings > Users and permissions**, add the service account email with **Full** permission.
5. Set `BUZZ_GSC_CREDENTIALS` to the JSON key content on one line (or a path to the key file) and restart the server.

Buzz queries the `sc-domain:<BUZZ_DOMAIN>` property; set `BUZZ_GSC_PROPERTY` to override. Data lags ~2 days and omits rare queries.

### Coolify

Use `docker-compose.coolify.yml` for Coolify deployments.

- Enable **Raw Docker Compose Deployment**.
- Leave the app **FQDN/Domains** field empty. Routing is handled by Traefik labels.
- Set `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` on the app. For Google search terms, also set `BUZZ_GSC_CREDENTIALS` to the JSON key content.
- Configure Coolify's proxy through **Servers > Proxy**, not only by editing `/data/coolify/proxy/docker-compose.yml` over SSH.
- The proxy must use Cloudflare DNS-01 and a single wildcard certificate for `BUZZ_DOMAIN` and `*.BUZZ_DOMAIN`.
- Buzz app labels should use `tls=true` without `tls.certresolver`; the proxy-level wildcard cert router owns ACME issuance.

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
3. Traefik routes requests based on Host header to the Python server
4. Python server serves static files with support for clean URLs (`/about` serves `/about.html`)
5. SQLite stores site metadata (name, size, creation date)

## Features

**Clean URLs** - `/about` serves `about.html` or `about/index.html`

**Custom 404** - Add a `404.html` file to show a custom error page for missing routes

**SPA Support** - Add a `200.html` file to enable client-side routing. When a route doesn't match any file, `200.html` is served with a 200 status code, allowing your SPA router to handle the route.

```bash
# For SPAs, copy your index.html to 200.html before deploying
cp dist/index.html dist/200.html
buzz deploy ./dist
```

## Releasing

The CLI is published to npm automatically using [Release Please](https://github.com/googleapis/release-please).

When you push commits to `main`, use conventional commit format:

```bash
git commit -m "fix: handle empty directory error"    # patch (0.1.0 → 0.1.1)
git commit -m "feat: add verbose flag"               # minor (0.1.0 → 0.2.0)
git commit -m "feat!: change config format"          # major (0.1.0 → 1.0.0)
```

Release Please will open a PR with version bump and changelog. Merge it to publish to npm.
