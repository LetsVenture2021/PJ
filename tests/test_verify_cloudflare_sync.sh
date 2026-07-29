#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validator="$repo_root/scripts/verify_cloudflare_sync.sh"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT

cat >"$test_dir/wrangler.toml" <<'EOF'
name = "pj-realtime-backend"
main = "pj_realtime_backend_worker.js"

routes = [
  { pattern = "example.com/health", zone_name = "example.com" },
  { pattern = "example.com/session", zone_name = "example.com" },
  { pattern = "example.com/token", zone_name = "example.com" },
  { pattern = "example.com/tool-schemas", zone_name = "example.com" },
  { pattern = "example.com/execute-tool", zone_name = "example.com" },
  { pattern = "example.com/responses/*", zone_name = "example.com" },
  { pattern = "example.com/upload/*", zone_name = "example.com" },
]

[vars]
PJ_ALLOWED_ORIGINS = "https://example.com"
CF_ACCESS_TEAM_DOMAIN = "example.cloudflareaccess.com"
CF_ACCESS_AUD = "test-audience"
PJ_TOOL_BRIDGE_URL = "https://runtime.example.com/execute-tool"
PJ_TOOL_SCHEMAS_URL = "https://runtime.example.com/tool-schemas"
EOF

cat >"$test_dir/wrangler" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1 $2 $3" == "secret list --config" ]]
cat <<'JSON'
[
  {"name": "OPENAI_API_KEY", "type": "secret_text"},
  {"name": "PJ_OWNER_EMAILS", "type": "secret_text"}
JSON
if [[ "${OMIT_BRIDGE_SECRET:-}" != "1" ]]; then
  cat <<'JSON'
  ,{"name": "PJ_TOOL_BRIDGE_TOKEN", "type": "secret_text"}
JSON
fi
cat <<'JSON'
]
JSON
EOF
chmod +x "$test_dir/wrangler"

WRANGLER_BIN="$test_dir/wrangler" "$validator" "$test_dir/wrangler.toml" \
  >"$test_dir/pass.out" 2>"$test_dir/pass.err"
grep -Fq "Cloudflare sync validation passed." "$test_dir/pass.out"
grep -Fq "Cloudflare Access application/policy checklist" "$test_dir/pass.out"

sed '/example.com\/token/d' "$test_dir/wrangler.toml" >"$test_dir/incomplete.toml"
if WRANGLER_BIN="$test_dir/wrangler" "$validator" "$test_dir/incomplete.toml" \
  >"$test_dir/fail.out" 2>"$test_dir/fail.err"; then
  echo "Expected incomplete config validation to fail." >&2
  exit 1
fi
grep -Fq "Expected route must be configured exactly once: /token" "$test_dir/fail.err"
grep -Fq "Cloudflare Access application/policy checklist" "$test_dir/fail.out"

if OMIT_BRIDGE_SECRET=1 WRANGLER_BIN="$test_dir/wrangler" \
  "$validator" "$test_dir/wrangler.toml" \
  >"$test_dir/missing-secret.out" 2>"$test_dir/missing-secret.err"; then
  echo "Expected missing remote secret validation to fail." >&2
  exit 1
fi
grep -Fq "Remote secret name is missing: PJ_TOOL_BRIDGE_TOKEN" \
  "$test_dir/missing-secret.err"

printf 'Cloudflare sync validator tests passed.\n'
