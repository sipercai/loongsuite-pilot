import { afterEach, describe, expect, it } from 'vitest';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  invocationContextPath,
  parseInvocationAttributes,
  putInvocationContext,
} from '../../../src/cli/invocation-context.js';

const agentId = 'qoder-cn';
const messageUuid = '8db9c076-fdad-4ee7-8fe3-39fa15cf1fe0';
let dataDir: string | undefined;

afterEach(async () => {
  if (dataDir) await fs.rm(dataDir, { recursive: true, force: true });
  dataDir = undefined;
});

async function newDataDir(): Promise<string> {
  dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'pilot-invocation-context-'));
  return dataDir;
}

describe('invocation context store', () => {
  it('writes an atomically-addressable, private context that the Qoder Hook can read', async () => {
    const root = await newDataDir();
    const status = await putInvocationContext({
      dataDir: root,
      agentId,
      messageUuid,
      spanAttributes: {
        'agentcore.task_id': 'task-a',
        'agentcore.subtask_id': 'subtask-a',
      },
      now: new Date('2026-09-04T00:00:00.000Z'),
    });

    expect(status).toBe('stored');
    const file = invocationContextPath(root, agentId, messageUuid);
    expect(JSON.parse(await fs.readFile(file, 'utf8'))).toMatchObject({
      version: 1,
      agent_id: agentId,
      message_uuid: messageUuid,
      span_attributes: {
        'agentcore.task_id': 'task-a',
        'agentcore.subtask_id': 'subtask-a',
      },
    });
    expect((await fs.stat(file)).mode & 0o777).toBe(0o600);

    const { readInvocationSpanAttributes } = await import('../../../assets/hooks/shared/invocation-context.mjs');
    expect(readInvocationSpanAttributes({
      agentId,
      messageUuid,
      dataDir: root,
      now: Date.parse('2026-09-04T00:00:01.000Z'),
    })).toEqual({
      'agentcore.task_id': 'task-a',
      'agentcore.subtask_id': 'subtask-a',
    });
  });

  it('is idempotent for identical data and rejects a conflicting reuse of one UUID', async () => {
    const root = await newDataDir();
    const attributes = { 'agentcore.task_id': 'task-a' };
    await putInvocationContext({ dataDir: root, agentId, messageUuid, spanAttributes: attributes });
    await expect(putInvocationContext({
      dataDir: root, agentId, messageUuid, spanAttributes: attributes,
    })).resolves.toBe('unchanged');
    await expect(putInvocationContext({
      dataDir: root,
      agentId,
      messageUuid,
      spanAttributes: { 'agentcore.task_id': 'task-b' },
    })).rejects.toThrow('already exists with different attributes');
  });

  it('does not let concurrent conflicting writers overwrite one UUID', async () => {
    const root = await newDataDir();
    const results = await Promise.allSettled([
      putInvocationContext({
        dataDir: root, agentId, messageUuid, spanAttributes: { 'agentcore.task_id': 'task-a' },
      }),
      putInvocationContext({
        dataDir: root, agentId, messageUuid, spanAttributes: { 'agentcore.task_id': 'task-b' },
      }),
    ]);
    expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
  });

  it('allows replacement after TTL expiry and Hook reads do not consume the context', async () => {
    const root = await newDataDir();
    await putInvocationContext({
      dataDir: root,
      agentId,
      messageUuid,
      spanAttributes: { 'agentcore.task_id': 'task-a' },
      ttlMs: 1,
      now: new Date('2026-09-04T00:00:00.000Z'),
    });
    const { readInvocationSpanAttributes } = await import('../../../assets/hooks/shared/invocation-context.mjs');
    expect(readInvocationSpanAttributes({
      agentId, messageUuid, dataDir: root, now: Date.parse('2026-09-04T00:00:02.000Z'),
    })).toEqual({});
    await expect(putInvocationContext({
      dataDir: root,
      agentId,
      messageUuid,
      spanAttributes: { 'agentcore.task_id': 'task-b' },
      now: new Date('2026-09-04T00:00:02.000Z'),
    })).resolves.toBe('stored');
  });

  it('fails open when a persisted context has an invalid expiry', async () => {
    const root = await newDataDir();
    const file = invocationContextPath(root, agentId, messageUuid);
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(file, JSON.stringify({
      version: 1,
      agent_id: agentId,
      message_uuid: messageUuid,
      span_attributes: { 'agentcore.task_id': 'task-a' },
      created_at: '2026-09-04T00:00:00.000Z',
      expires_at: 'not-a-date',
    }));
    const { readInvocationSpanAttributes } = await import('../../../assets/hooks/shared/invocation-context.mjs');
    expect(readInvocationSpanAttributes({ agentId, messageUuid, dataDir: root })).toEqual({});
  });

  it('accepts task attributes but rejects protected, sensitive, and malformed fields', () => {
    expect(parseInvocationAttributes({ 'agentcore.task_id': 'task-a' })).toEqual({ 'agentcore.task_id': 'task-a' });
    expect(() => parseInvocationAttributes({ 'gen_ai.session.id': 'override' })).toThrow('not allowed');
    expect(() => parseInvocationAttributes({ 'agentcore.token': 'secret' })).toThrow('not allowed');
    expect(() => parseInvocationAttributes({ '../path': 'value' })).toThrow('invalid span attribute key');
  });

  it('also validates exported store inputs before using them in a path', async () => {
    const root = await newDataDir();
    await expect(putInvocationContext({
      dataDir: root,
      agentId: '../qoder-cn',
      messageUuid,
      spanAttributes: { 'agentcore.task_id': 'task-a' },
    })).rejects.toThrow('invalid agent id');
  });
});
