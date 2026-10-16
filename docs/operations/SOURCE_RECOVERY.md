# Manual source recovery

After a transient source read failure, **Retry source / 重试来源** repeats only the
selected document/version/chunk lookup. It preserves the displayed answer, current
chat session, draft question and active surface. Retry is unavailable while the
lookup is pending; another click is required after another transient failure.
There is no automatic retry, polling, chat generation or Agent replay.

The control covers transport `TypeError`, cooperative `TimeoutError` already
propagated by the source client, and HTTP 408, 429 or 5xx. No new deadline is added.
Missing/unavailable sources, other 4xx responses and invalid successful source
responses retain their previous handling. In particular, a successful HTTP
response whose body read throws `TypeError` is still `INVALID_RESPONSE` under the
unchanged generic parser, so this case does not offer retry.

The pre-existing **Diagnostics → Evidence** inspector-tab workaround also issues
a fresh lookup without clearing the answer, session or draft question. Merely
selecting Diagnostics does not call its endpoint. Re-clicking the same citation
alone does not repeat the failed read. Changing citation, closing its view or
starting a new conversation cancels the pending lookup and ignores late results.
