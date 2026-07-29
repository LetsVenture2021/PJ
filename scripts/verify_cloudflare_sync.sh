#!/usr/bin/env bash

set -uo pipefail

EXPECTED_ROUTES=(
  "/health"
  "/session"
  "/token"
  "/tool-schemas"
  "/execute-tool"
  "/responses/*"
  "/upload/*"
)

REQUIRED_VARS=(
  "PJ_ALLOWED_ORIGINS"
  "CF_ACCESS_TEAM_DOMAIN"
  "CF_ACCESS_AUD"
  "PJ_TOOL_BRIDGE_URL"
  "PJ_TOOL_SCHEMAS_URL"
)

REQUIRED_SECRETS=(
  "OPENAI_API_KEY"
  "PJ_OWNER_EMAILS"
  "PJ_TOOL_BRIDGE_TOKEN"
)

# Paths the zone's WAF custom skip rule must exempt from challenge products on
# the bridge hostname. Worker subrequests cannot answer bot challenges, so a
# missing path fails silently: the client reports work in progress and nothing
# reaches the runtime.
EXPECTED_WAF_SKIP_PATHS=(
  "/execute-tool"
  "/tool-schemas"
  "/health"
  "/responses/"
  "/upload/"
)

usage() {
  cat <<'EOF'
Usage: scripts/verify_cloudflare_sync.sh [wrangler-config]

Validates the configured Worker routes and variables, checks remote secret names
with Wrangler, and prints the manual Cloudflare Access application/policy checks.

Environment:
  WRANGLER_BIN  Wrangler executable to use (default: wrangler)
  PJ_RELEASE_MANIFEST Optional manifest whose routes/config/WAF sets are checked
  PJ_RELEASE_OBSERVATIONS Optional sanitized health JSON; enables parity verifier
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if (( $# > 1 )); then
  usage >&2
  exit 2
fi

config="${1:-wrangler.toml}"
wrangler_bin="${WRANGLER_BIN:-wrangler}"
error_count=0

pass() {
  printf 'PASS: %s\n' "$1"
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  error_count=$((error_count + 1))
}

print_waf_checklist() {
  cat <<'EOF'

WAF challenge-skip checklist (manual):
  [ ] A custom WAF skip rule covers the bridge hostname's authenticated paths:
      /execute-tool, /tool-schemas, /health, /responses/*, and /upload/*.
  [ ] The rule skips the challenge-issuing products in use (for example Super
      Bot Fight Mode) so Worker subrequests are never challenged.

Set CLOUDFLARE_API_TOKEN and PJ_CLOUDFLARE_ZONE_ID to check the deployed rule
automatically.
EOF
}

check_waf_skip_rule() {
  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${PJ_CLOUDFLARE_ZONE_ID:-}" ]]; then
    print_waf_checklist
    return
  fi
  local api="https://api.cloudflare.com/client/v4/zones/${PJ_CLOUDFLARE_ZONE_ID}/rulesets"
  local ruleset_id
  ruleset_id="$(
    curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$api" |
      python3 -c '
import json, sys
for ruleset in json.load(sys.stdin).get("result") or []:
    if ruleset.get("phase") == "http_request_firewall_custom":
        print(ruleset["id"])
        break
' 2>/dev/null
  )" || true
  if [[ -z "$ruleset_id" ]]; then
    fail "Could not read the zone's custom WAF ruleset with CLOUDFLARE_API_TOKEN"
    return
  fi
  local expressions
  expressions="$(
    curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$api/$ruleset_id" |
      python3 -c '
import json, sys
for rule in (json.load(sys.stdin).get("result") or {}).get("rules") or []:
    if rule.get("action") == "skip" and rule.get("enabled"):
        print(rule.get("expression", ""))
' 2>/dev/null
  )" || true
  for waf_path in "${EXPECTED_WAF_SKIP_PATHS[@]}"; do
    if printf '%s' "$expressions" | grep -Fq -- "$waf_path"; then
      pass "WAF skip rule covers bridge path: $waf_path"
    else
      fail "WAF skip rule is missing bridge path: $waf_path"
    fi
  done
}

print_access_checklist() {
  cat <<'EOF'

Cloudflare Access application/policy checklist (manual):
  [ ] A self-hosted Access application covers every configured Worker route.
  [ ] Its application audience (AUD) exactly matches CF_ACCESS_AUD.
  [ ] An Allow policy restricts protected routes to the intended owner identity.
  [ ] No broader Bypass or Allow policy applies to the protected routes.
  [ ] Access injects Cf-Access-Jwt-Assertion before requests reach the Worker.

The Access checklist is informational because Wrangler does not manage or expose
the Zero Trust application and policy configuration used by this manifest.
EOF
}

if [[ ! -r "$config" ]]; then
  fail "Wrangler config is not readable: $config"
  print_access_checklist
  exit 1
fi

if [[ -n "${PJ_RELEASE_MANIFEST:-}" ]]; then
  if [[ ! -r "$PJ_RELEASE_MANIFEST" ]]; then
    fail "Release manifest is not readable"
  else
    manifest_check="$(python3 - "$PJ_RELEASE_MANIFEST" "$config" <<'PY'
import json, re, sys
m = json.load(open(sys.argv[1]))
text = open(sys.argv[2]).read()
paths = sorted("/" + x.split("/", 1)[1] for x in re.findall(r'pattern\s*=\s*"([^"]+)"', text))
expected_waf = sorted(["/execute-tool", "/health", "/responses/", "/tool-schemas", "/upload/"])
errors = []
if sorted(m.get("routes", [])) != paths: errors.append("routes")
if sorted(m.get("waf_paths", expected_waf)) != expected_waf: errors.append("waf_paths")
required = {"PJ_ALLOWED_ORIGINS", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "PJ_TOOL_BRIDGE_URL", "PJ_TOOL_SCHEMAS_URL"}
if not required.issubset(m.get("required_config_keys", [])): errors.append("required_config_keys")
print(",".join(errors))
PY
)" || manifest_check="corrupt_manifest"
    if [[ -n "$manifest_check" ]]; then fail "Release manifest mismatch: $manifest_check"; else pass "Release manifest matches Wrangler route/config/WAF contract"; fi
  fi
fi

if [[ -n "${PJ_RELEASE_OBSERVATIONS:-}" ]]; then
  if python3 "$(dirname "$0")/verify_release.py" "$PJ_RELEASE_MANIFEST" "$PJ_RELEASE_OBSERVATIONS" >/dev/null; then
    pass "Runtime, Worker, and browser release parity"
  else
    fail "Runtime, Worker, or browser release parity mismatch"
  fi
fi

configured_routes="$(
  sed -n \
    '/^[[:space:]]*#/d; s/.*pattern[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$config"
)"
configured_paths=""

while IFS= read -r route; do
  [[ -z "$route" ]] && continue
  route_without_scheme="${route#*://}"
  if [[ "$route_without_scheme" != */* ]]; then
    fail "Route pattern has no path: $route"
    continue
  fi
  configured_paths="${configured_paths}/${route_without_scheme#*/}"$'\n'
done <<< "$configured_routes"

for expected_route in "${EXPECTED_ROUTES[@]}"; do
  route_count="$(
    printf '%s' "$configured_paths" | grep -Fxc -- "$expected_route" || true
  )"
  if [[ "$route_count" == "1" ]]; then
    pass "Expected route is configured: $expected_route"
  else
    fail "Expected route must be configured exactly once: $expected_route"
  fi
done

while IFS= read -r configured_path; do
  [[ -z "$configured_path" ]] && continue
  is_expected=false
  for expected_route in "${EXPECTED_ROUTES[@]}"; do
    if [[ "$configured_path" == "$expected_route" ]]; then
      is_expected=true
      break
    fi
  done
  if [[ "$is_expected" == false ]]; then
    fail "Unexpected Worker route is configured: $configured_path"
  fi
done <<< "$configured_paths"

get_var_assignment() {
  awk -v wanted="$1" '
    /^[[:space:]]*\[/ {
      in_vars = ($0 ~ /^[[:space:]]*\[vars\][[:space:]]*(#.*)?$/)
      next
    }
    in_vars {
      line = $0
      sub(/[[:space:]]*#.*/, "", line)
      equals = index(line, "=")
      if (equals == 0) {
        next
      }
      key = substr(line, 1, equals - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == wanted) {
        value = substr(line, equals + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        print value
        exit
      }
    }
  ' "$config"
}

for required_var in "${REQUIRED_VARS[@]}"; do
  assignment="$(get_var_assignment "$required_var")"
  if [[ -z "$assignment" || "$assignment" == '""' || "$assignment" == "''" ]]; then
    fail "Required [vars] binding is missing or empty: $required_var"
  elif [[ "$assignment" == *replace-with* ]]; then
    fail "Required [vars] binding still contains a placeholder: $required_var"
  else
    pass "Required [vars] binding is present: $required_var"
  fi
done

wrangler_available=true
if [[ "$wrangler_bin" == */* && ! -x "$wrangler_bin" ]]; then
  fail "Wrangler executable is not available: $wrangler_bin"
  wrangler_available=false
elif [[ "$wrangler_bin" != */* ]] && ! command -v "$wrangler_bin" >/dev/null 2>&1; then
  fail "Wrangler executable is not available on PATH: $wrangler_bin"
  wrangler_available=false
fi

secret_output=""
if [[ "$wrangler_available" == true ]]; then
  if secret_output="$("$wrangler_bin" secret list --config "$config" 2>&1)"; then
    for required_secret in "${REQUIRED_SECRETS[@]}"; do
      if printf '%s\n' "$secret_output" |
        grep -Eq "\"name\"[[:space:]]*:[[:space:]]*\"${required_secret}\""; then
        pass "Remote secret name exists: $required_secret"
      else
        fail "Remote secret name is missing: $required_secret"
      fi
    done
  else
    fail "Wrangler could not list remote secret names for $config"
    printf '%s\n' "$secret_output" >&2
  fi
fi

check_waf_skip_rule
print_access_checklist

if (( error_count > 0 )); then
  printf '\nCloudflare sync validation failed with %d error(s).\n' "$error_count" >&2
  exit 1
fi

printf '\nCloudflare sync validation passed.\n'
