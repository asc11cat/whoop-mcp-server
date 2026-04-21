# Whoop MCP Server

An MCP server that exposes personal Whoop data (cycles, recovery, sleep,
workouts, body measurements) to any MCP host — e.g. Claude Code — via
the Whoop v2 developer API.

Runs as an ephemeral, rootless podman container: one container per MCP
session, no persistent state beyond the rotating OAuth token file.

> **Wiring this into an existing project?** See
> **[docs/INTEGRATION.md](docs/INTEGRATION.md)** for the end-to-end
> guide (including the exact `.mcp.json` diff, tool namespace, and
> troubleshooting).

## Features

- **v2 Whoop developer API** (v1 is no longer supported by Whoop).
- **OAuth 2.0 authorization-code** flow. Credentials never stored in
  plaintext; one-time consent generates a refresh token that rotates
  on each use.
- **15 tools** covering every personal-data GET endpoint the API
  exposes.
- **Hardened container**: pinned digest base, non-root uid, `cap-drop=ALL`,
  `no-new-privileges`, read-only rootfs, resource limits.
- **Fully pinned deps**: every transitive dependency locked with sha256
  hashes; `pip install --require-hashes` enforces it.

## Layout

```
whoop-mcp-server/
├── Containerfile            # multi-stage build, digest-pinned python:3.12-slim
├── .containerignore
├── pyproject.toml           # top-level runtime deps
├── requirements.lock        # transitive pins with hashes (uv pip compile)
├── config/
│   ├── .env.example         # template
│   ├── .env                 # (not committed) client_id / client_secret
│   └── token.json           # (not committed) rotating refresh token bundle
├── docs/
│   └── INTEGRATION.md       # wiring guide for consuming projects
├── scripts/
│   ├── build.sh             # podman build
│   ├── auth.sh              # one-time browser OAuth flow inside the image
│   └── mcp-whoop.sh         # ephemeral MCP launcher (used by MCP hosts)
└── src/
    ├── whoop_server.py      # FastMCP tool surface
    ├── whoop_client.py      # httpx client, token refresh/rotation
    └── auth.py              # OAuth authorization-code helper
```

## Quick start

1. Register an app at [developer.whoop.com](https://developer.whoop.com)
   with redirect URI `http://localhost:8765/callback`. Enable scopes
   `offline`, `read:profile`, `read:body_measurement`, `read:cycles`,
   `read:recovery`, `read:sleep`, `read:workout`. Grab the client_id
   and client_secret.

2. Populate credentials:
   ```bash
   cp config/.env.example config/.env
   chmod 600 config/.env
   # edit config/.env and fill in WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET
   ```

3. Build the image:
   ```bash
   ./scripts/build.sh
   ```

4. One-time OAuth dance (opens your browser, captures the redirect,
   writes `config/token.json`):
   ```bash
   ./scripts/auth.sh
   ```

5. From now on `./scripts/mcp-whoop.sh` is the launcher. It's what MCP
   hosts spawn — each invocation is a fresh container that exits when
   the MCP client disconnects.

For wiring this into a specific project (e.g. a Claude Code workspace),
see [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Tools

All 15 tools are introspectable via MCP `tools/list`; the descriptions
below are summaries. Sleep and workout IDs are UUIDs; cycle IDs are
integers (Whoop kept those as `long`).

### Identity & body

| Tool | Returns |
|---|---|
| `check_auth_status()` | Live health check (does a profile fetch) |
| `get_profile()` | `user_id`, `email`, `first_name`, `last_name` |
| `get_body_measurements()` | `height_meter`, `weight_kilogram`, `max_heart_rate` |

### Cycles

A **cycle** is one physiological day. Today's in-progress cycle has
`end: null`. Scored cycles expose strain (0-21, non-linear Borg scale),
kilojoules, avg/max HR.

| Tool | Description |
|---|---|
| `get_latest_cycle()` | Most recent cycle, often the one in progress |
| `get_cycles(days=10)` | Cycles in the last N days, newest first |
| `get_cycle_by_id(cycle_id)` | Fetch by integer ID |

### Recovery

Morning score 0-100% derived from the prior night's sleep + HRV, RHR,
respiratory rate, SpO2, skin temp. Banded RED (≤33), YELLOW (34-66),
GREEN (≥67). `user_calibrating: true` means scores are still tuning
(~first 4 days for new users). Not every cycle has a recovery.

| Tool | Description |
|---|---|
| `get_latest_recovery()` | Most recent recovery score |
| `get_recoveries(days=10)` | Last N days of recoveries |
| `get_recovery_for_cycle(cycle_id)` | Recovery tied to a given cycle |

### Sleep

Both nightly sleep and naps (distinguished by the `nap` boolean).
Stage durations in ms (Light, Slow-Wave/Deep, REM, Awake). Plus
performance %, consistency %, efficiency %, respiratory rate, and a
sleep-need breakdown (baseline + debt + strain + nap adjustments).

| Tool | Description |
|---|---|
| `get_sleeps(days=10)` | Last N days of sleeps + naps |
| `get_sleep_by_id(sleep_id)` | Fetch by UUID |
| `get_sleep_for_cycle(cycle_id)` | Main nightly sleep for a cycle |

### Workouts

Scored exercise sessions tagged by `sport_name`. Per-workout strain
(0-21), avg/max HR, energy (kJ), distance/altitude (for outdoor
sports), and per-zone HR duration in ms (`zone_zero_milli` through
`zone_five_milli`). Zones use `max_heart_rate` from body measurements.

| Tool | Description |
|---|---|
| `get_workouts(days=10)` | Last N days of workouts |
| `get_workout_by_id(workout_id)` | Fetch by UUID |

### Derived

| Tool | Description |
|---|---|
| `get_average_strain(days=7)` | Mean cycle strain over the window |

## What's NOT available

The Whoop v2 API does not expose any of the following. If you need
them you'd have to go outside the supported API (not recommended):

- Raw continuous heart-rate time-series samples
- Raw continuous HRV samples
- Journal entries (alcohol, caffeine, stress, mood logs from the app)
- GPS tracks / route data
- Coaching insights, goals, or habit tracking

## Security

- **Container:** uid 1000, `--cap-drop=ALL`,
  `--security-opt=no-new-privileges`, read-only rootfs + tmpfs `/tmp`,
  `--pids-limit=100`, `--memory=256m`, `--cpus=0.5`,
  `--userns=keep-id` for file ownership on the token mount.
- **Network:** required (outbound HTTPS to `api.prod.whoop.com`). No
  listening ports except during the one-time `auth.sh` flow
  (`127.0.0.1:8765` loopback only).
- **Deps:** `pip install --require-hashes --no-deps` against
  `requirements.lock`. Base image pinned by sha256 digest.
- **Secrets at rest:** `config/.env` (OAuth app creds, mode 0600) and
  `config/token.json` (refresh token bundle, mode 0600). Both gitignored.
- **Token rotation:** each refresh produces a new refresh token; the
  old one is invalidated and the new pair is written atomically to
  `config/token.json`.

## Dependencies

Runtime (all pinned, all sha256-hashed via `requirements.lock`):

- `mcp` — Python MCP SDK, stdio transport
- `httpx` — HTTP client to the Whoop API
- `python-dotenv` — local `.env` loading

plus their transitive closure (29 packages total).

Regenerate the lock after editing `pyproject.toml`:
```bash
uv pip compile pyproject.toml --generate-hashes -o requirements.lock
```

## Revoking access

Remove the refresh token (`rm config/token.json`) and revoke the
OAuth app's authorization from your Whoop account settings. Rotate
the client_secret in the developer dashboard if it may have leaked.
