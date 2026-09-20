"""Focused native-ABI tests. Run with QwenPaw 2.1.0 / AgentScope 2.0.4.post1.

The runtime E2E creates the representative native records; these tests isolate
stream ownership, concurrent scope and final-close regressions found there.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("pilot_qwenpaw", ROOT / "assets/plugins/qwenpaw/loongsuite-pilot/plugin.py")
plugin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = plugin
SPEC.loader.exec_module(plugin)

from agentscope.message import Msg, TextBlock, ThinkingBlock, ToolCallBlock, ToolResultBlock
from agentscope.formatter import DashScopeChatFormatter, OpenAIChatFormatter
from agentscope.model import ChatResponse, ChatUsage
from agentscope.tool import ToolResponse, ToolChunk
from agentscope.event import ToolCallStartEvent, TextBlockDeltaEvent


class Capture:
    def __init__(self):
        self.records = []
    def write(self, value):
        self.records.append(value)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.capture = Capture()
        self.old_writer = plugin._writer
        plugin._writer = self.capture
        self.token = plugin._scope.set(None)
        self.agent = NS(name="test", _system_prompt="system", model=NS(model="model"), toolkit=NS())
        self.scope = plugin.Scope({"gen_ai.session.id": "session", "gen_ai.turn.id": "turn"})
        self.middleware = plugin.PilotMiddleware(self.scope)

    def tearDown(self):
        plugin._writer = self.old_writer
        plugin._scope.reset(self.token)

    async def test_nonstream_has_usage_but_no_fake_ttft(self):
        response = ChatResponse([TextBlock(text="done")], True, usage=ChatUsage(12, 4, 1.0, cache_input_tokens=7))
        async def handler(**kwargs):
            return response
        actual = await self.middleware.on_model_call(self.agent, {"messages": [Msg(name="u", content=[TextBlock(text="hi")], role="user")]}, handler)
        self.assertIs(response, actual)
        request, result = self.capture.records
        self.assertEqual(request["gen_ai.step.id"], result["gen_ai.step.id"])
        self.assertEqual(result["gen_ai.usage.cache_read.input_tokens"], 7)
        self.assertNotIn("gen_ai.response.time_to_first_token", result)

    async def test_llm_input_respects_native_formatter_without_mutating_messages(self):
        messages = [Msg(name="assistant", role="assistant", content=[ThinkingBlock(thinking="reasoning"), TextBlock(text="visible")])]
        response = ChatResponse([ThinkingBlock(thinking="new reasoning"), TextBlock(text="answer")], True)
        async def handler(**kwargs):
            self.assertIs(kwargs["messages"], messages)
            self.assertIsInstance(messages[0].content[0], ThinkingBlock)
            return response
        cases = [(DashScopeChatFormatter(), False),
                 (DashScopeChatFormatter(input_types=["text/plain", "application/x-thinking"]), True),
                 (OpenAIChatFormatter(), True), (None, True)]
        for formatter, include_reasoning in cases:
            with self.subTest(formatter=type(formatter).__name__, include_reasoning=include_reasoning):
                self.capture.records.clear()
                model = NS(model="qwen", formatter=formatter)
                self.assertIs(await self.middleware.on_model_call(self.agent, {"current_model": model, "messages": messages}, handler), response)
                request, result = self.capture.records
                parts = request["gen_ai.input.messages"][0]["parts"]
                self.assertEqual([p["type"] for p in parts], ["reasoning", "text"] if include_reasoning else ["text"])
                self.assertEqual(result["gen_ai.output.messages"][0]["parts"][0]["type"], "reasoning")

    async def test_cancel_preserves_entry_deltas_without_inventing_agent_output(self):
        plugin._boundary(self.scope, "turn")
        async def reply(**kwargs):
            yield TextBlockDeltaEvent(reply_id="r", block_id="b", delta="par")
            yield TextBlockDeltaEvent(reply_id="r", block_id="b", delta="tial")
        stream = self.middleware.on_reply(self.agent, {}, reply)
        await anext(stream)
        await anext(stream)
        plugin._finish_request(self.scope, asyncio.CancelledError("stop"))
        await stream.aclose()
        entry = next(r for r in self.capture.records if r.get("agent.qwenpaw.boundary") == "entry.end")
        agent = next(r for r in self.capture.records if r.get("agent.qwenpaw.boundary") == "agent.end")
        self.assertEqual(entry["gen_ai.output.messages"][0]["parts"], [{"type": "text", "content": "partial"}])
        self.assertEqual(entry["response.finish_reasons"], "interrupted")
        self.assertEqual(agent["gen_ai.output.messages"], [])

    async def test_complete_reply_replaces_partial_and_tool_boundary_resets_text(self):
        async def reply(**kwargs):
            yield TextBlockDeltaEvent(reply_id="r", block_id="b", delta="before tool")
            yield ToolCallStartEvent(reply_id="r", tool_call_id="t", tool_call_name="read_file")
            yield TextBlockDeltaEvent(reply_id="r", block_id="c", delta="after")
            self.assertEqual(self.scope.output[0]["parts"][0]["content"], "after")
            yield Msg(name="test", role="assistant", content=[TextBlock(text="final")])
        async for _ in self.middleware.on_reply(self.agent, {}, reply):
            pass
        self.assertEqual(self.scope.output[0]["parts"][0]["content"], "final")

    async def test_nested_helper_partial_does_not_replace_entry_output(self):
        self.scope.output = [{"role": "assistant", "parts": [{"type": "text", "content": "visible"}]}]
        tool = plugin.Scope({}, parent=self.scope)
        token = plugin._scope.set(tool)
        async def reply(**kwargs):
            yield TextBlockDeltaEvent(reply_id="helper", block_id="b", delta="hidden helper")
        stream = self.middleware.on_reply(self.agent, {}, reply)
        try:
            await anext(stream)
            await stream.aclose()
        finally:
            plugin._scope.reset(token)
        self.assertEqual(self.scope.output[0]["parts"][0]["content"], "visible")

    async def test_completed_tool_generator_close_is_success(self):
        response = ToolResponse(content=[TextBlock(text="value")])
        closed = []
        async def handler(**kwargs):
            try:
                yield response
            finally:
                closed.append(True)
        stream = self.middleware.on_acting(self.agent, {"tool_call": ToolCallBlock(id="call", name="read_file", input="{}")}, handler)
        self.assertIs(await anext(stream), response)
        await stream.aclose()
        result = self.capture.records[-1]
        self.assertEqual(result["tool.result.status"], "success")
        self.assertNotIn("error.type", result)
        self.assertEqual(closed, [True])
        self.assertIsNone(plugin._scope.get())

    async def test_partial_model_close_records_cancel_and_keeps_original_chunk(self):
        chunk = ChatResponse([TextBlock(text="partial")], False)
        closed = []
        async def source():
            try:
                yield chunk
                yield ChatResponse([TextBlock(text="done")], True)
            finally:
                closed.append(True)
        async def handler(**kwargs):
            return source()
        stream = await self.middleware.on_model_call(self.agent, {}, handler)
        self.assertIs(await anext(stream), chunk)
        self.assertIsNone(plugin._scope.get())
        await stream.aclose()
        result = self.capture.records[-1]
        self.assertTrue(result["agent.qwenpaw.cancelled"])
        self.assertEqual(result["response.finish_reasons"], "interrupted")
        self.assertGreater(result["gen_ai.response.time_to_first_token"], 0)
        self.assertEqual(closed, [True])

    async def test_completed_model_close_is_not_cancelled(self):
        async def source():
            yield ChatResponse([TextBlock(text="done")], True)
        async def handler(**kwargs):
            return source()
        stream = await self.middleware.on_model_call(self.agent, {}, handler)
        await anext(stream)
        await stream.aclose()
        self.assertNotIn("error.type", self.capture.records[-1])

    async def test_concurrent_requests_keep_their_scopes(self):
        gate = asyncio.Event()
        entered = []
        async def run(name):
            scope = plugin.Scope({"gen_ai.session.id": name, "gen_ai.turn.id": name})
            mw = plugin.PilotMiddleware(scope)
            agent = NS(name=name, _system_prompt="", state=NS(session_id=name))
            async def handler(**kwargs):
                self.assertEqual(plugin._scope.get().fields["gen_ai.session.id"], name)
                entered.append(name)
                if len(entered) == 2:
                    gate.set()
                await gate.wait()
                self.assertEqual(plugin._scope.get().fields["gen_ai.session.id"], name)
                yield Msg(name=name, content=[TextBlock(text=name)], role="assistant")
            async for _ in mw.on_reply(agent, {}, handler):
                self.assertIsNone(plugin._scope.get())
        await asyncio.gather(run("first"), run("second"))
        ends = [r for r in self.capture.records if r.get("agent.qwenpaw.boundary") == "agent.end"]
        self.assertEqual(len(ends), 2)
        for row in ends:
            self.assertEqual(row["gen_ai.output.messages"][0]["parts"][0]["content"], row["gen_ai.session.id"])

    async def test_provider_error_propagates_and_is_recorded_once(self):
        failure = RuntimeError("provider failure")
        async def handler(**kwargs):
            raise failure
        with self.assertRaises(RuntimeError) as caught:
            await self.middleware.on_model_call(self.agent, {}, handler)
        self.assertIs(caught.exception, failure)
        self.assertEqual([r["event.name"] for r in self.capture.records], ["llm.request", "llm.response"])
        self.assertEqual(self.capture.records[-1]["error.type"], "RuntimeError")

    async def test_raw_tool_error_is_not_success(self):
        async def handler(**kwargs):
            yield ToolResponse(content=[TextBlock(text="missing")], state="error")
        async for _ in self.middleware.on_acting(self.agent, {"tool_call": ToolCallBlock(id="call", name="read_file", input="{}")}, handler):
            pass
        self.assertEqual(self.capture.records[-1]["error.type"], "ToolError")

    async def test_terminal_tool_chunk_error_survives_native_close(self):
        async def handler(**kwargs):
            yield ToolChunk(content=[TextBlock(text="missing file")], state="error")
        stream = self.middleware.on_acting(self.agent, {"tool_call": ToolCallBlock(id="bad", name="read_file", input="{}")}, handler)
        await anext(stream)
        await stream.aclose()
        result = self.capture.records[-1]
        self.assertEqual(result["error.type"], "ToolError")
        self.assertNotIn("agent.qwenpaw.cancelled", result)
        self.assertEqual(result["gen_ai.tool.call.result"][0], {"type": "text", "content": "missing file"})

    async def test_request_cancel_closes_children_before_terminal_once(self):
        plugin._boundary(self.scope, "turn")
        async def model(**kwargs):
            async def chunks():
                yield ChatResponse([TextBlock(text="partial")], False)
                yield ChatResponse([TextBlock(text="done")], True)
            return chunks()
        async def reply(**kwargs):
            stream = await self.middleware.on_model_call(self.agent, {}, model)
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()
        stream = self.middleware.on_reply(self.agent, {}, reply)
        await anext(stream)
        plugin._finish_request(self.scope, asyncio.CancelledError())
        records_at_end = len(self.capture.records)
        self.assertEqual(self.capture.records[-1]["agent.qwenpaw.boundary"], "entry.end")
        await stream.aclose()
        self.assertEqual(len(self.capture.records), records_at_end)
        self.assertEqual(len([r for r in self.capture.records if r["event.name"] == "llm.response"]), 1)
        self.assertTrue(all(c.ended for c in self.scope.children))


    async def test_react_step_contains_acting_until_next_reasoning_and_reply_end(self):
        tool_event = ToolCallStartEvent(reply_id="r", tool_call_id="tool", tool_call_name="read_file")
        text_event = TextBlockDeltaEvent(reply_id="r", block_id="text", delta="done")
        async def first_reasoning(**kwargs):
            yield tool_event
        async def final_reasoning(**kwargs):
            yield text_event
            self.assertIsNotNone(self.scope.first)  # Observed native delta, before final Msg.
            yield Msg(name="test", content=[TextBlock(text="done")], role="assistant")
        async def acting(**kwargs):
            yield ToolResponse(content=[TextBlock(text="file")])
        async def reply(**kwargs):
            async for item in self.middleware.on_reasoning(self.agent, {}, first_reasoning):
                yield item
            self.assertFalse(any(r.get("agent.qwenpaw.boundary") == "step.end" for r in self.capture.records))
            tool = ToolCallBlock(id="tool", name="read_file", input="{}")
            async for item in self.middleware.on_acting(self.agent, {"tool_call": tool}, acting):
                yield item
            self.assertFalse(any(r.get("agent.qwenpaw.boundary") == "step.end" for r in self.capture.records))
            async for item in self.middleware.on_reasoning(self.agent, {}, final_reasoning):
                yield item
        async for _ in self.middleware.on_reply(self.agent, {}, reply):
            pass
        steps = [r for r in self.capture.records if r.get("agent.qwenpaw.boundary") == "step.end"]
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["response.finish_reasons"], "tool_calls")
        self.assertEqual(steps[1]["response.finish_reasons"], "stop")
        tool_start, tool_end = [r for r in self.capture.records if r["event.name"] in ("tool.call", "tool.result")]
        self.assertEqual(tool_start["agent.qwenpaw.parent.id"], steps[0]["agent.qwenpaw.span.id"])
        self.assertLessEqual(int(tool_end["time_unix_nano"]), int(steps[0]["time_unix_nano"]))
        starts = [r for r in self.capture.records if r.get("agent.qwenpaw.boundary") == "step.start"]
        self.assertLessEqual(int(steps[0]["time_unix_nano"]), int(starts[1]["time_unix_nano"]))
        self.assertIsNotNone(self.scope.first)
        self.assertEqual(self.scope.fields["gen_ai.agent.name"], "test")
        self.assertTrue(all(c.ended for c in self.scope.children))

    async def test_cancel_during_acting_closes_tool_step_agent_before_entry(self):
        plugin._boundary(self.scope, "turn")
        async def reasoning(**kwargs):
            yield ToolCallStartEvent(reply_id="r", tool_call_id="tool", tool_call_name="read_file")
        async def acting(**kwargs):
            yield ToolChunk(content=[TextBlock(text="partial")])
            yield ToolResponse(content=[TextBlock(text="complete")])
        async def reply(**kwargs):
            async for item in self.middleware.on_reasoning(self.agent, {}, reasoning):
                yield item
            tool = ToolCallBlock(id="tool", name="read_file", input="{}")
            stream = self.middleware.on_acting(self.agent, {"tool_call": tool}, acting)
            try:
                async for item in stream:
                    yield item
            finally:
                await stream.aclose()
        stream = self.middleware.on_reply(self.agent, {}, reply)
        await anext(stream)
        await anext(stream)
        plugin._finish_request(self.scope, asyncio.CancelledError())
        closed_records = list(self.capture.records)
        await stream.aclose()
        self.assertEqual(self.capture.records, closed_records)
        ends = [r for r in closed_records if r["event.name"] == "tool.result" or str(r.get("agent.qwenpaw.boundary", "")).endswith(".end")]
        self.assertEqual([r.get("agent.qwenpaw.boundary", r["event.name"]) for r in ends], ["tool.result", "step.end", "agent.end", "entry.end"])
        self.assertTrue(all(r["error.type"] == "CancelledError" for r in ends))
        self.assertTrue(all(c.ended for c in self.scope.children))

    async def test_native_constructor_attachment_is_once_and_reversible(self):
        from agentscope.agent import Agent
        owner = plugin.PilotPlugin()
        original = Agent.__init__
        try:
            owner._attach_helpers()
            existing = plugin.PilotMiddleware()
            explicit = Agent("explicit", "system", NS(), None, [existing])
            helper = Agent("helper", "system", NS())
            self.assertEqual(explicit._reply_middlewares, [existing])
            self.assertEqual(len(helper._reply_middlewares), 1)
            self.assertIsInstance(helper._reply_middlewares[0], plugin.PilotMiddleware)
        finally:
            await owner.shutdown()
        self.assertIs(Agent.__init__, original)


    async def test_existing_helper_middleware_bypasses_all_hooks_after_shutdown(self):
        from agentscope.agent import Agent
        owner = plugin.PilotPlugin()
        owner._attach_helpers()
        agent = Agent("existing", "system", NS())
        middleware = agent._reply_middlewares[0]
        await owner.shutdown()
        value = Msg(name="existing", content=[TextBlock(text="business continues")], role="assistant")
        async def stream(**kwargs):
            yield value
        async def model(**kwargs):
            return value
        for hook in (middleware.on_reply, middleware.on_reasoning, middleware.on_acting):
            self.assertEqual([item async for item in hook(agent, {}, stream)], [value])
        self.assertIs(await middleware.on_model_call(agent, {}, model), value)
        self.assertEqual(self.capture.records, [])

    async def test_shutdown_suppresses_events_from_an_already_suspended_reply(self):
        owner = plugin.PilotPlugin()
        middleware = plugin.PilotMiddleware(owner=owner)
        async def reply(**kwargs):
            yield Msg(name="a", content=[TextBlock(text="first")], role="assistant")
            yield Msg(name="a", content=[TextBlock(text="last")], role="assistant")
        stream = middleware.on_reply(self.agent, {}, reply)
        await anext(stream)
        before_shutdown = len(self.capture.records)
        self.assertGreater(before_shutdown, 0)
        await owner.shutdown()
        await anext(stream)
        await stream.aclose()
        self.assertEqual(len(self.capture.records), before_shutdown)

    async def test_shutdown_suppresses_suspended_tool_without_request_parent(self):
        owner = plugin.PilotPlugin()
        middleware = plugin.PilotMiddleware(owner=owner)
        async def handler(**kwargs):
            yield ToolResponse(content=[TextBlock(text="result")])
        tool = ToolCallBlock(id="direct", name="read_file", input="{}")
        stream = middleware.on_acting(self.agent, {"tool_call": tool}, handler)
        await anext(stream)
        self.assertEqual(len(self.capture.records), 1)
        await owner.shutdown()
        await stream.aclose()
        self.assertEqual(len(self.capture.records), 1)

    async def test_skill_tool_records_native_frontmatter_and_cached_description(self):
        from agentscope.skill import Skill
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "workspaces" / "worker" / "skills" / "witness"
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text("---\nname: witness\ndescription: From frontmatter\nmetadata:\n  version: 1.2.3\n---\nBody")
            skill = Skill("witness", "Cached description", str(directory), "Body", 0)
            self.agent.toolkit = NS(_qp_skills={"witness": {"dir": str(directory)}},
                tool_groups=[NS(skills_or_loaders=[NS(_cache={"witness": skill})])])
            async def handler(**kwargs):
                yield ToolResponse(content=[TextBlock(text="skill body")])
            tool_call = ToolCallBlock(id="skill-call", name="Skill", input='{"skill":"witness"}')
            async for _ in self.middleware.on_acting(self.agent, {"tool_call": tool_call}, handler):
                pass
            for record in self.capture.records:
                self.assertEqual(record["gen_ai.skill.name"], "witness")
                self.assertEqual(record["gen_ai.skill.id"], "workspace:worker:witness")
                self.assertEqual(record["gen_ai.skill.description"], "Cached description")
                self.assertEqual(record["gen_ai.skill.version"], "1.2.3")


class ConversionTests(unittest.TestCase):
    def test_cache_none_alias_fallback_preserves_explicit_zero(self):
        for usage, expected in (({"cache_read_input_tokens": None, "cache_input_tokens": 9}, 9),
                                ({"cache_read_input_tokens": 0, "cache_input_tokens": 9}, 0),
                                ({"cache_input_tokens": 9, "prompt_tokens_details": {"cached_tokens": 3}}, 9),
                                ({"cache_read_input_tokens": None, "cache_input_tokens": None, "prompt_tokens_details": {"cached_tokens": 3}}, 3)):
            with self.subTest(usage=usage):
                self.assertEqual(plugin._usage(usage)["gen_ai.usage.cache_read.input_tokens"], expected)
        self.assertEqual(plugin._usage({"cache_read_input_tokens": None}), {})

    def test_provider_endpoint_wrappers_and_active_fallback(self):
        OpenAIChatModel = type("OpenAIChatModel", (), {})
        model = OpenAIChatModel()
        model.client = NS(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        wrapper = NS(_inner=NS(_model=model), base_url="https://api.openai.com/v1")
        self.assertEqual(plugin._provider(wrapper), "dashscope")
        model.client.base_url = "https://api.deepseek.com/v1"
        self.assertEqual(plugin._provider(wrapper), "deepseek")
        model.client.base_url = "https://proxy.example/v1"
        self.assertEqual(plugin._provider(wrapper), "unknown")
        model.qwenpaw_provider_id = " bailian "
        self.assertEqual(plugin._provider(wrapper), "dashscope")

    def test_provider_url_host_boundaries_and_unknown_defaults(self):
        for url in ("https://api.openai.com.evil.example", "https://api.openai.com@evil.example", "https://evil.example/api.openai.com", "file://api.openai.com", "https://[invalid"):
            with self.subTest(url=url):
                self.assertEqual(plugin._provider(NS(base_url=url)), "unknown")
        self.assertEqual(plugin._provider(NS(base_url="https://API.DEEPSEEK.COM./v1")), "deepseek")
        self.assertEqual(plugin._provider(NS(base_url="https://sub.api.moonshot.ai/v1")), "moonshot")
        self.assertEqual(plugin._provider(NS()), "unknown")

    def test_provider_native_mro_cycles_and_broken_metadata_are_safe(self):
        Native = type("DashScopeChatModel", (), {})
        self.assertEqual(plugin._provider(type("CustomModel", (Native,), {})()), "dashscope")
        Ollama = type("OllamaChatModel", (), {})
        local = Ollama()
        local.base_url = "http://localhost:11434"
        self.assertEqual(plugin._provider(local), "ollama")
        cycle = NS(_provider_id="anthropic")
        cycle._inner = cycle
        self.assertEqual(plugin._provider(cycle), "anthropic")
        class Broken:
            @property
            def client(self):
                raise RuntimeError("metadata unavailable")
        self.assertEqual(plugin._provider(Broken()), "unknown")

    def test_task_forwarded_fallback_metadata_precedence_and_normalization(self):
        context = {"metadata": {"agui": {"runId": "run"}}, "forwardedProps": {"agentteams": {
            "taskId": " task ", "subtaskId": " sub ", "taskName": " Task ", "subtaskName": " Sub ",
            "delegatedFromTaskId": " origin ", "delegatedFromSubTaskId": " parent ",
        }}}
        self.assertEqual(plugin._task_fields(context), {
            "agentcore.task_id": "task", "agentcore.subtask_id": "sub", "agentcore.task_name": "Task", "agentcore.subtask_name": "Sub",
            "agentcore.delegated_from_task_id": "origin", "agentcore.delegated_from_subtask_id": "parent",
        })
        context["metadata"]["agentteams"] = {"taskId": "preferred"}
        self.assertEqual(plugin._task_fields(context)["agentcore.task_id"], "preferred")
        self.assertEqual(plugin._task_fields(context)["agentcore.subtask_id"], "null")

    def test_task_names_and_delegation_require_valid_own_ids(self):
        task = {"taskId": "  ", "subtaskId": 42, "taskName": "orphan", "subtaskName": "orphan", "delegatedFromTaskId": "origin"}
        self.assertEqual(set(plugin._task_fields({"metadata": {"agentteams": task}}).values()), {"null"})
        task.update(taskId="task", taskName="x" * 600)
        fields = plugin._task_fields({"metadata": {"agentteams": task}})
        self.assertEqual(len(fields["agentcore.task_name"]), 512)
        self.assertEqual(fields["agentcore.subtask_name"], "null")

    def test_tool_result_native_block_list_uses_genai_parts(self):
        block = ToolResultBlock(id="read", name="read_file", output=[TextBlock(text="file content")])
        parts = plugin._parts([block])
        self.assertEqual(parts, [{"type": "tool_call_response", "id": "read", "response": [{"type": "text", "content": "file content"}]}])
        messages = plugin._messages(Msg(name="a", role="assistant", content=[block]))
        self.assertEqual(messages[0]["role"], "tool")
        self.assertEqual(messages[0]["parts"], parts)

    def test_tool_result_json_map_and_scalars_keep_their_shapes(self):
        for value in ({"content": "application-specific", "count": 2}, "plain", 3, None):
            self.assertEqual(plugin._tool_response_parts(value), value)

    def test_skill_manifest_version_fallback_and_native_only_directory(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "workspaces" / "default" / "skills" / "witness"
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text("---\nname: witness\ndescription: From disk\n---\nBody")
            (directory.parent.parent / "skill.json").write_text(json.dumps({"skills": {"witness": {"metadata": {"version_text": "9.0"}}}}))
            agent = NS(toolkit=NS(_qp_skills={"witness": {"dir": str(directory)}}))
            fields = plugin._skill_fields(agent, {"skill": "witness"})
            self.assertEqual(fields["gen_ai.skill.id"], "workspace:default:witness")
            self.assertEqual(fields["gen_ai.skill.description"], "From disk")
            self.assertEqual(fields["gen_ai.skill.version"], "9.0")



class WriterTests(unittest.TestCase):
    def test_agent_output_override_and_global_enabled_gate(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOONGSUITE_PILOT_DATA_DIR": root, "AGENT_DATA_COLLECTION_CONFIG": str(Path(root) / "config.json")}):
            config = Path(root) / "config.json"
            writer = plugin.JsonlWriter()
            value = {"agents": {"qwenpaw": {"enabled": True, "collectTrace": True}}, "collectLog": False, "collectTrace": False}
            config.write_text(json.dumps(value))
            writer.write({"test": "agent override"})
            value["enabled"] = False
            config.write_text(json.dumps(value))
            writer.write({"test": "globally disabled"})
            value["enabled"] = True
            value.update(collectLog=True, collectTrace=True)
            value["agents"]["qwenpaw"].update(collectLog=False, collectTrace=False)
            config.write_text(json.dumps(value))
            writer.write({"test": "agent outputs disabled"})
            self.assertEqual(json.loads(next(Path(root).rglob("*.jsonl")).read_text()), {"test": "agent override"})

    def test_explicit_output_off_on_is_dynamic_without_unloading(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOONGSUITE_PILOT_DATA_DIR": root, "AGENT_DATA_COLLECTION_CONFIG": str(Path(root) / "config.json")}):
            config = Path(root) / "config.json"
            value = {"agents": {"qwenpaw": {"enabled": True}}, "collectLog": False, "collectTrace": False}
            config.write_text(json.dumps(value))
            writer = plugin.JsonlWriter()
            writer.write({"test": "off"})
            self.assertEqual(list(Path(root).rglob("*.jsonl")), [])
            value["collectTrace"] = True
            config.write_text(json.dumps(value))
            writer.write({"test": "on"})
            output = next(Path(root).rglob("*.jsonl"))
            value["collectTrace"] = False
            config.write_text(json.dumps(value))
            writer.write({"test": "off again"})
            self.assertEqual(json.loads(output.read_text()), {"test": "on"})

    def test_output_gate_preserves_unspecified_defaults(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOONGSUITE_PILOT_DATA_DIR": root, "AGENT_DATA_COLLECTION_CONFIG": str(Path(root) / "config.json")}):
            config = Path(root) / "config.json"
            writer = plugin.JsonlWriter()
            for value in ({"agents": {"qwenpaw": {}}, "collectLog": False}, {"collectLog": False, "collectTrace": False}, {"agents": {"qwenpaw": {}}, "collectLog": True, "collectTrace": False}):
                config.write_text(json.dumps(value))
                writer.write({"test": "default"})
            self.assertEqual(len(next(Path(root).rglob("*.jsonl")).read_text().splitlines()), 3)
    def test_private_jsonl_and_distinct_ids(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOONGSUITE_PILOT_DATA_DIR": root}):
            writer = plugin.JsonlWriter()
            writer.write({"test": "value"})
            output = next(Path(root).rglob("*.jsonl"))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
            self.assertEqual(json.loads(output.read_text()), {"test": "value"})

    def test_capture_off_removes_content_and_disable_stops_writes(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOONGSUITE_PILOT_DATA_DIR": root}):
            config = Path(root) / "config.json"
            config.write_text(json.dumps({"agents": {"qwenpaw": {"captureMessageContent": False}}}))
            writer = plugin.JsonlWriter()
            writer.write({"event.name": "llm.request", "gen_ai.input.messages": ["private"], "error.message": "private"})
            output = next(Path(root).rglob("*.jsonl"))
            self.assertEqual(json.loads(output.read_text()), {"event.name": "llm.request"})
            config.write_text(json.dumps({"agents": {"qwenpaw": {"enabled": False}}}))
            writer.write({"event.name": "llm.response"})
            self.assertEqual(len(output.read_text().splitlines()), 1)


    def test_write_failure_is_fail_open(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"LOONGSUITE_PILOT_DATA_DIR": root}):
            with patch("os.open", side_effect=PermissionError("denied")):
                plugin.JsonlWriter().write({"test": "value"})


if __name__ == "__main__":
    unittest.main()
