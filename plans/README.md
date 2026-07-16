# Custom Domain Plans

These plans implement custom domains as an optional Buzz capability.

## Operator Opt-In

- `BUZZ_CUSTOM_DOMAINS_ENABLED` defaults to `false`.
- Disabled installations do not start the Traefik control listener, expose domain-management actions, reconcile domains, emit routers, or request custom-domain certificates.
- Enabling the flag without complete infrastructure keeps custom domains unavailable and reports configuration diagnostics. It does not fail `/health`, canonical Buzz URLs, deployment, or the dashboard.
- Disabling an installation with active domains requires acknowledged router withdrawal before the control provider is removed. The flag must not strand Traefik's last valid snapshot.
- Every custom-domain plan and acceptance test must preserve canonical hosting when the capability is disabled or unhealthy.
