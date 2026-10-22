# ADR 0009: Application-owned framework integration and durable recovery

Status: implemented; target-environment acceptance remains required  
Date: 2026-10-15

This extends [ADR 0005](0005-provider-abstraction-pattern.md). Its provider ports
remain authoritative. Optional framework components now implement or consume
those ports; they do not replace the application's authorization or tool runtime.
This record describes the code's design, not a deployment or a team approval.

## Problem

Framework interoperability is useful only if switching a component preserves the
business contract. A second tool-execution loop, serialized request credentials,
or a checkpoint that silently renews budgets would make recovery less safe than
the original bounded Agent. A lost HTTP response is also ambiguous: it does not
prove that a database commit or tool execution did not happen.

## Decisions

### Separate components from policy

LLM, embedding and Agent orchestration are independent selections. LangChain
chat/embedding adapters map real framework responses into the existing DTOs.
Controlled Retriever, Runnable and StructuredTool exports permit composition,
while the same authorized retrieval service and ToolRegistry execute operations.
Optional native model tool calls produce one declarative decision at a time.
The framework never receives authority to run an additional agent loop.

Framework callbacks, ambient tracing and debug collectors are isolated from
private requests. Application trace context remains usable through the existing
redacted boundaries. Unavailable provider usage is reported as unavailable, not
invented. Citations keep their canonical document/version/chunk identity.

### Keep durable business state separate from framework state

The private business snapshot owns the original wall-clock deadline, cumulative
budgets, repeated-action state, pending decision, evidence references and approval.
A compare-and-swap revision and expiring lease fence writes by competing workers.
LangGraph uses a real persisted StateGraph and a constrained checkpoint saver;
its JSON control channels carry opaque run references and routes, not prompts,
credentials, source excerpts, model messages or arbitrary Python objects.

Every resumed business phase reloads authoritative state and revalidates current
identity, tool permission and source evidence. An approval binds the exact action
and tool definition, expires with the original run, and is not transferred to a
new action when a stale UI refreshes. Final answers require validation and a
successful terminal commit before publication.

### Record uncertainty instead of promising generic exactly-once effects

The ledger reserves a tool attempt before dispatch and atomically commits its
completed result with the next business phase. A completed result is reused.
An uncertain read-only call may replay under policy and the remaining budget.
An uncertain side-effecting call marked `never` enters `intervention_required`;
a human must reconcile the external system. Database fencing cannot undo an
already sent request or make a third-party side effect transactional.

An optional client-generated startup UUID maps to one tenant/user-scoped run.
Its command fingerprint prevents reuse with a different command. Duplicate start
requests are reads; they do not acquire another execution lease or renew budgets.
Read-only recovery by key uses a POST body so access-log URLs do not contain the
key. Atomic creation and ownership-scoped cleanup distinguish the winning request
from a cancelled duplicate. Retained tombstones prevent recreation after private
snapshot expiry; that guarantee lasts only while the tombstone is retained.

### Bound auxiliary work without weakening required writes

Already-best-effort audit and progress delivery have finite waits and bounded
cancellation cleanup. Required tool recording, permission checks, the durable
journal and terminal persistence remain fail-closed. Auxiliary audit operations
use owned sessions so their cancellation or commit cannot poison the mandatory
request transaction. Python cancellation is cooperative; integrations must also
use cancellation-responsive clients and their own network/database timeouts.

### Treat ordinary commit acknowledgement as a separate contract

The non-durable application service uses cooperative storage phase budgets. Each
create/update and its commit share a deadline; rollback is separately bounded.
A commit that started without an acknowledgement is uncertain, even if rollback
returns. It is not automatically repeated or replaced by a cancellation write.
Acknowledged terminal state is recorded before optional audit, and mandatory I/O
remains owned rather than detached. These phases do not alter the existing
runtime/Registry fast-cancellation contract or solve its shared-session descendants.

## Consequences and rejected shortcuts

- Native defaults and optional extras preserve a smaller baseline installation
- A purpose-built sequential graph is less general than arbitrary LangGraph
  applications, but its state and execution policy can be audited and tested
- A fixed original deadline can expire during human review or downtime. Resume
  does not grant more time; longer-lived workflow policy would be a separate design
- CAS conflicts and unknown outcomes are user-visible states, not hidden retries
- Fast cancellation and shared request-session descendants have an unresolved
  resource-lifecycle boundary. Auxiliary I/O isolation is not proof that mandatory
  handler/recorder work has exited before session reuse or closure. Changing that
  policy requires an explicit join-versus-isolated-transaction design
- Startup protocol changes and snapshot schema changes require explicit reader/
  writer compatibility planning. Old workers must not receive new keyed traffic
- Enabling a generic framework checkpointer on a closure-based loop, replaying
  all tools after a crash, or treating HTTP abort as a cancellation receipt were
  rejected because they do not preserve these invariants

## Evidence and remaining gates

[Framework contracts](../FRAMEWORK_ADAPTERS.md) cover the eight meaningful backend
combinations and controlled ecosystem composition. [Durable operations](../operations/DURABLE_AGENT.md)
links persistence, process-exit, approval and competing-owner tests.
[Auxiliary I/O](../operations/AGENT_AUXILIARY_IO.md) documents bounded hooks and
session isolation. [Ordinary persistence phases](../operations/AGENT_PERSISTENCE_PHASES.md)
explain commit uncertainty, rollback and cooperative limits. The [database acceptance gate](../operations/DURABLE_DATABASE_ACCEPTANCE.md)
distinguishes local SQLite evidence from authored PostgreSQL/RLS acceptance.

MockTransport, synthetic fixtures and real SQLite/LangGraph tests establish their
tested contracts. They do not establish real-provider quality, PostgreSQL failover,
browser behavior, throughput, backup/restore or production readiness.

## Subsequent bounded extensions (2026-10-16)

The decisions above describe the original default wall-clock behavior. The later
opt-in active/background modes preserve old snapshots while independently bounding
execution and approval waits; see [current durable operations](../operations/DURABLE_AGENT.md).
Built-in descendant SQL operations now own their sessions as documented in
[session lifetimes](../operations/AGENT_SESSION_LIFETIMES.md). These extensions do
not turn this historical design record into target-environment acceptance.
