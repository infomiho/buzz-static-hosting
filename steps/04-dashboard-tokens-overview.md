# Step 4: Dashboard - Deploy Tokens Overview

## Parent RFC

See `rfc.md` in project root.

## What to build

Add a deploy tokens section to the dashboard page. It fetches data from the existing `GET /tokens` API endpoint and renders a table alongside the sites section. Users can see and delete their deploy tokens.

## Acceptance criteria

- [ ] Dashboard fetches `GET /tokens` on page load and renders a tokens table
- [ ] Each token row shows: name, associated site, created date, last used date (or "Never"), delete button
- [ ] Delete button calls `DELETE /tokens/{token_id}` and removes the row from the table
- [ ] Empty state shown when user has no deploy tokens
- [ ] Tests: dashboard page with tokens data renders correctly

## Blocked by

- Blocked by Step 3 (dashboard page must exist)

## Key files

- `server/src/server/templates/dashboard.html` (update)
