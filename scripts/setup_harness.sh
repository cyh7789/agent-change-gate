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

# 內容走 stdin，不走命令列：`ps` 對機器上的任何使用者都看得見 argv，而這裡面有 API key。
post() {   # post <path> <json-on-stdin>
  local out
  out=$(printf '%s' "$2" | curl -sS -X POST "$BASE$1" -H 'Content-Type: application/json' --data-binary @- \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("error",{}).get("message") or "ok")')
  echo "$out"
  # 已經設好不算失敗；其他錯誤要停下來，不然接著跑的東西會撞上沒有模型的 harness。
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
