# Step 1: Cookie Auth + Templated Landing Page

## Parent RFC

See `rfc.md` in project root.

## What to build

Replace the static `landing.html` with Jinja2 templates and add cookie-based session auth alongside the existing Bearer token auth. This is the foundation layer: after this step, the root domain serves a templated landing page, and all existing API endpoints (`/sites`, `/tokens`, `/auth/me`) accept authentication via an HTTP-only `buzz_session` cookie in addition to Bearer tokens.

## Acceptance criteria

- [ ] `jinja2>=3.1.0` added to `server/pyproject.toml` and `uv sync` works
- [ ] `server/src/server/cookies.py` exists with `set_session_cookie(response, token)` and `clear_session_cookie(response)` helpers (HttpOnly, Secure in prod, SameSite=Lax, Path=/, max_age=30 days)
- [ ] `get_identity()` in `dependencies.py` reads the `buzz_session` cookie when no `Authorization` header is present
- [ ] `server/src/server/templates/base.html` exists with shared layout (system-ui font, black/white/gray palette matching old landing.html style, nav block, content block, scripts block)
- [ ] `server/src/server/templates/login.html` extends `base.html` and contains the CLI setup instructions from the old `landing.html` (no login JS yet)
- [ ] `app.py` sets up `Jinja2Templates` and renders `login.html` at the root `/` route (when no subdomain and not authenticated)
- [ ] `server/src/server/landing.html` is deleted
- [ ] `GET /` returns HTML containing "Login with GitHub" (or similar) when unauthenticated
- [ ] `GET /sites` with a valid `buzz_session` cookie returns site data (proves cookie auth works for existing API routes)
- [ ] Tests pass

## Blocked by

None - can start immediately.

## Key files

- `server/pyproject.toml`
- `server/src/server/cookies.py` (new)
- `server/src/server/dependencies.py`
- `server/src/server/templates/base.html` (new)
- `server/src/server/templates/login.html` (new)
- `server/src/server/app.py`
- `server/src/server/landing.html` (delete)
