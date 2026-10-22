# Continuous folder synchronization

Folder sync extends the existing checksum-incremental folder importer with a
durable tenant-scoped plan, resumable bounded batches, a polling scheduler, status
API, and reviewed soft deletion of documents whose managed source files disappear.
It does not watch arbitrary paths or read a path supplied by an API caller.

## Enable and configure

1. Apply migrations with `uv run alembic upgrade head`. Revision
   `20261016_0019` adds `folder_sync_plans`, its due-work index, uniqueness for
   `(tenant_id, connector_id)`, and PostgreSQL tenant RLS.
2. Set `FOLDER_SYNC_ENABLED=true` on the API. Keep the existing database,
   object-storage, ingestion-worker, embedding-worker and queue configuration.
   These variables are optional:

   ```dotenv
   FOLDER_CONNECTOR_ROOT=/data/connectors
   FOLDER_SYNC_ENABLED=true
   FOLDER_SYNC_BATCH_TIMEOUT_SECONDS=120
   FOLDER_SYNC_MAX_MANAGED_SOURCES=10000
   ```

   Production requires `FOLDER_CURSOR_SECRET` or `JWT_SECRET`, as does the
   existing folder import endpoint. A plan pins its configured root's identity
   (path, filesystem device and directory inode). A changed/replaced root returns
   `FOLDER_SYNC_ROOT_CHANGED`; restore the original directory before resuming.
3. Put supported files inside `FOLDER_CONNECTOR_ROOT/<tenant_id>/`. Supported
   types remain PDF, DOCX, UTF-8 TXT and Markdown. Symlinks and oversized or
   invalid documents are skipped by the existing connector.
4. With an authenticated `document:manage` identity, call:

   ```http
   PUT /v1/sources/folder/sync/plan
   Content-Type: application/json
   Authorization: Bearer <existing-token>

   {
     "connector_id": "folder",
     "enabled": true,
     "interval_seconds": 300,
     "batch_size": 25,
     "delete_missing": false
   }
   ```

The returned `data.id` identifies the single plan for that tenant and connector.
Repeating PUT updates the plan without discarding its managed-source manifest or
checkpoint. `interval_seconds` is 30–86400 and `batch_size` is 1–100. A running
batch returns 409 if another request tries to change its plan. Setting
`enabled=false` pauses it between batches and remains possible if its source
directory is unavailable.

## Run scheduled batches

The scheduler calls the regular authenticated API. Every batch therefore uses
current request authentication and tenant context; it does not store a copied
user permission set or create a privileged cross-tenant scheduler identity.

Provide an existing, appropriately scoped bearer token through the scheduler's
process environment (never as a command argument):

```sh
FOLDER_SYNC_API_URL=https://rag.example.internal \
  uv run python -m apps.worker.folder_sync --poll-seconds 5
```

Set `FOLDER_SYNC_BEARER_TOKEN` through your normal secret-injection mechanism.
The origin must use HTTPS, except loopback HTTP for local development. Redirects
are not followed. Run one scheduler per authenticated tenant context; multiple
schedulers for the same tenant are safe because the database atomically leases
each plan. 401/403 stops the scheduler so credentials can be renewed. Tokens are
never written to plan storage or logs.

For an existing system timer, run one pass with:

```sh
uv run python -m apps.worker.folder_sync --once
```

Each pass submits one due batch per enabled plan. While a cycle is incomplete,
the next pass continues it; the configured interval begins when the entire
scan and reconciliation cycle completes. A failed batch retains the completed
item checkpoints and is retried after at most 60 seconds. The request has a
configurable timeout and a slightly longer database lease; an interrupted
process can be resumed after lease expiry. Upload identities remain based on
source and checksum, preventing duplicate versions across ordinary retries.

To run a batch manually, call:

```http
POST /v1/sources/folder/sync/plans/<plan_id>/batch
```

Pass `?due_only=true` to respect the schedule, as the scheduler does. Without it,
an enabled plan runs immediately. A paused plan remains paused in either mode.

## Status and source deletion

Read `GET /v1/sources/folder/sync/plans` or
`GET /v1/sources/folder/sync/plans/<plan_id>`. Responses include:

- enabled flag, interval, batch size and deletion policy
- cycle number, scan/reconciliation phase and status
- imported, skipped, deleted and currently managed counts
- last cycle start/completion, next due time and safe error code
- current revision, candidate count and a preview of up to 25 deletion candidates

Counts are per cycle. Imported means a version and ingestion job were accepted;
it does not mean embedding has completed. Use the existing document version
status endpoint for downstream ingestion/index readiness. Status never exposes
source content, filesystem roots, internal cursors or the private manifest.

`delete_missing` defaults to false. Enabling it enables candidate detection only.
After a successful complete scan, bounded reconciliation checks only paths
previously imported by that plan and records missing-source candidates. Scheduled
and manual batches never delete documents automatically.

Review the status preview or page through
`GET /v1/sources/folder/sync/plans/<plan_id>/candidates?offset=0&limit=25`.
Then explicitly confirm the selected candidate document IDs and the exact
reviewed revision (up to 100 IDs per confirmation):

```http
POST /v1/sources/folder/sync/plans/<plan_id>/reconcile
Content-Type: application/json

{
  "expected_revision": 7,
  "document_ids": ["<reviewed-candidate-document-id>"]
}
```

The service takes the same plan lease, verifies that a completed candidate scan
is still current, rechecks the pinned root and source absence, then calls the
existing document lifecycle deletion service for those selected documents only.
It soft-deletes documents/versions/chunks and removes vectors. It does not
permanently delete local files, raw objects, database records or unrelated uploads.
If the revision changed, refresh the candidate list and review again. The
scheduler never calls the confirmation endpoint. Setting `delete_missing=false`
clears pending candidates without deleting their managed documents.

Only a definite missing path below an available configured tenant directory is
eligible. A present but invalid/oversized document, permission error, symlink,
or unavailable tenant root is preserved. Reappearing files get a new managed
document incarnation, so soft-deleted history remains intact. The manifest
retains deletion tombstones for this purpose; tombstones count toward the
configured managed-source limit. Raising that limit is an operator decision.
The bounded JSON manifest is intended for private-pilot folder collections;
it is not an unbounded filesystem inventory.

The opt-in flag is off by default. No scheduler is started automatically by
API startup, migration, worker startup or importing this module.

## Local verification

```sh
uv run pytest --no-cov -q tests/integration/worker/test_folder_sync_feature.py
```

This suite uses SQLite, in-memory object/queue providers and mocked HTTP. It
checks restart/resume, incremental unchanged/updated imports, the normal API,
pause/resume, candidate review followed by explicit lifecycle deletion,
reappearance, scheduler due-work
selection, and migration upgrade/downgrade without external model calls.
