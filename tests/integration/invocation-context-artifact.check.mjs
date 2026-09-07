// Built-artifact check (node:test), separate from the source Vitest suite.
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { copyFile, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readInvocationSpanAttributes } from '../../assets/hooks/shared/invocation-context.mjs';

let root;
before(async () => {
  root = await mkdtemp(join(tmpdir(), 'pilot-light-artifact-'));
  // Intentionally omit collector.js, native-deps-guard and node_modules.
  // The real built public entry must work without any of them.
  await writeFile(join(root, 'package.json'), '{"type":"module"}');
  await copyFile('dist/index.js', join(root, 'index.js'));
  await copyFile('dist/invocation-context.js', join(root, 'invocation-context.js'));
});
after(async () => { if (root) await rm(root, { recursive: true, force: true }); });

function run(args, input = '') {
  return spawnSync(process.execPath, [join(root, 'index.js'), ...args], {
    input, encoding: 'utf8', timeout: 5000,
    env: { ...process.env, NODE_OPTIONS: '', LOONGSUITE_PILOT_DATA_DIR: join(root, 'data'),
      LOONGSUITE_PILOT_INVOCATION_CONTEXT_ROOT: join(root, 'contexts') },
  });
}

for (const agentId of ['qoder', 'qoder-cn']) {
  test(`built public CLI registers ${agentId} without collector dependencies`, async () => {
    const messageUuid = randomUUID();
    const attributes = { 'agentcore.task_id': 'task,甲=1', 'agentcore.subtask_id': 'subtask,乙=2' };
    const args = ['invocation-context', 'put', '--agent', agentId, '--message-uuid', messageUuid];
    const result = run(args, JSON.stringify(attributes));
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, '');
    assert.equal(JSON.parse(result.stdout).status, 'stored');
    const file = join(root, 'contexts', agentId, `${messageUuid}.json`);
    const context = JSON.parse(await readFile(file, 'utf8'));
    assert.deepEqual(context.span_attributes, attributes);
    assert.equal(context.message_uuid, messageUuid);
    assert.equal((await stat(file)).mode & 0o777, 0o600);
    assert.deepEqual(readInvocationSpanAttributes({ agentId, messageUuid, contextRoot: join(root, 'contexts') }), attributes);
    assert.equal(JSON.parse(run(args, JSON.stringify(attributes)).stdout).status, 'unchanged');
    assert.equal(run(args, JSON.stringify({ 'agentcore.task_id': 'different' })).status, 1);
    assert.deepEqual(JSON.parse(await readFile(file, 'utf8')).span_attributes, attributes);
  });
}

test('public CLI preserves validation exit codes and never echoes attribute values', () => {
  assert.equal(run(['invocation-context', 'put', '--agent', '../bad']).status, 2);
  const args = ['invocation-context', 'put', '--agent', 'qoder', '--message-uuid', randomUUID()];
  const result = run(args, JSON.stringify({ 'agentcore.password': 'DO_NOT_ECHO_VALUE' }));
  assert.equal(result.status, 1);
  assert.ok(!`${result.stdout}${result.stderr}`.includes('DO_NOT_ECHO_VALUE'));
  assert.equal(run(args, 'not-json').status, 1);
});

test('other commands keep argv and use the original collector entry', async () => {
  await writeFile(join(root, 'collector.js'), 'console.log(JSON.stringify(process.argv.slice(2)));');
  for (const args of [[], ['deploy', '--require', 'qoder', '--json'], ['worker', 'status']]) {
    const result = run(args);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), args);
  }
  await writeFile(join(root, 'collector.js'), 'throw new Error("collector-guard-failure");');
  const failure = run([]);
  assert.equal(failure.status, 1);
  assert.match(failure.stderr, /collector-guard-failure/);
});
