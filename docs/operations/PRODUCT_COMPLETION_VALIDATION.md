# Product completion: implementation and validation

Date: 2026-10-16. Source baseline: `3d015ae2d6fe5ac9728802774102fa9a7245f847`.
This record describes the locally validated implementation before its authorized
draft-PR publication. It separates implemented entry points and offline evidence
from acceptance in a configured deployment; publishing the source does not satisfy
the target-environment gates below.

## Implemented entry points

| Area | Available entry point | Important operating boundary |
| --- | --- | --- |
| Folder synchronization | Knowledge Base plan/batch/status/candidate controls; `/v1/sources/folder/sync/*`; optional polling CLI | Operator-configured root and opt-in worker; missing sources produce candidates only. Exact selected IDs and revision require confirmation before existing soft deletion. |
| RQ recovery | Knowledge Base queue inspector and retry/terminate controls; `/v1/queue/jobs*` | Uses current delivery/status/attempt facts and the existing retry allowance. `stop_requested` is not a termination receipt. |
| Long execution and approval | Agent Runs durable budget/dispatch controls, approval and refresh/recovery; durable API and worker CLI | Old snapshots keep wall-clock semantics. Active execution and approval waiting have separate bounds. Background dispatch defaults off and requires a trusted operator-supplied current-context resolver/factory. |
| Cancellation/session lifetime | Ordinary, durable and ServiceToken Agent composition | Retrieval, required tool-call persistence and evidence rehydration own their operation sessions. Finite cancellation-resistant work cannot use a closed request session. Infinite custom cancellation resistance still needs provider/driver timeouts and process supervision. |
| Usage and cost | Review cost/budget views; `/v1/costs/summary` and `/budget` | Opt-in, operator-versioned prices and atomic reservations. Unpriced/incomplete usage remains unknown with retained holds. This is an application guardrail, not an invoice hard cap. |
| Frozen evaluation | `python -m packages.eval.domain_cli`; captured-context integration and optional judge adapter | Sixteen synthetic IT-operations cases are an initial offline contract corpus. They do not establish broad domain coverage or live model quality. |
| Deployment/recovery | Helm and Compose configuration, queue metrics, upgrade checker and explicit snapshot/isolated-restore CLI | Procedures/configuration are implemented. Installing the stack, migration in PostgreSQL, admission/PVC behavior and a successful service-level restore require target execution. |
| Support tickets | Agent mandatory approval for `create_support_ticket`; Review owner/manager ticket queue and status controls | Opt-in local application records; idempotent client reference and atomic audit. Normal approval precedes creation; manager transitions use current revisions. No external ticket system or messaging is implied. |

The UI reuses the existing Workbench. It does not enable flags, grant permissions,
install a resolver, start workers or approve actions by merely reading status.
See [operations controls](../OPERATIONS_WORKBENCH.md),
[durable controls](../AGENT_DURABLE_WORKBENCH.md) and
[deployment procedures](PRODUCT_DEPLOYMENT.md).

## Offline verification

- Focused backend aggregate: **163 passed, 2 skipped**, 165 collected. The two
  skipped checks require an unavailable Helm executable. This is a selected
  normal-feature suite, not a claim about the complete repository test suite.
- Extended existing positive regressions: **119 passed**. The JUnit case sets
  have zero overlap with the focused aggregate: **282 unique passed, 2 skipped**
  across both selections. This does not repeat the historical 3,177-case suite;
  the broader audit/live-service scope was not run in this task.
- Frontend aggregate: **224 passed**, 14 files, including Workbench DOM/state/API
  tests and 28 execution-proxy tests. No browser was launched.
- TypeScript: passed with the installed toolchain. Scoped ESLint: passed for all
  30 changed frontend TypeScript/JavaScript paths.
- Next.js 16.2.9 production build with the installed offline dependencies:
  passed using `next build --webpack`, including the dynamic execution route.
- Ruff: all 82 changed Python paths passed. Production mypy: 56 changed paths
  passed with `--python-version=3.12 --follow-imports=silent`, matching the
  available Python 3.12 environment and its installed dependency stubs.
- Migration metadata has one head, `20261016_0021`. File-backed SQLite tests
  verify the `0019 → 0020 → 0021` schema/model contract, persisted data and
  ordered downgrade preservation. This does not verify PostgreSQL/RLS behavior.
- Frozen evaluation CLI: **16/16** offline contracts and zero-drop comparison
  passed. Reports identify corpus, case, configuration and gate hashes.
- Scoped proxy contract passed. Local native HTTP/SSE fixtures exercise response
  forwarding and downstream cancellation; they are not a deployed Next/ingress
  acceptance test.
- Independent final diff inspection found no remaining blocking finding after
  the reviewed fixes. It did not expand the test/audit scope.

The first extended regression run had three failures (113 passed): the generated
Agent UI fixture lacked new response defaults, and two Compose contracts did not
yet include the new durable worker. The fixture was regenerated with the existing
production serializer; the exact application-service list was extended to the
worker, adding three parameter cases while retaining the original anchor/default
assertions. The complete same selection then passed all 119 cases. Initial failure
logs are retained alongside the final evidence; production behavior and test
deadlines were not changed by these two corrections.

The execution proxy is restricted to specified Agent start/stream/resume and
folder batch/reconcile POST paths. Background submission has a 30-second bound,
folder execution 315 seconds, ordinary Agent execution 135 seconds, and explicit
long active inline execution at most its remaining budget plus 15 seconds
(3,615-second maximum). Ordinary reads, recovery and cancellation retain the
existing short proxy path. There is no global long proxy timeout or global HTTP
dispatcher change. Background submission plus status polling is preferable for
long tasks once the resolver/worker has actually been configured.

## Publication integration revalidation

The initial counts above remain the pre-publication record. After CI integration
corrections, the same two backend selections plus 25 architecture cases passed
**307 unique cases with 2 Helm-unavailable skips**. The unchanged frontend
selection passed **224 cases** with one worker. Ruff passed on 86 changed Python
paths; Python-3.12-target mypy passed on 57 application/operator paths.

The integration corrections moved concrete session-owning adapters into storage
layers without changing their class behavior, added four exact FastAPI factory
support paths while prohibiting route declarations in those support files, and
restricted an existing accessible Chinese upload-label locator to the file input.
The original upload, cancellation, persistence and architecture assertions remain.
An intermediate two-worker frontend repeat recorded 220 passes and four generic
stack/time-budget failures; the unchanged one-worker repeat passed. Both records
are retained; no timeout or assertion was relaxed and no root cause is claimed.

Existing CI also exposed exact migration-head/table-set expectations predating
0019–0021, and a lease-acquisition test that still classified success as `None`
after acquisition began returning `DurableSnapshot`. Expectations now match the
current schema and typed return. Exactly one winner, loser conflict, winning token,
CAS and cancellation assertions remain intact. The initial failure alone did not
prove two lease winners. These database/audit cases were not executed locally.
Only the existing naturally triggered CI runs them under the separate publication
approval; current CI results belong to the PR's exact head, not to this local record.

The historical 3,177-case suite was still not rerun locally. This revalidation does
not satisfy the deployment/provider/manual acceptance gates below.

## NOT_RUN acceptance gates

The following remain unverified and must not be reported as successful:

1. Real PostgreSQL/Redis/RQ/MinIO behavior, concurrent multi-worker deployment
   and production role/policy semantics
2. A configured trusted background resolver, real long-running work and long
   approval recovery across actual worker/process restarts
3. Docker Compose installation/build/upgrade, Helm lint/render, Kubernetes
   admission, PVC permissions, graceful drain and complete backup/restore rehearsal
4. Real-browser behavior, actual Next/ingress connection lifetimes and an
   hour-long request or background execution
5. Real model/embedding/provider invoice reconciliation, paid judge execution,
   and frozen-domain quality on a representative user-owned domain corpus

No credentials were provisioned, paid providers called or deployment changed
during local implementation/validation. Source publication is a separate
authorized step; optional features remain disabled by default.

## Upgrade and rollback

Apply the existing controlled upgrade procedure through `0019` (folder plans),
`0020` (cost reservations/charges) and `0021` (support tickets). Preserve a verified
recovery point first. Downgrading these revisions drops their new private
workflow records; it is not an automatic rollback strategy. Existing durable
snapshot compatibility is preserved without rewriting old wall-clock records.

The baseline source snapshot is unchanged. Its pre-existing missing historical
`screenshot.png` was not synthesized or included in the change set.
