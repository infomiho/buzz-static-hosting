# Step 6: Dashboard Table Link Refinements

## Parent RFC

See `rfc.md` in project root.

## What to build

Refine the sites table in the dashboard so the site name links to the detail page instead of the live site. Add a small external-link icon next to the name that opens the live site URL in a new tab. This separates "manage this site" (click name) from "visit this site" (click icon).

Changes are in the `loadSites()` JS function in `dashboard.html`:
- Site name becomes a link to `/dashboard/sites/{name}` (bold text, no underline by default)
- Small external-link icon after the name opens `siteUrl(name)` in a new tab
- Icon uses the same subtle hover style as existing trash icons

## Acceptance criteria

- [ ] Clicking the site name navigates to `/dashboard/sites/{name}`
- [ ] External-link icon is visible next to the site name
- [ ] Clicking the external-link icon opens the live site in a new tab
- [ ] Icon does not trigger navigation to the detail page (separate click targets)
- [ ] Visual style is consistent with the existing dashboard theme

## Blocked by

- Blocked by Step 5 (detail page must exist to link to)

## User stories addressed

- User story 4: Click site name to go to detail page
- User story 5: External-link icon to visit live site
