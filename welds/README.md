# Weld history audit trail

The weld log maintains a full audit trail of create, update, and rollback events for each weld.
All revisions are stored in the `WeldHistory` model, which snapshots the serialized weld data
before and after each change, along with the list of modified fields, the user who made the
change, and an optional reason.

## API endpoints

* `GET /projects/<org>/<project>/weld-log/welds/<weld_pk>/history/` — returns the paginated
  history for a weld (most recent first). Each entry includes the before/after snapshots,
  changed fields, metadata about the user, and any recorded reason.
* `POST /projects/<org>/<project>/weld-log/welds/history/<history_id>/rollback/` — restores the
  weld to the snapshot captured before the referenced history entry. Rollbacks require project
  manager permissions and create a new `WeldHistory` record documenting the action.

## Front-end integration

The weld log UI provides a modal detail view for each weld, including a history panel that
renders human-friendly field-level diffs and exposes rollback controls (when permitted).
Rollback actions surface conflict warnings if the weld has changed since the selected snapshot.
