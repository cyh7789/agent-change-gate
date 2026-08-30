#!/usr/bin/env bash
# Point a running TrueForge harness at a model provider and the GitHub MCP server.
#
#   GEMINI_API_KEY=... ./scripts/setup_harness.sh
#
# Both calls are idempotent: POST creates, and a 409 means it is already there.
set -euo pipefail

BASE="${TRUEFORGE_BASE:-http://localhost:8790/api/v1}"
: "${GEMINI_API_KEY:?set GEMINI_API_KEY (or edit this script for another provider)}"
GH_TOKEN="${GITHUB_TOKEN:-$(gh auth token)}"

# The content goes through stdin, not the command line: `ps` shows argv to every user on the
# machine, and this carries an API key.
post() {   # post <path> <json-on-stdin>
  local out
  out=$(printf '%s' "$2" | curl -sS -X POST "$BASE$1" -H 'Content-Type: application/json' --data-binary @- \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("error",{}).get("message") or "ok")')
  echo "$out"
  # Already configured is not a failure; any other error stops here, otherwise what runs next
  # meets a harness with no model.
  case "$out" in
    ok|*"already exists"*) return 0 ;;
    *) return 1 ;;
  esac
}

echo -n "model provider: "
post /settings/model-providers "$(cat <<JSON
{"manifest": {"type": "google-gemini",
   "auth": {"api_key": "$GEMINI_API_KEY"},
   "models": [
     {"model_id": "gemini-3.6-flash", "name": "gemini-3-6-flash",
      "properties": {"context_length": 1048576, "max_output_tokens": 65536}},
     {"model_id": "gemini-3.1-pro-preview", "name": "gemini-3-1-pro-preview",
      "properties": {"context_length": 1048576, "max_output_tokens": 65536}}]}}
JSON
)"

echo -n "github mcp:     "
post /settings/mcp-servers "$(cat <<JSON
{"manifest": {"type": "remote", "name": "github",
  "url": "https://api.githubcopilot.com/mcp/",
  "description": "GitHub repositories, issues and pull requests.",
  "auth": {"type": "header", "headers": {"Authorization": "Bearer $GH_TOKEN"}}}}
JSON
)"

echo -n "auth status:    "
curl -sS "$BASE/mcp-servers" \
  | python3 -c 'import json,sys; print(*["%s=%s" % (s["name"], s["auth_status"]["status"]) for s in json.load(sys.stdin)["data"]])'
