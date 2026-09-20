# QwenPaw Pilot plugin

This native plugin targets the AgentCore runtime ABI: QwenPaw **2.1.0** and
AgentScope **2.0.4.post1**. It does not import the LoongSuite Python probe or
initialize an OpenTelemetry exporter. Unsupported installed versions do not
attach instrumentation.

Pilot deploys this directory to `$QWENPAW_WORKING_DIR/plugins/loongsuite-pilot`
(default `~/.qwenpaw/plugins/loongsuite-pilot`). QwenPaw must load it through its
normal plugin loader at startup; copying files into a running process does not
install its hooks. Restart QwenPaw after initial deployment or changes.

The writer uses `LOONGSUITE_PILOT_DATA_DIR`, then `dataDir` from the Pilot-managed
`.loongsuite-pilot-managed.json` marker, then `~/.loongsuite-pilot`. Output is
`logs/qwenpaw/qwenpaw-YYYY-MM-DD-PID.jsonl` (UTC date), with directory mode 0700
and file mode 0600. A failed write does not stop the application. It honors
`LOONGSUITE_PILOT_ENABLED=false`, configuration `enabled=false`, and
`agents.qwenpaw.enabled=false`. Setting `agents.qwenpaw.captureMessageContent`
to false removes message/system/tool content and error text before writing.

`PRE_DISPATCH` and `FINALLY` create one explicit request boundary, including
slash-command shortcuts and runtime errors. The agent middleware wraps reply,
reasoning, model and tool execution. A reversible `Agent.__init__` attachment
covers helper agents created outside QwenPaw's main builder without double
attachment. The optional reversible ReMe `dream` wrapper assigns background
work its own request identity and the owning QwenPaw agent name. Shutdown or
uninstall restores only wrappers still owned by this plugin. Existing agent
middlewares also stop collecting through the plugin owner's active flag,
including suspended streams; application execution continues unchanged.

Records use `llm.request`, `llm.response`, `tool.call`, and `tool.result`.
Boundaries use `event.name=other` with `agent.qwenpaw.boundary` equal to
`entry.start/end`, `agent.start/end`, or `step.start/end`. Each operation has a
16-character `agent.qwenpaw.span.id` and its explicit `agent.qwenpaw.parent.id`;
start/end pairs share the same ID. `gen_ai.step.id` identifies one model call,
while `agent.qwenpaw.reasoning.id` and `.round` identify the containing ReAct
step. A STEP remains open through tool execution and closes when the next
reasoning starts or the agent finishes; Runtime cancellation closes children
before the terminal request boundary. `gen_ai.turn.end=true` appears only on the request end record. Timestamps
are decimal nanosecond strings; TTFT is a numeric nanosecond duration measured
at the first observed nonempty streaming content. Non-streaming responses do
not receive synthetic TTFT. Tool duration follows Pilot's millisecond contract.

Session IDs remain stable across turns; request IDs identify turns. Runtime
Task fields come directly from `request_context.agentcore`, not process-global
environment variables. Stream context is restored before yielding to callers.
The native `Skill` tool records name, workspace-scoped ID, description and
version from already loaded Skill metadata and QwenPaw's bounded frontmatter
reader (with the Python probe's legacy manifest version fallback). Arbitrary
`read_file` calls are not inferred to be skill invocations. Nested tool-result
blocks are normalized into GenAI parts while scalar/map results remain intact.

Final agent output comes from actual reply messages, excluding earlier tool
interactions. No model call is invented for a shortcut. Runtime shortcuts that
never invoke an agent have no middleware-derived reply content.

Focused tests (with the pinned QwenPaw environment):

```sh
python -m unittest discover -s tests/unit/hooks/qwenpaw-plugin -v
```
