# Coolify Deployment

## 1. Create App

In Coolify, create a Docker Compose app pointing at `docker-compose.coolify.yml`.

Set environment variables: `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`.

## 2. Configure Wildcard SSL

In **Servers > Proxy** (Traefik config):

1. Add `CF_DNS_API_TOKEN` environment variable with your Cloudflare API token
2. Switch certificate resolver from HTTP challenge to DNS challenge (provider: `cloudflare`)
3. Add wildcard certificate domain labels on the Traefik service for `*.yourdomain`

## 3. Cloudflare DNS

Create two DNS records (DNS-only, no orange cloud proxy):

- `A` record: `yourdomain` → server IP
- `A` record: `*.yourdomain` → server IP
