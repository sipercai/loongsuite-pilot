import { describe, expect, it } from 'vitest';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { extractCodexTerminalTurn, extractCodexTranscriptMeta } from '../../src/inputs/codex-transcript/codex-transcript-extractor.js';
import { buildCodexTranscriptEntries } from '../../src/inputs/codex-transcript/codex-transcript-builder.js';
import { applyAgentContentPolicy } from '../../src/normalization/agent-content-policy.js';
import { maskAgentActivityEntry } from '../../src/mask/entry-masker.js';
import { OtlpTraceFlusher } from '../../src/flushers/otlp-trace-flusher.js';

function fixture(output: unknown) {
  const at = Date.parse('2026-11-06T01:00:00Z');
  const payloads = [
    ['session_meta', { id: 'fixture-session', model_provider: 'openai' }],
    ['turn_context', { turn_id: 'fixture-turn', model: 'fixture-model' }],
    ['event_msg', { type: 'task_started', turn_id: 'fixture-turn' }],
    ['response_item', { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'fixture request' }] }],
    ['response_item', { type: 'function_call', call_id: 'failed', name: 'fixture_failure', arguments: '{}' }],
    ['response_item', { type: 'function_call', call_id: 'ok', name: 'fixture_success', arguments: '{}' }],
    ['response_item', { type: 'function_call_output', call_id: 'failed', output }],
    ['response_item', { type: 'function_call_output', call_id: 'ok', output: 'ordinary error-looking text' }],
    ['event_msg', { type: 'token_count', info: { last_token_usage: { input_tokens: 100, output_tokens: 10, total_tokens: 110 } } }],
    ['event_msg', { type: 'agent_message', phase: 'final', message: 'done' }],
    ['event_msg', { type: 'task_complete', turn_id: 'fixture-turn', last_agent_message: 'done' }],
  ];
  const records = payloads.map(([type, payload], index) => ({ timestamp: new Date(at + index * 100).toISOString(), type, payload }));
  const turn = extractCodexTerminalTurn(records, extractCodexTranscriptMeta(records[0]), 'fixture-session', 'fixture-turn');
  expect(turn).not.toBeNull();
  return buildCodexTranscriptEntries(turn!).map(entry => ({ ...entry,
    'agentcore.run_id': 'fixture-run', 'agentcore.task_name': '任务, 名称=一',
  }));
}

describe('AgentCore Codex native result to OTLP', () => {
  for (const content of [true, false]) it(`preserves exact tool failure status with content=${content}`, async () => {
    const marker = JSON.stringify({ agentcore_tool_error: 'CODEX_TOOL_FAILED', message: 'Tool operation failed.' });
    const records = fixture(marker).map(entry => maskAgentActivityEntry(
      applyAgentContentPolicy(entry, { codex: { captureMessageContent: content } }), { mode: 'all', types: [] },
    ));
    const spans: ReadableSpan[] = [];
    const flusher = new OtlpTraceFlusher({ enabled: true, serviceName: 'fixture-codex',
      endpoints: [{ name: 'fixture', endpoint: 'http://127.0.0.1:4318' }], protocol: 'http/protobuf',
      spanAttributePassthroughPrefixes: ['agentcore.'], captureMessageContent: content,
    }, undefined, () => ({ export(exported, callback) { spans.push(...exported); callback({ code: 0 }); }, shutdown: async () => {} }));
    await flusher.sendBatch(records);
    await flusher.shutdown();
    const tools = spans.filter(span => span.attributes['gen_ai.span.kind'] === 'TOOL');
    expect(tools).toHaveLength(2);
    expect(tools.find(span => span.attributes['gen_ai.tool.call.id'] === 'failed')?.status.code).toBe(2);
    expect(tools.find(span => span.attributes['gen_ai.tool.call.id'] === 'failed')?.attributes['error.type']).toBe('CODEX_TOOL_FAILED');
    expect(tools.find(span => span.attributes['gen_ai.tool.call.id'] === 'ok')?.status.code).toBe(0);
    expect(spans.every(span => span.attributes['agentcore.run_id'] === 'fixture-run')).toBe(true);
    expect(spans.every(span => span.attributes['agentcore.task_name'] === '任务, 名称=一')).toBe(true);
    if (!content) expect(JSON.stringify(records)).not.toContain('agentcore_tool_error');
  });

  it('does not infer failure from surrounding prose or a different business marker', () => {
    for (const output of ['ordinary CODEX_TOOL_FAILED text', '{"agentcore_tool_error":"BUSINESS_VALUE"}']) {
      expect(fixture(output).find(entry => entry['gen_ai.tool.call.id'] === 'failed' && entry['event.name'] === 'tool.result'))
        .toMatchObject({ 'tool.result.status': 'success' });
    }
  });
});
