// Copyright 2026 Alibaba Group Holding Limited
// SPDX-License-Identifier: Apache-2.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const AGENT_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ATTRIBUTE_KEY_RE = /^[A-Za-z][A-Za-z0-9_.-]*$/;
const MAX_ATTRIBUTES = 32;
const MAX_ATTRIBUTE_KEY_LENGTH = 128;
const MAX_ATTRIBUTE_VALUE_LENGTH = 512;
const SENSITIVE_FIELD_NAME_RE = /(^|[_.-])(TOKEN|SECRET|PASSWORD|CREDENTIAL|COOKIE)([_.-]|$)|^(API_KEY|API_HEADER)$/i;
const RESERVED_ATTRIBUTE_PREFIXES = [
  'gen_ai.', 'git.', 'workspace.', 'event.', 'trace_', 'user.', 'cost_', 'agent.',
  'time_unix_nano', 'observed_time_unix_nano',
];

function pilotDataDir() {
  return process.env.LOONGSUITE_PILOT_DATA_DIR || path.join(os.homedir(), '.loongsuite-pilot');
}

function isAllowedAttributeKey(key) {
  return ATTRIBUTE_KEY_RE.test(key)
    && key.length <= MAX_ATTRIBUTE_KEY_LENGTH
    && !SENSITIVE_FIELD_NAME_RE.test(key)
    && !RESERVED_ATTRIBUTE_PREFIXES.some(prefix => key === prefix || key.startsWith(prefix));
}

function parseSpanAttributes(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const entries = Object.entries(value);
  if (entries.length === 0 || entries.length > MAX_ATTRIBUTES) return undefined;
  const attributes = {};
  for (const [key, rawValue] of entries) {
    if (!isAllowedAttributeKey(key) || typeof rawValue !== 'string'
      || rawValue.length === 0 || rawValue.length > MAX_ATTRIBUTE_VALUE_LENGTH) return undefined;
    attributes[key] = rawValue;
  }
  return attributes;
}

/**
 * Reads caller-registered, per-user-message attributes. The context is never
 * deleted here: delayed Stop callbacks and retry hooks must observe the same
 * attributes until their TTL expires.
 */
export function readInvocationSpanAttributes({ agentId, messageUuid, dataDir = pilotDataDir(), now = Date.now() }) {
  if (typeof agentId !== 'string' || !AGENT_ID_RE.test(agentId)) return {};
  if (typeof messageUuid !== 'string' || !UUID_RE.test(messageUuid)) return {};
  const file = path.join(dataDir, 'state', 'invocation-contexts', agentId, `${messageUuid}.json`);
  try {
    const context = JSON.parse(fs.readFileSync(file, 'utf8'));
    const expiresAt = Date.parse(context?.expires_at);
    if (!context || context.version !== 1 || context.agent_id !== agentId || context.message_uuid !== messageUuid
      || typeof context.expires_at !== 'string' || !Number.isFinite(expiresAt) || expiresAt <= now) return {};
    return parseSpanAttributes(context.span_attributes) || {};
  } catch {
    return {};
  }
}
