#!/usr/bin/env bash
# Build inside Linux amd64/glibc. Includes production native dependencies, not Node.
set -euo pipefail
cd "$(dirname "$0")/.."
OUTPUT="${1:?Usage: deploy/package-agentcore.sh /absolute/output.tar.gz}"
case "$OUTPUT" in /*) ;; *) echo 'Output must be absolute' >&2; exit 1 ;; esac
test "$(uname -s)" = Linux && test "$(uname -m)" = x86_64
getconf GNU_LIBC_VERSION >/dev/null
COMMIT="${PILOT_BUILD_COMMIT:-$(git rev-parse HEAD)}"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo 'Full source commit required' >&2; exit 1; }
npm run build
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/loongsuite-pilot"
mkdir -p "$PKG"
cp -R dist assets agents.d scripts "$PKG/"
cp package.json package-lock.json "$PKG/"
rm -f "$PKG/scripts/migrate-internal-config.js" "$PKG/scripts/updater-daemon.js"
find "$PKG" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PKG" -type f -name '*.pyc' -delete
(
  cd "$PKG"
  npm ci --omit=dev --registry="${NPM_REGISTRY:-https://registry.npmmirror.com/}"
  node -e "require('sqlite3'); require('zstd-napi')"
  test -s dist/index.js && test -s dist/collector.js && test -s dist/invocation-context.js
  test -s agents.d/qwenpaw.json && test -s assets/plugins/qwenpaw/loongsuite-pilot/plugin.py
  PILOT_BUILD_COMMIT="$COMMIT" node --input-type=module -e '
    import fs from "node:fs";
    const pkg=JSON.parse(fs.readFileSync("package.json"));
    fs.writeFileSync("BUILD.json", JSON.stringify({package:pkg.name,version:pkg.version,
      commit:process.env.PILOT_BUILD_COMMIT,platform:process.platform,arch:process.arch,
      node:process.version,builtAt:new Date().toISOString()},null,2)+"\n");'
)
mkdir -p "$(dirname "$OUTPUT")"
tar -czf "$OUTPUT" -C "$STAGE" loongsuite-pilot
sha256sum "$OUTPUT"
