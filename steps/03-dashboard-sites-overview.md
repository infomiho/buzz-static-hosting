# Step 3: Dashboard - Sites Overview

## Parent RFC

See `rfc.md` in project root.

## What to build

Create the authenticated dashboard page that shows the user's deployed sites. When a user visits the root domain with a valid session cookie, they see the dashboard instead of the login page. The sites section fetches data from the existing `GET /sites` API endpoint and renders a table with site name (linked to the live URL), creation date, size, and a delete button.

## Acceptance criteria

- [ ] `server/src/server/templates/dashboard.html` exists, extends `base.html`
- [ ] Root `/` route renders `dashboard.html` when the user is authenticated (cookie present and valid)
- [ ] Root `/` route still renders `login.html` when unauthenticated
- [ ] In DEV_MODE, root `/` route renders dashboard with the fake dev user
- [ ] Dashboard shows the user's GitHub login/name (passed from server template context)
- [ ] Dashboard fetches `GET /sites` on page load and renders a sites table
- [ ] Each site row shows: name (as a link to `https://{name}.{domain}`), created date (human-readable), size (human-readable, e.g. "1.2 MB")
- [ ] Each site row has a delete button that calls `DELETE /sites/{name}` and removes the row from the table
- [ ] Empty state shown when user has no sites
- [ ] Tests: `GET /` with valid session cookie returns dashboard HTML containing the username

## Blocked by

- Blocked by Step 1 (cookie auth infrastructure + templates)

## Key files

- `server/src/server/templates/dashboard.html` (new)
- `server/src/server/app.py` (update root route to be auth-aware)
