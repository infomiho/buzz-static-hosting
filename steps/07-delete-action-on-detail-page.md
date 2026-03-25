# Step 7: Delete Action on Detail Page

## Parent RFC

See `rfc.md` in project root.

## What to build

Add a delete site action to the site detail page. Uses the existing `DELETE /sites/{name}` API endpoint. Includes a confirmation dialog (same Basecoat dialog pattern used in the dashboard) and redirects back to the dashboard after successful deletion.

## Acceptance criteria

- [ ] Detail page has a delete button (destructive styling, positioned in the metadata header area)
- [ ] Clicking delete opens a confirmation dialog with the site name
- [ ] Confirming calls `DELETE /sites/{name}` and redirects to `/` on success
- [ ] Cancel dismisses the dialog without action
- [ ] Button shows loading state while the delete request is in flight

## Blocked by

- Blocked by Step 5 (detail page must exist)

## User stories addressed

- User story 7: Delete a site from its detail page
