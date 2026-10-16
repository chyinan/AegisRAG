# Product-readiness improvement plan

This bounded engineering pass prioritizes complete, demonstrable user journeys
and verifiable failure behavior. It does not authorize deployment, production
migration, publication or changes to credentials/access settings. Implementation
and evidence are separate: an authored test or CI job is not a passing run.

## Milestone 1: framework integration acceptance

Complete the [parity plan](FRAMEWORK_PARITY_PLAN.md): eight independent backend
combinations, controlled ecosystem exports, native tool decisions, shared Agent
SSE, explicit durable recovery/approval and version compatibility gates. Preserve
native application policy and truthfully distinguish offline, alternate-version,
live-endpoint and PostgreSQL evidence. Independent failure-injection regressions
remain in normal CI discovery.

## Milestone 2: complete the existing Agent workbench

After Milestone 1 acceptance, add explicit opt-in live-progress and durable modes
to the existing workbench, retaining ordinary one-shot behavior. A useful demo
must show progress, stopped/failed/incomplete states, exact approval arguments,
original deadlines, consumed budgets, conflict refresh and current terminal state.

Required invariants:
- Permission checks remain server-owned and no UI control enables backend settings
- A local abort is not falsely described as a confirmed persistent cancellation
- Stale approvals are never silently refreshed into approval of a different action
- Authentication change/unmount cancels owned requests and excludes stale UI updates
- Private input, approval arguments and credentials are not put in browser storage
- Unknown side-effect outcomes visibly require intervention, not automatic retry
- UI fixtures come from the actual backend serializers and DTOs; component tests
  do not substitute for an unavailable real-browser acceptance pass

## Milestone 3: operational and demonstration evidence

Choose the next work from concrete gaps exposed by the completed journey. Prefer
bounded audit/storage failure handling, recovery/retention diagnostics, reproducible
synthetic acceptance, upgrade/restore instructions and safe evidence packaging over
new unrelated providers or connectors. Keep any new control opt-in and any
material product tradeoff explicit. Do not increase privileges to make a test pass.

Record source version, dependency versions, commands, test scope, skips, expected
failures and environment limits. Keep retained artifacts free of credentials,
private prompts and user documents. Write the demo and interview story from
implemented behavior and its evidence, without invented users, latency gains,
production incidents, team history or deployment claims.

## Final acceptance

Reserve time for full applicable backend and frontend validation, focused
independent review, documentation/link checks and an isolated patch/source
manifest. Report remaining real-provider, PostgreSQL/RLS, browser and deployment
gates explicitly. Preserve the original workspace and leave publication to the
owner's separately authorized workflow.

## Recorded progress (2026-10-15)

The framework integration milestone has an immutable source/evidence snapshot:
2,486 locked-backend tests passed, with four explicit skips and five existing
non-strict XPASS results. A cached compatible framework subset passed 754 tests;
these are overlapping scopes, not additive counts. The cached alternate is not
upstream-latest or minimum-supported evidence.

The Agent workbench, startup-key recovery, bounded transport/auxiliary I/O and
required database acceptance gate are implemented. The original workbench
milestone passed 258 frontend tests and a webpack production build. The combined startup-key and transport continuation then passed all 335 frontend
tests across 27 files, lint, TypeScript and the installed webpack production build.
Final backend acceptance is recorded separately in the accompanying stage report;
focused counts are not added together.

A final durable completed-response reconciliation correction still lacks its
last independent review. Earlier passing lease tests do not close that gap.
Physical PostgreSQL, real provider, browser-engine and target deployment gates
remain unverified. Ordinary Agent mandatory persistence now has cooperative
phase deadlines and explicit uncertain-commit handling, with unit, SQLite and
independent full-stream regressions. The change never detaches mandatory storage
and does not redefine the runtime budget. The separate shared-session descendant
cleanup limitation remains open. Complete aggregate acceptance is recorded in the
stage report rather than inferred from focused checks.

## Subsequent implementation (2026-10-16)

The dated progress above remains historical evidence. The later product-completion
pass adds operation-owned descendant sessions and the eight bounded extensions;
see [the current implementation/validation record](operations/PRODUCT_COMPLETION_VALIDATION.md)
and [session ownership](operations/AGENT_SESSION_LIFETIMES.md). Current exact-head
PR checks and remaining target gates must be read separately from the older counts.
