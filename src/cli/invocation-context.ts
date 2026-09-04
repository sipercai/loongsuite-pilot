import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';
import { resolveHome } from '../utils/fs-utils.js';

const DEFAULT_TTL_MS = 48 * 60 * 60 * 1_000;
const MAX_TTL_MS = 7 * 24 * 60 * 60 * 1_000;
const MAX_ATTRIBUTES = 32;
const MAX_ATTRIBUTE_KEY_LENGTH = 128;
const MAX_ATTRIBUTE_VALUE_LENGTH = 512;
const MAX_STDIN_BYTES = 16 * 1_024;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const AGENT_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ATTRIBUTE_KEY_RE = /^[A-Za-z][A-Za-z0-9_.-]*$/;
const SENSITIVE_FIELD_NAME_RE = /(^|[_.-])(TOKEN|SECRET|PASSWORD|CREDENTIAL|COOKIE)([_.-]|$)|^(API_KEY|API_HEADER)$/i;
const RESERVED_ATTRIBUTE_PREFIXES = [
  'gen_ai.', 'git.', 'workspace.', 'event.', 'trace_', 'user.', 'cost_', 'agent.',
  'time_unix_nano', 'observed_time_unix_nano',
];

export interface InvocationContext {
  readonly version: 1;
  readonly agent_id: string;
  readonly message_uuid: string;
  readonly span_attributes: Readonly<Record<string, string>>;
  readonly created_at: string;
  readonly expires_at: string;
}

interface ParsedArgs {
  readonly action?: 'put';
  readonly agentId?: string;
  readonly messageUuid?: string;
  readonly ttlMs?: number;
  readonly error?: string;
}

function defaultDataDir(environment: NodeJS.ProcessEnv): string {
  return resolveHome(environment.LOONGSUITE_PILOT_DATA_DIR || path.join(os.homedir(), '.loongsuite-pilot'));
}

export function invocationContextPath(dataDir: string, agentId: string, messageUuid: string): string {
  return path.join(dataDir, 'state', 'invocation-contexts', agentId, `${messageUuid}.json`);
}

function parseArgs(args: readonly string[]): ParsedArgs {
  if (args[0] !== 'put') return { error: 'expected subcommand: put' };
  let agentId: string | undefined;
  let messageUuid: string | undefined;
  let ttlMs: number | undefined;
  for (let index = 1; index < args.length; index += 1) {
    const flag = args[index];
    const value = args[index + 1];
    if (flag === '--agent') agentId = value;
    else if (flag === '--message-uuid') messageUuid = value;
    else if (flag === '--ttl-ms') ttlMs = Number(value);
    else return { error: `unknown argument: ${flag}` };
    if (value === undefined) return { error: `missing value for ${flag}` };
    index += 1;
  }
  if (!agentId || !AGENT_ID_RE.test(agentId)) return { error: 'invalid --agent' };
  if (!messageUuid || !UUID_RE.test(messageUuid)) return { error: 'invalid --message-uuid' };
  if (ttlMs !== undefined && (!Number.isSafeInteger(ttlMs) || ttlMs < 1 || ttlMs > MAX_TTL_MS)) {
    return { error: `--ttl-ms must be an integer from 1 to ${MAX_TTL_MS}` };
  }
  return { action: 'put', agentId, messageUuid, ttlMs };
}

function isReservedAttributeKey(key: string): boolean {
  return RESERVED_ATTRIBUTE_PREFIXES.some(prefix => key === prefix || key.startsWith(prefix));
}

export function parseInvocationAttributes(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('stdin must be a JSON object of span attributes');
  }
  const entries = Object.entries(value);
  if (entries.length === 0 || entries.length > MAX_ATTRIBUTES) {
    throw new Error(`stdin must contain 1 to ${MAX_ATTRIBUTES} span attributes`);
  }
  const attributes: Record<string, string> = {};
  for (const [key, rawValue] of entries) {
    if (!ATTRIBUTE_KEY_RE.test(key) || key.length > MAX_ATTRIBUTE_KEY_LENGTH) {
      throw new Error(`invalid span attribute key: ${key}`);
    }
    if (SENSITIVE_FIELD_NAME_RE.test(key) || isReservedAttributeKey(key)) {
      throw new Error(`span attribute key is not allowed: ${key}`);
    }
    if (typeof rawValue !== 'string' || rawValue.length === 0 || rawValue.length > MAX_ATTRIBUTE_VALUE_LENGTH) {
      throw new Error(`invalid span attribute value for: ${key}`);
    }
    attributes[key] = rawValue;
  }
  return attributes;
}

function sameAttributes(left: Readonly<Record<string, string>>, right: Readonly<Record<string, string>>): boolean {
  const leftEntries = Object.entries(left).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right).sort(([a], [b]) => a.localeCompare(b));
  return leftEntries.length === rightEntries.length
    && leftEntries.every(([key, value], index) => key === rightEntries[index][0] && value === rightEntries[index][1]);
}

function parseStoredContext(value: unknown): InvocationContext | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  if (record.version !== 1 || typeof record.agent_id !== 'string' || typeof record.message_uuid !== 'string'
    || typeof record.created_at !== 'string' || typeof record.expires_at !== 'string') return undefined;
  if (!Number.isFinite(Date.parse(record.created_at)) || !Number.isFinite(Date.parse(record.expires_at))) {
    return undefined;
  }
  try {
    const spanAttributes = parseInvocationAttributes(record.span_attributes);
    return {
      version: 1,
      agent_id: record.agent_id,
      message_uuid: record.message_uuid,
      span_attributes: spanAttributes,
      created_at: record.created_at,
      expires_at: record.expires_at,
    };
  } catch {
    return undefined;
  }
}

async function readContext(file: string): Promise<InvocationContext | undefined> {
  try {
    return parseStoredContext(JSON.parse(await fs.readFile(file, 'utf8')));
  } catch {
    return undefined;
  }
}

async function createContextExclusive(file: string, context: InvocationContext): Promise<boolean> {
  const directory = path.dirname(file);
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  await fs.chmod(directory, 0o700).catch(() => {});
  const temporary = path.join(directory, `.${path.basename(file)}.${process.pid}.${randomUUID()}.tmp`);
  try {
    await fs.writeFile(temporary, `${JSON.stringify(context)}\n`, { encoding: 'utf8', mode: 0o600 });
    await fs.chmod(temporary, 0o600).catch(() => {});
    try {
      // link() is an atomic create-if-absent operation. rename() would replace
      // an existing file on POSIX, allowing two concurrent Runtime calls for
      // one UUID to silently overwrite each other.
      await fs.link(temporary, file);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (code === 'EEXIST') return false;
      throw error;
    }
    await fs.chmod(file, 0o600).catch(() => {});
    return true;
  } finally {
    await fs.unlink(temporary).catch(() => {});
  }
}

export async function putInvocationContext(input: {
  readonly dataDir: string;
  readonly agentId: string;
  readonly messageUuid: string;
  readonly spanAttributes: Readonly<Record<string, string>>;
  readonly ttlMs?: number;
  readonly now?: Date;
}): Promise<'stored' | 'unchanged'> {
  const ttlMs = input.ttlMs ?? DEFAULT_TTL_MS;
  if (!AGENT_ID_RE.test(input.agentId)) throw new Error('invalid agent id');
  if (!UUID_RE.test(input.messageUuid)) throw new Error('invalid message UUID');
  if (!Number.isSafeInteger(ttlMs) || ttlMs < 1 || ttlMs > MAX_TTL_MS) throw new Error('invalid TTL');
  const spanAttributes = parseInvocationAttributes(input.spanAttributes);
  const now = input.now ?? new Date();
  const file = invocationContextPath(input.dataDir, input.agentId, input.messageUuid);
  const context: InvocationContext = {
    version: 1,
    agent_id: input.agentId,
    message_uuid: input.messageUuid,
    span_attributes: spanAttributes,
    created_at: now.toISOString(),
    expires_at: new Date(now.getTime() + ttlMs).toISOString(),
  };
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const existing = await readContext(file);
    if (existing && Date.parse(existing.expires_at) > now.getTime()) {
      if (existing.agent_id === input.agentId && existing.message_uuid === input.messageUuid
        && sameAttributes(existing.span_attributes, spanAttributes)) return 'unchanged';
      throw new Error('invocation context already exists with different attributes');
    }
    if (existing) await fs.unlink(file).catch(() => {});
    if (await createContextExclusive(file, context)) return 'stored';
  }
  throw new Error('invocation context could not be registered after concurrent writes');
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
    size += bytes.byteLength;
    if (size > MAX_STDIN_BYTES) throw new Error(`stdin exceeds ${MAX_STDIN_BYTES} bytes`);
    chunks.push(bytes);
  }
  return Buffer.concat(chunks).toString('utf8');
}

export async function runInvocationContextCommand(
  args: readonly string[] = process.argv.slice(3),
  options: { readonly environment?: NodeJS.ProcessEnv; readonly stdin?: () => Promise<string> } = {},
): Promise<number> {
  const parsed = parseArgs(args);
  if (parsed.error || !parsed.action || !parsed.agentId || !parsed.messageUuid) {
    process.stderr.write(`loongsuite-pilot invocation-context: ${parsed.error || 'invalid arguments'}\n`);
    return 2;
  }
  try {
    const raw = await (options.stdin ?? readStdin)();
    const spanAttributes = parseInvocationAttributes(JSON.parse(raw));
    const status = await putInvocationContext({
      dataDir: defaultDataDir(options.environment ?? process.env),
      agentId: parsed.agentId,
      messageUuid: parsed.messageUuid,
      spanAttributes,
      ttlMs: parsed.ttlMs,
    });
    process.stdout.write(`${JSON.stringify({ status, agent: parsed.agentId, messageUuid: parsed.messageUuid })}\n`);
    return 0;
  } catch (error) {
    process.stderr.write(`loongsuite-pilot invocation-context: ${error instanceof Error ? error.message : String(error)}\n`);
    return 1;
  }
}
