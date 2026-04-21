# Integration guide

How to wire `whoop-mcp-server` into a consuming project — typically a
Claude Code workspace that already has an `.mcp.json` file.

Throughout this doc:
- `$WHOOP_MCP_DIR` is the path to this repo on your machine
  (e.g. `~/dev/whoop-mcp-server`).
- `$CONSUMER_DIR` is the path to the project you want to wire Whoop
  into (e.g. `~/research/health`).

## Prerequisites

Before doing any integration work, confirm the server itself is ready.
From `$WHOOP_MCP_DIR`:

```bash
ls config/.env config/token.json   # both must exist
podman image exists whoop-mcp-server && echo OK
```

If any of those fail, walk through the [Quick start](../README.md#quick-start)
section of the main README first.

## 1. Add the MCP entry

Edit `$CONSUMER_DIR/.mcp.json` and add a `whoop` server. The command
must be an **absolute path** — `.mcp.json` does not expand `$HOME` or
relative paths, so substitute the real absolute path when you edit.
Example diff:

```diff
 {
   "mcpServers": {
     "memory": {
       "command": "./scripts/mcp-memory.sh",
       "args": []
     },
     "pubmed": {
       "command": "./scripts/mcp-pubmed.sh",
       "args": []
+    },
+    "whoop": {
+      "command": "/abs/path/to/whoop-mcp-server/scripts/mcp-whoop.sh",
+      "args": []
     }
   }
 }
```

Use repo-relative paths (`./scripts/...`) only for scripts that live
inside the consumer project itself. Whoop lives in a separate repo, so
absolute is correct.

## 2. Restart Claude Code in the project

Claude Code reads `.mcp.json` at session start. Quit any running
session in `$CONSUMER_DIR` and re-enter the project. On first launch
in a session that references a new MCP server, Claude Code typically
prompts you to approve the server before it's spawned — say yes.

## 3. Verify the tools loaded

Inside Claude Code, run:

```
/mcp
```

(or whatever your build uses to list active MCP servers). You should
see `whoop` listed with 15 tools. In Claude Code's internal tool
namespace they'll appear as:

```
mcp__whoop__check_auth_status
mcp__whoop__get_profile
mcp__whoop__get_body_measurements
mcp__whoop__get_latest_cycle
mcp__whoop__get_cycles
mcp__whoop__get_cycle_by_id
mcp__whoop__get_latest_recovery
mcp__whoop__get_recoveries
mcp__whoop__get_recovery_for_cycle
mcp__whoop__get_sleeps
mcp__whoop__get_sleep_by_id
mcp__whoop__get_sleep_for_cycle
mcp__whoop__get_workouts
mcp__whoop__get_workout_by_id
mcp__whoop__get_average_strain
```

## 4. First smoke-test query

Try a natural-language prompt that should unambiguously trigger a tool
call, e.g.:

> *"What's my latest Whoop recovery score?"*

Claude will invoke `get_latest_recovery`, a fresh podman container
will start, it'll hit the Whoop API, refresh tokens if needed, and
reply in-conversation. The container exits immediately after.

## 5. Allow the tools (optional but recommended)

If you're tired of the per-call "Allow this tool?" prompt, add the
tools to the project's `.claude/settings.json` allowlist. The
`fewer-permission-prompts` skill can scan recent transcripts and
suggest an allowlist, but manually:

```json
{
  "permissions": {
    "allow": [
      "mcp__whoop__*"
    ]
  }
}
```

## How it runs, end-to-end

```
Claude Code
  ├─ reads .mcp.json
  ├─ spawns: $WHOOP_MCP_DIR/scripts/mcp-whoop.sh
  │    ├─ reads $PROJECT_DIR/config/.env  (OAuth app creds)
  │    ├─ checks $PROJECT_DIR/config/token.json exists and is non-empty
  │    └─ exec podman run -i --rm …  (ephemeral container)
  │         ├─ mounts config/token.json read-write (for rotation)
  │         ├─ --cap-drop=ALL, --read-only, --userns=keep-id, etc.
  │         └─ ENTRYPOINT python src/whoop_server.py (MCP stdio)
  ├─ sends JSON-RPC over stdin/stdout
  │    ├─ initialize → tools/list → tools/call(name, args) → …
  │    └─ tool call → whoop_client → httpx → api.prod.whoop.com
  └─ on disconnect, container exits; next prompt spawns a fresh one
```

Startup cost per call-chain is the `podman run` itself (~200ms on a
warm host). If that ever matters, the mcp-pubmed.sh pattern in this
same project (persistent container, `podman exec` per session) is the
optimization path — but for a per-question tool like Whoop it's not
worth the complexity.

## Troubleshooting

### "Missing config/.env" on startup
`./scripts/auth.sh` or the launcher can't find credentials. Re-check
that `config/.env` exists in the whoop-mcp-server repo root (not in
the health project).

### "Missing config/token.json — run scripts/auth.sh first"
Token file hasn't been created yet. Run `./scripts/auth.sh` from the
whoop-mcp-server repo.

### "Authentication error" returned from `check_auth_status`
Most likely the refresh token got revoked (you logged in with a
different Whoop app, or revoked the app from
[whoop.com](https://whoop.com) settings). Delete `config/token.json`
and re-run `./scripts/auth.sh`.

### Tools appear but all calls return `_error: "401"`
The access token couldn't be refreshed. Same fix as above. Also
double-check that `WHOOP_CLIENT_ID` / `WHOOP_CLIENT_SECRET` in
`config/.env` match the app registered in the Whoop dashboard — a
mismatch here yields 401 on refresh.

### Container won't start / podman errors
Check logs directly (substitute your real absolute path):
```bash
podman run --rm -i \
  --env-file "$WHOOP_MCP_DIR/config/.env" \
  -e WHOOP_TOKEN_PATH=/app/config/token.json \
  -v "$WHOOP_MCP_DIR/config/token.json:/app/config/token.json:z" \
  --userns=keep-id \
  whoop-mcp-server </dev/null
```
This runs the container, lets it hit stdin EOF immediately, and
prints startup errors to stderr.

### Rebuilding after updating the server
```bash
cd "$WHOOP_MCP_DIR"
./scripts/build.sh
```
The MCP launcher always pulls the `whoop-mcp-server:latest` image, so
rebuilding is enough — no need to touch `.mcp.json` or restart Claude
Code (next tool call will use the new image).

## Notes for the LLM consumer

- **List-returning tools** (e.g. `get_cycles`) emit one MCP text
  content block per list element. Claude Code handles this natively
  — each record is effectively its own message for the model to
  reason about. No action needed.
- Sleep/workout IDs are **UUIDs**, cycle IDs are **integers** — pass
  as strings.
- Dates accepted in the arguments aren't currently exposed (tools use
  "last N days" windows). If you need absolute date-range queries,
  extend `whoop_server.py` to add them.
- Tool return shapes follow the Whoop v2 docs exactly: refer to the
  [user-data spec](https://developer.whoop.com/docs/developing/user-data/cycle)
  for field semantics. The per-tool docstrings surface the most
  important fields inline.
