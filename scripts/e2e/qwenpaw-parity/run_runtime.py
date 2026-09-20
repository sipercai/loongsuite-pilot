"""Run an isolated, real QwenPaw Runtime with a real provider.

Secrets are read from DASHSCOPE_API_KEY and only persisted in the isolated
QwenPaw secret directory. Outputs contain synthetic test conversations only.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import time


async def main(args):
    root = Path(args.root).resolve()
    for child in ("home", "working", "secret", "evidence"):
        (root / child).mkdir(parents=True, exist_ok=True)
    os.environ.update(HOME=str(root / "home"),
                      QWENPAW_WORKING_DIR=str(root / "working"),
                      QWENPAW_SECRET_DIR=str(root / "secret"),
                      PARITY_CONTRACT_LOG=str(root / "evidence/contract.jsonl"),
                      OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="SPAN_AND_EVENT",
                      OTEL_SEMCONV_STABILITY_OPT_IN="gen_ai_latest_experimental",
                      OTEL_METRICS_EXPORTER="none",
                      LOONGSUITE_PYTHON_SITE_BOOTSTRAP="false")
    if args.mode == "pilot":
        # A deployed plugin contains an explicit dataDir marker. Override it
        # for this isolated run even when loading the same installed artifact.
        os.environ["LOONGSUITE_PILOT_DATA_DIR"] = str(root / "home/.loongsuite-pilot")
    import importlib.metadata
    import hashlib
    metadata = {"mode": args.mode, "model": args.model, "source": "real QwenPaw Runtime / real DashScope",
                "versions": {name: importlib.metadata.version(name) for name in ("qwenpaw", "agentscope", "reme-ai")},
                "metrics_exporter": "none", "message_capture": "SPAN_AND_EVENT",
                "semconv_stability_opt_in": "gen_ai_latest_experimental"}
    if args.mode == "baseline":
        metadata["versions"].update({name: importlib.metadata.version(name) for name in ("loongsuite-instrumentation-qwenpaw", "loongsuite-instrumentation-agentscope", "loongsuite-otel-util-genai")})
        metadata["otlp_endpoint"] = args.otlp_endpoint
    if args.plugin:
        metadata["plugin_path"] = str(Path(args.plugin).resolve())
        metadata["pilot_data_dir"] = os.environ.get("LOONGSUITE_PILOT_DATA_DIR")
        metadata["plugin_sha256"] = hashlib.sha256((Path(args.plugin)/"plugin.py").read_bytes()).hexdigest()
    (root / "evidence/run-metadata.json").write_text(json.dumps(metadata, indent=2))
    provider = None
    if args.mode == "baseline":
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
        from opentelemetry.sdk.resources import Resource

        class Exporter(SpanExporter):
            def export(self, spans):
                with (root / "evidence/spans.jsonl").open("a") as output:
                    for span in spans:
                        output.write(json.dumps(json.loads(span.to_json())) + "\n")
                return SpanExportResult.SUCCESS

        provider = TracerProvider(resource=Resource.create({"service.name": "qwenpaw-parity-baseline", "parity.run.id": root.name}))
        provider.add_span_processor(SimpleSpanProcessor(Exporter()))
        if args.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=args.otlp_endpoint)))
        trace.set_tracer_provider(provider)
        from opentelemetry.instrumentation.qwenpaw import QwenPawInstrumentor
        from opentelemetry.instrumentation.agentscope import AgentScopeInstrumentor
        QwenPawInstrumentor().instrument()
        AgentScopeInstrumentor().instrument()

    from qwenpaw.app.migration import ensure_default_agent_exists
    from qwenpaw.config.config import load_agent_config, save_agent_config, ModelSlotConfig
    from qwenpaw.providers.provider_manager import ProviderManager
    from qwenpaw.app.app_services import AppServiceManager
    from qwenpaw.app.workspace_registry import WorkspaceRegistry
    from qwenpaw.app.workspace.bootstrap_factory import WorkspaceBootstrapFactory
    from qwenpaw.plugins.registry import PluginRegistry
    from qwenpaw.plugins.loader import PluginLoader
    from qwenpaw.schemas import AgentRequest

    ensure_default_agent_exists()
    config = load_agent_config("default")
    config.name = "QwenPaw parity"
    config.active_model = ModelSlotConfig(provider_id="dashscope", model=args.model)
    config.running.memory_manager_backend = "remelight"
    config.running.max_iters = 6
    config.running.llm_retry_enabled = False
    save_agent_config("default", config)
    pm = ProviderManager.get_instance()
    pm.update_provider("dashscope", {"api_key": os.environ["DASHSCOPE_API_KEY"]})
    workspace_dir = Path(config.workspace_dir)
    (workspace_dir / "PARITY_WITNESS.txt").write_text("PARITY_WITNESS_20260920\n")
    (workspace_dir / "AGENTS.md").write_text("Follow the user's task. Use tools when requested. Keep answers short.\n")
    if args.skill_only:
        from qwenpaw.agents.skill_system.workspace_service import SkillService
        SkillService(workspace_dir).create_skill(
            "parity-skill",
            "---\nname: parity-skill\ndescription: Return a synthetic telemetry acceptance marker.\nversion: 1.2.3\n---\n"
            "# Parity skill\nReply exactly PARITY_SKILL_20260920. This skill needs no other tools.\n",
            enable=True,
        )
    if args.extended:
        fixtures = workspace_dir / "extended-fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        for name, marker in (("part-one.txt", "EXTENDED_FILE_A_20260920"),
                             ("part-two.txt", "EXTENDED_FILE_B_20260920"),
                             ("part-three.txt", "EXTENDED_FILE_C_20260920")):
            (fixtures / name).write_text(marker + "\n")
        (fixtures / "lookup-target.txt").write_text("EXTENDED_INDEX_TARGET_20260920\n")
        (fixtures / "lookup.txt").write_text(str(fixtures / "lookup-target.txt") + "\n")

    services = AppServiceManager()
    await services.start()
    registry = WorkspaceRegistry(app_services=services,
        bootstrap_plugins_kwargs=WorkspaceBootstrapFactory.build_bootstrap_kwargs(services))
    PluginRegistry().set_workspace_manager(registry)
    loader = PluginLoader([root / "working/plugins"])
    await loader.load_plugin_from_path(Path(__file__).parent / "probe-plugin")
    if args.plugin:
        await loader.load_plugin_from_path(Path(args.plugin))
    workspace = await registry.get_agent("default")
    results = []

    async def run(case, prompt, session, stream=True, started_event=None, expected_markers=None):
        request = AgentRequest.model_validate({"session_id": session, "user_id": "parity-user",
            "stream": stream, "agent_id": "default", "channel": "console",
            "input": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]})
        started = time.time()
        events = []
        try:
            async for event in workspace.stream_query(request):
                data = event.model_dump(mode="json") if hasattr(event, "model_dump") else {"repr": str(event)}
                events.append(data)
                if started_event and data.get("delta") and (data.get("text") or data.get("data")):
                    started_event.set()
                with (root / "evidence/runtime-events.jsonl").open("a") as output:
                    output.write(json.dumps({"case": case, "session_id": session, "event": data}) + "\n")
            error = next((str(event.get("error")) for event in events if event.get("error")), None)
        except BaseException as exc:
            error = type(exc).__name__
        record = {"case": case, "session_id": session, "stream_request": stream,
                  "events": len(events), "error": error, "duration_s": time.time()-started}
        if expected_markers:
            record["expected_markers"] = expected_markers
        results.append(record)
        print(json.dumps(record), flush=True)

    try:
        if args.skill_only:
            await run("skill", "Call the Skill tool with skill='parity-skill' to load that skill, then follow its instructions. You must actually invoke the Skill tool.", "parity-skill")
        if not args.cancel_only and not args.skill_only and not args.extended:
            await run("tool_stream", f"Use the read_file tool to read {workspace_dir}/PARITY_WITNESS.txt. Reply with its exact contents. You must actually call the tool.", "parity-multiturn")
        if args.extended:
            await asyncio.gather(*(run(
                f"extended_concurrent_{index}",
                f"Reply exactly EXTENDED_CONCURRENT_{index:02d}_20260920. Do not use tools.",
                f"parity-extended-concurrent-{index:02d}",
                expected_markers=[f"EXTENDED_CONCURRENT_{index:02d}_20260920"],
            ) for index in range(1, 7)))
            session = "parity-extended-three-turns"
            marker = "EXTENDED_MEMORY_20260920"
            await run("extended_memory_1", f"Remember the exact value {marker} for this conversation. Reply only with that value. Do not use tools.", session, expected_markers=[marker])
            marker += "_SECOND"
            await run("extended_memory_2", "Take the exact value you replied in the previous turn and append _SECOND. Reply only with the resulting value. Do not use tools.", session, expected_markers=[marker])
            marker += "_THIRD"
            await run("extended_memory_3", "Take the exact value you replied in the previous turn and append _THIRD. Reply only with the resulting value. Do not use tools.", session, expected_markers=[marker])
            await run("extended_three_files",
                f"Use read_file to actually read all three files: {fixtures}/part-one.txt, {fixtures}/part-two.txt, and {fixtures}/part-three.txt. Reply with their exact contents, one per line in that order. Only use read_file; do not write or change any files.",
                "parity-extended-three-files",
                expected_markers=["EXTENDED_FILE_A_20260920", "EXTENDED_FILE_B_20260920", "EXTENDED_FILE_C_20260920"])
            await run("extended_index_chain",
                f"Use read_file to read {fixtures}/lookup.txt. Its content is the absolute path of another file. Then actually use read_file on that exact path and reply with the second file's exact contents. Only use read_file; do not write or change any files.",
                "parity-extended-index-chain", expected_markers=["EXTENDED_INDEX_TARGET_20260920"])
        if args.matrix:
            await run("second_turn", "What was the exact witness value from the previous turn? Reply only with that value.", "parity-multiturn")
            await run("nonstream_request", "Reply exactly PARITY_NONSTREAM.", "parity-nonstream", False)
            await asyncio.gather(run("concurrent_a", "Reply exactly PARITY_A.", "parity-a"),
                                 run("concurrent_b", "Reply exactly PARITY_B.", "parity-b"))
            await run("tool_failure", f"Use read_file to read {workspace_dir}/INTENTIONALLY_MISSING.txt, then explain the failure briefly.", "parity-tool-failure")
            config.active_model = ModelSlotConfig(provider_id="dashscope", model="parity-intentionally-invalid-model")
            save_agent_config("default", config)
            await run("model_failure", "Reply briefly.", "parity-model-failure")
            config.active_model = ModelSlotConfig(provider_id="dashscope", model=args.model)
            save_agent_config("default", config)
        if args.matrix or args.cancel_only:
            first_delta = asyncio.Event()
            cancelled = asyncio.create_task(run("cancel_stream", "Write the integers from 1 to 2000, one per line, with no omissions.", "parity-cancel", started_event=first_delta))
            try:
                await asyncio.wait_for(first_delta.wait(), 30)
                cancelled.cancel("client_stop")
            except asyncio.TimeoutError:
                cancelled.cancel("test_timeout")
            await asyncio.gather(cancelled, return_exceptions=True)
        if args.dream:
            import datetime
            day = datetime.date.today().isoformat()
            daily = workspace_dir / config.running.reme_light_memory_config.daily_dir / day
            daily.mkdir(parents=True, exist_ok=True)
            (daily / "parity-observation.md").write_text("# Parity observation\nThe user prefers concise technical reports. The deployment uses QwenPaw 2.1.0 and AgentScope 2.0.4.post1. Their test marker is PARITY_WITNESS_20260920.\n")
            started = time.time()
            try:
                await asyncio.wait_for(workspace.memory_manager.dream(date=day, hint="Extract only durable preferences; do not use web search."), 120)
                error = None
            except BaseException as exc:
                error = type(exc).__name__
            record = {"case": "dream", "error": error, "duration_s": time.time()-started}
            results.append(record)
            print(json.dumps(record), flush=True)
    finally:
        (root / "evidence/results.json").write_text(json.dumps(results, indent=2))
        await registry.stop_all()
        await services.stop()
        # asyncio.run normally closes generators after main returns; flush only
        # after cancelled AgentScope generators have finalized their child spans.
        await asyncio.get_running_loop().shutdown_asyncgens()
        if provider:
            provider.force_flush()
            provider.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--mode", choices=("baseline", "pilot", "probe"), required=True)
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--otlp-endpoint")
    parser.add_argument("--plugin")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--dream", action="store_true")
    parser.add_argument("--cancel-only", action="store_true")
    parser.add_argument("--skill-only", action="store_true")
    parser.add_argument("--extended", action="store_true")
    asyncio.run(main(parser.parse_args()))
