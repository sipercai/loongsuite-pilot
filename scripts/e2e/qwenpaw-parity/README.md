# QwenPaw runtime parity

Two isolated Python environments exercise the actual QwenPaw 2.1.0 Runtime,
AgentScope 2.0.4.post1, and a real DashScope model. Baseline loads LoongSuite
Python 0.9.0 instrumentors; Pilot loads the native plugin through QwenPaw's
PluginLoader without enabling Python automatic instrumentation.

```bash
bash scripts/e2e/qwenpaw-parity/prepare.sh /tmp/qwenpaw-pilot-ab-example
```

Supply `DASHSCOPE_API_KEY` in the process environment. The harness stores it
only in the isolated QwenPaw secret directory; never commit the test data root.
The harness sets `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
and records it in run metadata. Python 0.9 requires this opt-in as well as
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_AND_EVENT` for message
attributes; setting the content mode alone is insufficient. Older evidence
without the opt-in supports structural/usage comparisons, not content parity.

```bash
/tmp/qwenpaw-pilot-ab-example/baseline/venv/bin/python scripts/e2e/qwenpaw-parity/run_runtime.py \
  --root /tmp/qwenpaw-pilot-ab-example/baseline-run --mode baseline --matrix
/tmp/qwenpaw-pilot-ab-example/pilot/venv/bin/python scripts/e2e/qwenpaw-parity/run_runtime.py \
  --root /tmp/qwenpaw-pilot-ab-example/pilot-run --mode pilot --matrix \
  --plugin assets/plugins/qwenpaw/loongsuite-pilot
python3 scripts/e2e/qwenpaw-parity/compare.py \
  --baseline /tmp/qwenpaw-pilot-ab-example/baseline-run \
  --pilot /tmp/qwenpaw-pilot-ab-example/pilot-run
```

The matrix includes a real file tool, a second turn, nonstreaming model output,
two concurrent sessions, a missing-file tool failure, cancellation after the
first output, and a real provider 404 for an intentionally invalid model.
The observer's POST_AGENT_BUILD hook sets the actual model's stream flag for
the nonstream scenario because QwenPaw's provider factory defaults to streaming.
No model or tool response is mocked. Add `--dream` to seed a synthetic daily
memory and invoke the actual ReMe Dream workflow (120-second bound).

Evidence lives under each run's `evidence/`: raw Runtime events, correlated
request/model/tool observer events, scenario outcomes, dependency versions, and
baseline spans. Pilot's raw events live under that run's isolated
`home/.loongsuite-pilot/logs/qwenpaw/`. The report compares structural coverage,
not token/timing equality: independent real model requests naturally differ.
The harness directly constructs the official shared WorkspaceBootstrapFactory
and WorkspaceRegistry used by Web/ACP startup. It does not exercise the
AgentCore container build, runtime HTTP adapter, cloud authorization, or CMS
readback. Install Pilot, consume the captured events, and validate actual OTLP
output separately before claiming complete end-to-end delivery.

For actual OTLP/HTTP wire capture, start the receiver with the baseline venv:

```bash
/tmp/qwenpaw-pilot-ab-example/baseline/venv/bin/python scripts/e2e/qwenpaw-parity/receiver.py \
  --root /tmp/qwenpaw-pilot-ab-example/otlp-wire
```

Its `endpoint.json` identifies the ephemeral loopback endpoint. Pass it to the
baseline runner using `--otlp-endpoint`, and configure the installed Pilot OTLP
trace exporter with the same URL and service name `qwenpaw-parity-pilot`. The
receiver preserves original protobuf bodies and decoded OTLP JSON, separated
by `service.name`. It supports HTTP chunked bodies and protobuf/JSON, binds only
to loopback, and records transport content headers without authorization headers.

Compare both actual OTLP wire exports with:

```bash
python3 scripts/e2e/qwenpaw-parity/compare.py \
  --baseline-wire /tmp/qwenpaw-pilot-ab-example/otlp-wire/qwenpaw-parity-baseline.jsonl \
  --pilot-wire /tmp/qwenpaw-pilot-ab-example/otlp-wire/qwenpaw-parity-pilot-qwenpaw.jsonl \
  --output /tmp/qwenpaw-pilot-ab-example/wire-comparison.json
```

Wire comparison checks foreground parent edges and session isolation, nonempty
LLM content, token presence, streaming TTFT bounds, absent nonstream TTFT,
provider/tool errors, cancellation, and Dream owner attribution. Its field-gap
section lists every baseline attribute absent from Pilot for review; it does
not silently treat legacy `copaw.*` keys as required product behavior. Independent
Dream executions may use different numbers of reasoning/tool calls. A known
baseline cancellation child interval can exceed its already-ended ENTRY; such
baseline timing findings are reported separately from Pilot validation.

For a cancellation-only regression, use `--cancel-only` without `--matrix`.
For an isolated real Skill call, use `--skill-only` without `--matrix`. It creates
and enables a synthetic skill through QwenPaw's native SkillService, asks the
real model to invoke the Skill tool, and captures name, runtime-scoped id,
description, and version. Use a separate run root and wire receiver directory
for supplemental scenarios so they do not change the main matrix's counts.
The runner explicitly sets `LOONGSUITE_PILOT_DATA_DIR` to the run's isolated
home, overriding an installed plugin's managed deployment marker. If a wire
file contains a separately run supplemental session, `--exclude-session NAME`
selects the main matrix and records that exclusion in the comparison report.

For a larger real-model workload, use `--extended` in a fresh run root. It sends
six concurrent requests with distinct sessions and response markers, three
successive turns in one session that refer to the previous response, one request
to read three synthetic files, and one request to read an index followed by the
file named in that index. These eleven requests use the official Runtime and
provider; tools only read isolated synthetic files. Scenario results include
the expected response markers for independent verification. Model/tool call
counts can differ between independent runs; compare actual content, coverage,
parent relationships, and telemetry completeness before treating count
differences as a defect.
