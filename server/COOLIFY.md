# Coolify Deployment

## 1. Create App

In Coolify, create a Docker Compose app pointing at `docker-compose.coolify.yml`.

Enable **Raw Docker Compose Deployment** in the app settings. This is required because Coolify's YAML parser wraps labels in single quotes, which prevents `${BUZZ_DOMAIN}` substitution in Traefik labels ([coolify#5351](https://github.com/coollabsio/coolify/issues/5351)).

Leave the **FQDN/Domains** field empty — routing is handled by labels in the compose file.

Set environment variables: `BUZZ_DOMAIN`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`.

## 2. Configure Wildcard SSL on Coolify's Traefik

Go to **Servers > Proxy** and edit the Traefik compose config. You need three changes:

### Add environment variable

```yaml
environment:
  - CF_DNS_API_TOKEN=<your-cloudflare-api-token>
```

### Switch from HTTP challenge to DNS challenge

Replace:

```
--certificatesresolvers.letsencrypt.acme.httpchallenge=true
--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=http
```

With:

```
--certificatesresolvers.letsencrypt.acme.dnschallenge=true
--certificatesresolvers.letsencrypt.acme.dnschallenge.provider=cloudflare
--certificatesresolvers.letsencrypt.acme.dnschallenge.resolvers=1.1.1.1:53,8.8.8.8:53
```

### Add wildcard certificate labels

Add these to the Traefik service labels:

```
traefik.http.routers.wildcard-certs.tls.certresolver=letsencrypt
traefik.http.routers.wildcard-certs.tls.domains[0].main=yourdomain
traefik.http.routers.wildcard-certs.tls.domains[0].sans=*.yourdomain
```

Restart the proxy after saving.

## 3. Cloudflare DNS

Create two DNS records (DNS-only, no orange cloud proxy):

- `A` record: `yourdomain` → server IP
- `A` record: `*.yourdomain` → server IP