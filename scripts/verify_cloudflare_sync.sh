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
  "PJ_MAX_UPLOAD_BYTES"
)

REQUIRED_SECRETS=(
  "OPENAI_API_KEY"
  "PJ_OWNER_EMAILS"
  "PJ_TOOL_BRIDGE_TOKEN"
)

usage() {
  cat <<'EOF'
Usage: scripts/verify_cloudflare_sync.sh [wrangler-config]

Validates the configured Worker routes and variables, checks remote secret names
with Wrangler, and prints the manual Cloudflare Access application/policy checks.

Environment:
  WRANGLER_BIN  Wrangler executable to use (default: wrangler)
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

print_access_checklist

if (( error_count > 0 )); then
  printf '\nCloudflare sync validation failed with %d error(s).\n' "$error_count" >&2
  exit 1
fi

printf '\nCloudflare sync validation passed.\n'
