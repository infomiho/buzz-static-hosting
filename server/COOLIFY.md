# Coolify Deployment

Follow the public [Coolify deployment guide](https://infomiho.github.io/buzz-static-hosting/self-hosting/coolify/) to configure the app, DNS, TLS, and persistent data.

## Repository Deployment

The production Coolify app connected to this repository deploys every push to `main`. It builds the `server` service from `docker-compose.coolify.yml` and restarts the container. Merging a pull request does not require a separate manual deployment.

The `.github/workflows/release-please.yml` workflow releases the CLI package only. Coolify handles the server rollout independently.
