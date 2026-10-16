# Framework integration acceptance plan

AegisRAG integrates supported framework components without delegating business
policy to the framework. This plan defines the scope and acceptance boundaries
for the implementation; the capability and evidence matrix in
[FRAMEWORK_ADAPTERS.md](FRAMEWORK_ADAPTERS.md) records what is actually verified.

## Invariants

1. ToolRegistry remains the sole tool-execution authority. A model emits one
   declarative decision; no framework agent runs a second execution loop
2. Tenant/user context comes from the authenticated application request, never a
   model argument, RunnableConfig override, checkpoint body or approval payload
3. Retrieval authorization precedes model context. Canonical citations retain
   document, version and chunk identity through every supported adapter
4. An Agent answer is visible only after final-answer validation and successful
   terminal persistence. Model/tool progress is not an answer
5. Retry budgets, tool/step budgets and deadlines belong to AegisRAG. Interrupted
   execution cannot reset them, revive cancellation, or reuse stale approval
6. A completed tool ledger entry is immutable. Unknown side-effect outcomes need
   operator reconciliation; there is no generic exactly-once guarantee
7. Framework callbacks, ambient tracing, debug collectors and caches do not gain
   automatic access to application inputs, raw tool content or credentials
8. A locked offline test proves the tested contract and version only. It does not
   establish live provider compatibility, model quality or production readiness

## Implementation layers and acceptance

### P0: Business-contract parity

Exercise native and optional LLM, embedding and orchestration selections in all
8 meaningful combinations. Assert API envelopes, filters, citations, chat memory,
embedding worker provenance, governed Agent behavior and errors. Real framework
classes use controlled local transports; deterministic outputs are fixtures, not
claims of equivalent real-model prose.

### P1: Controlled ecosystem exports

Expose authorized retrieval/query services as asynchronous framework components
and Registry tools as StructuredTool values. Consumers may compose real LangChain
sequences and LangGraph graphs while AegisRAG retains validation, permissions,
timeouts and audit. Synchronous thread/loop substitution is not supported for
application services that own asynchronous database sessions.

### P2: Shared Agent lifecycle and structured decisions

Support redacted ordered progress over SSE for both schedulers. Final results
follow validation and persistence; disconnect cancels owned work and attempts a
bounded durable terminal update. A separate provider-neutral decision port supports
native tool calls while preserving the legacy text-only LLM contract and JSON
stepper default. Malformed/multiple/unrequested calls fail closed.

### P3: Durable sequential controlled execution

Use a versioned private business snapshot, safe framework checkpoint references,
transactional revisions/leases and a tool-result ledger. Restore cumulative
budgets, repeat detection and deadline; reauthorize identity and evidence; bind
approval to the exact action and definition. Test restarts, failed persistence,
concurrent resumes, cancellation races and ambiguous tool outcomes. The supported
scope is a sequential registered-tool graph, not arbitrary graphs or multi-agent
scheduling. Existing agent_runs summary rows alone do not establish durability.

### P4: Version and deployment evidence

Run locked-version tests locally and gate supported minimum/latest-compatible
versions separately in CI. Keep opt-in live endpoint acceptance distinct from
mocked HTTP/framework tests and synthetic evaluator contracts. Document every
unexecuted environment or endpoint check. Update README and stale architecture
claims to match the implementation, not an aspirational feature list.

## Review and completion

Each layer requires focused regression tests and an independent lifecycle review.
The final pass includes relevant aggregate tests, lint/type checks, migration
review, and a changed-file/evidence manifest. Database migrations are authored and
tested against isolated fixtures; applying them to a deployment, publishing,
merging and releasing are separate operations.
