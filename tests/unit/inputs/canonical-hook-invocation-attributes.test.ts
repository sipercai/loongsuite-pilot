import { describe, expect, it } from 'vitest';
import { buildCanonicalHookEntry } from '../../../src/inputs/base/canonical-hook-record.js';

describe('structured invocation attributes', () => {
  for (const agent of ['qoder-cli', 'qoder-cn']) {
    it.each(['task-A', '任务甲', 'task=甲', 'task,甲', '测试,task=甲'])
    (`preserves JSON values for ${agent}: %s`, (value) => {
      const entry = buildCanonicalHookEntry({
        'event.name': 'llm.response', 'gen_ai.agent.type': agent,
        'agentcore.task_id': value, 'agentcore.subtask_id': `${value}-sub`,
      }, agent, undefined, { preserveSafeCustomTopLevelFields: true });
      expect(entry?.['agentcore.task_id']).toBe(value);
      expect(entry?.['agentcore.subtask_id']).toBe(`${value}-sub`);
    });
  }

  it('keeps opt-in, sensitive-key, malformed-key and size boundaries', () => {
    const record = {
      'event.name': 'llm.response', 'gen_ai.agent.type': 'qoder-cli',
      'agentcore.task_id': 'safe,task=甲', 'agentcore.password': 'blocked',
      'bad,key': 'blocked', 'bad=key': 'blocked', 'custom.long': 'a'.repeat(513),
      'custom.object': { value: 'blocked' },
    };
    expect(buildCanonicalHookEntry(record, 'qoder-cli')).not.toHaveProperty('agentcore.task_id');
    const entry = buildCanonicalHookEntry(record, 'qoder-cli', undefined, { preserveSafeCustomTopLevelFields: true });
    expect(entry?.['agentcore.task_id']).toBe('safe,task=甲');
    for (const key of ['agentcore.password', 'bad,key', 'bad=key', 'custom.long', 'custom.object']) {
      expect(entry?.[key]).toBeUndefined();
    }
  });
});
