# Step 2: Web Login/Logout Flow

## Parent RFC

See `rfc.md` in project root.

## What to build

Implement the GitHub Device Flow login and logout for the browser. The user clicks "Login with GitHub", sees a user code, visits GitHub to authorize, and the browser polls until complete. On success, the server sets an HTTP-only session cookie. Logout clears the cookie and invalidates the session. These are dedicated web endpoints separate from the CLI's `/auth/device/*` (the CLI endpoints return raw tokens in JSON; the web endpoints set cookies instead).

## Acceptance criteria

- [ ] `POST /dashboard/login/start` starts a device flow and returns `{device_code, user_code, verification_uri, interval}`
- [ ] `POST /dashboard/login/poll` with `{device_code}` returns `{status: "pending"}` while waiting
- [ ] `POST /dashboard/login/poll` returns `{status: "complete"}` on success with a `Set-Cookie: buzz_session=...` header (token never exposed in JSON body)
- [ ] `POST /dashboard/logout` clears the `buzz_session` cookie, deletes the session from DB, and redirects to `/`
- [ ] `login.html` has a "Login with GitHub" button that triggers the device flow
- [ ] Login page shows the user code prominently and links to the GitHub verification URI
- [ ] Login page polls automatically and redirects to `/` on success
- [ ] `base.html` nav shows a logout button/link when the user is authenticated
- [ ] `routes/dashboard.py` is wired into the app via `routes/__init__.py` and `app.py`
- [ ] Tests cover: login start, poll success, poll pending, logout

## Blocked by

- Blocked by Step 1 (cookie auth infrastructure + templates)

## Key files

- `server/src/server/routes/dashboard.py` (new)
- `server/src/server/routes/__init__.py`
- `server/src/server/templates/login.html` (update with JS)
- `server/src/server/templates/base.html` (update nav)
- `server/src/server/app.py`
