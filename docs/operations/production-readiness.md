# Production readiness runbook

This project treats a successful response as a governed, tenant-scoped workflow rather than a vector demo.

## Release gates

- Run `uv run ruff check .`, the full backend suite, and the Web lint/typecheck/unit/build pipeline.
- Run both RAG gates. The 20-case smoke gate and 224-case extended gate are deterministic fixture checks; they must never contain placeholder corpus markers.
- Run a real-provider benchmark against a staging deployment before promotion. Record P50/P95 latency, token usage, retrieval hit rate, citation coverage, ACL isolation, and prompt-injection results for the exact model and embedding versions.
- Do not promote if the real-provider P95 exceeds the configured service objective or if an ACL/prompt-injection case is missing from the dataset.

## Backup and restore

PostgreSQL, Redis, and MinIO are stateful boundaries. Schedule:

```powershell
docker compose --env-file .env -f docker/compose.yaml exec -T postgres `
  pg_dump -U $env:POSTGRES_MIGRATION_USER -d $env:POSTGRES_DB --format=custom > backups\aegisrag-$(Get-Date -Format yyyyMMdd-HHmmss).dump
docker compose --env-file .env -f docker/compose.yaml exec -T minio `
  mc mirror --overwrite /data /backup
```

Restore into an isolated environment first, run `alembic upgrade head`, and verify tenant/RLS smoke tests before switching traffic. Keep database dumps and object backups encrypted, access-controlled, and outside the application container.

## Tenant and security invariants

- API and worker connections use the non-superuser runtime role; migrations use the dedicated migrator role.
- Every request carries `tenant_id`, `user_id`, request/trace IDs, and RBAC permissions. PostgreSQL RLS is the final tenant boundary.
- Connector roots are tenant-specific and read-only; document signatures, ZIP limits, and symlink checks run before parsing.
- Redis revocation/session-family state and queue idempotency leases are shared across replicas.
- Audit records contain safe summaries only. Never export prompts, raw tool output, access tokens, API keys, object keys, or document excerpts.

## Incident response

Use the request or trace ID to inspect the diagnostics timeline, retrieval provenance, tool calls, and final-answer validation. If a source is suspected of being exposed, revoke the session family, disable the connector, preserve the audit trail, and rotate the affected secret before re-enabling traffic.
