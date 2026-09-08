/**
 * Match OpenTelemetry Python OTELResourceDetector's resource-list parsing:
 * first '=' splits the pair, trim both sides, unquote values (not keys),
 * retain '+' and empty values, and let the last duplicate win. Invalid pairs
 * are skipped. Invalid UTF-8 is replaced, while malformed % escapes survive,
 * just like urllib.parse.unquote. Do not log raw input: it may contain IDs.
 *
 * This deliberately does not use the span-attribute parser (no URL decoding)
 * or newer OTel JS envDetector rules (different malformed-input semantics).
 */
export function parseResourceEnvironment(raw: string | undefined): Record<string, string> {
  const attributes: Record<string, string> = Object.create(null);
  if (!raw) return attributes;
  for (const pair of raw.split(',')) {
    const separator = pair.indexOf('=');
    if (separator < 0) continue;
    const key = pair.slice(0, separator).trim();
    const value = pair.slice(separator + 1).trim();
    attributes[key] = value.replace(/(?:%[0-9a-f]{2})+/gi, encoded =>
      Buffer.from(encoded.replace(/%/g, ''), 'hex').toString('utf8'));
  }
  return attributes;
}
