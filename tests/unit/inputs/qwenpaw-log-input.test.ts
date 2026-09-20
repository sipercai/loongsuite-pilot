import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import { QwenPawLogInput } from '../../../src/inputs/qwenpaw-log/qwenpaw-log-input.js';
import { StateStore } from '../../../src/checkpoints/state-store.js';

class Input extends QwenPawLogInput {
  collectOnce() { return this.collect(); }
}

describe('QwenPaw process logs', () => {
  let dir: string;
  beforeEach(async () => { dir = await fs.mkdtemp(path.join(os.tmpdir(), 'qwenpaw-input-')); });
  afterEach(async () => { await fs.rm(dir, { recursive: true, force: true }); });
  const record = (id: string) => ({
    'event.id': id, 'event.name': 'llm.response', time_unix_nano: '1700000000010000000',
    'gen_ai.agent.type': 'qwenpaw', 'gen_ai.session.id': 'session', 'gen_ai.turn.id': 'run',
    'agent.qwenpaw.span.id': '1111111111111111', 'agent.qwenpaw.parent.id': '2222222222222222',
    'agentcore.task_id': 'task-a', 'gen_ai.response.time_to_first_token': 9_000_000,
    'gen_ai.output.messages': [{ role: 'assistant', parts: [{ type: 'text', content: '真实输出' }] }],
  });
  const line = (id: string) => JSON.stringify(record(id)) + '\n';

  it('preserves native hierarchy, message, Task and nanosecond fields through normalization', async () => {
    await fs.writeFile(path.join(dir, 'qwenpaw-day-101.jsonl'), line('one'));
    const input = new Input({ sessionDir: dir, stateStore: new StateStore(path.join(dir, 'state.json')) });
    const [entry] = await input.collectOnce();
    expect(entry).toMatchObject(record('one'));
  });

  it('resumes independent process offsets from disk and waits for a complete final line', async () => {
    const statePath = path.join(dir, 'state.json');
    const state = new StateStore(statePath);
    const a = path.join(dir, 'qwenpaw-day-101.jsonl');
    const b = path.join(dir, 'qwenpaw-day-202.jsonl');
    await fs.writeFile(a, line('a') + JSON.stringify(record('partial')));
    await fs.writeFile(b, line('b'));
    await fs.writeFile(path.join(dir, 'other.jsonl'), line('ignored'));
    await fs.mkdir(path.join(dir, 'qwenpaw-directory.jsonl'));
    const input = new Input({ sessionDir: dir, stateStore: state });
    expect((await input.collectOnce()).map(e => e['event.id'])).toEqual(['a', 'b']);
    await state.save();
    const restored = new StateStore(statePath);
    await restored.load();
    const restarted = new Input({ sessionDir: dir, stateStore: restored });
    expect(await restarted.collectOnce()).toEqual([]);
    await fs.appendFile(a, '\n');
    await fs.appendFile(b, line('b2'));
    expect((await restarted.collectOnce()).map(e => e['event.id'])).toEqual(['partial', 'b2']);
    expect(await restarted.collectOnce()).toEqual([]);
  });

  it('ignores foreign-agent records in a matching file', async () => {
    await fs.writeFile(path.join(dir, 'qwenpaw-day-101.jsonl'), JSON.stringify({ ...record('foreign'), 'gen_ai.agent.type': 'qoder' }) + '\n');
    expect(await new Input({ sessionDir: dir, stateStore: new StateStore(path.join(dir, 'state.json')) }).collectOnce()).toEqual([]);
  });
});
