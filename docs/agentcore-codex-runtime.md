# AgentCore Codex App Server

This fork extends the pinned Qoder/QwenPaw runtime branch with Codex App Server
support. The production dependency lockfile and the existing Qoder invocation
context and QwenPaw plugin protocols remain unchanged.

The Runtime owns one shared App Server per execution generation. It registers
each native `session_id` (thread ID), `turn_id`, `transcript_path` and effective
`CODEX_HOME` through `assets/hooks/codex-hook-processor.mjs user-prompt-submit`
after `turn/start` returns. It invokes the same processor with `stop` after any
confirmed terminal result, including failed and interrupted turns. Payloads go
through stdin; registration failure degrades telemetry and never repeats model
or business tool calls.

`LOONGSUITE_PILOT_SPAN_ATTRIBUTES` accepts a JSON object of string values as well
as the existing comma-separated syntax. JSON preserves commas, equals signs,
newlines and Unicode in `agentcore.task_name` and `agentcore.subtask_name`.
Reserved fields and sensitive names are rejected; values retain the 512-character
limit. JSON should be used for task names. Context is persisted under the exact
native thread/turn pair and retains the existing 48-hour TTL.

Managed collectors set `agents.codex.requireTurnSpanContext: true`. A root turn
is not emitted until its own context exists. Missing context keeps the source
range and checkpoint recoverable across restart; a later Stop can retry the
registration. This option defaults to false for ordinary Codex installations.
Native child sessions retain the existing linker/fusion contract; this change
does not claim new child-agent inheritance behavior.

The Runtime's structured `{ "agentcore_tool_error": "CODEX_TOOL_FAILED" }`
tool result is classified by exact call ID. The original tool result is retained,
and `tool.result.status=failure` / `error.type=CODEX_TOOL_FAILED` reach the OTLP
TOOL span with status 2. A successful neighboring tool retains status 0. Arbitrary
error-looking prose and unrelated JSON business values are not classified as
errors. Content-off removes arguments/results while retaining the bounded error
classification. OpenClaw uses the same existing status mapping.

Packages include `assets/hooks/codex-runtime-capabilities.json`; Runtime checks
this contract before starting a managed collector so older packages cannot
silently misparse JSON attributes. Full-day CMS evidence and remaining baseline
gaps are recorded by the consuming Runtime's validation report.
