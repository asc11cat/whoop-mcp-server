#!/usr/bin/env python3
"""MCP server for the Whoop v2 developer API.

Whoop tracks physiological data via a wrist-worn strap. The core concepts:

- Cycle: one "physiological day", typically starting at wake. Today's cycle
  has `end: null` because it's still ongoing. Scored cycles contain strain
  and energy-expenditure metrics.
- Recovery: a morning-of score (0-100%) derived from the prior day's sleep
  plus HRV, RHR, respiratory rate, SpO2, and skin temperature. Whoop bands
  recovery as RED (0-33%), YELLOW (34-66%), GREEN (67-100%).
- Strain: a 0-21 Borg-based exertion score; NON-LINEAR (moving from 16→17
  takes far more load than 4→5). Bucketed as Light / Moderate / High /
  All Out. Whoop scores strain per full cycle and also per workout.
- Sleep: tracks stages (Light, REM, Slow-Wave/Deep, Awake) and computes
  performance/consistency/efficiency percentages plus a "sleep need"
  figure in ms. Naps are tracked as separate sleep activities (`nap: true`).
- Workout: a scored exercise session tagged with a `sport_name`. Contains
  heart-rate zone time (zone_zero..zone_five, milliseconds), plus
  distance/altitude for outdoor sports.

Every scored object carries `score_state`: "SCORED" (data present),
"PENDING_SCORE" (in progress), or "UNSCORABLE" (insufficient data).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from whoop_client import WhoopClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("Whoop API MCP Server")
_client: WhoopClient | None = None


def _iso_range(days: int) -> tuple[str, str]:
    """Build an ISO-8601 UTC (start, end) spanning the last `days` days."""
    now = datetime.now(timezone.utc)
    end = datetime.combine(now.date(), time.max, tzinfo=timezone.utc)
    start = datetime.combine(
        (now - timedelta(days=days)).date(), time.min, tzinfo=timezone.utc
    )
    return (
        start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


def _require_client() -> WhoopClient:
    if _client is None:
        raise RuntimeError(
            "Whoop client not initialized — missing credentials or token"
        )
    return _client


# ---- MCP tools ----------------------------------------------------------


@mcp.tool()
def check_auth_status() -> dict[str, Any]:
    """Verify the server can talk to the Whoop API.

    Performs a live profile fetch as a round-trip health check. Returns
    `{"authenticated": true, "profile": {...}}` on success, or
    `{"authenticated": false, "error": "<message>"}` on any failure
    (bad credentials, expired refresh token, network issue).
    """
    try:
        profile = _require_client().get_profile()
        return {"authenticated": True, "profile": profile}
    except Exception as e:
        return {"authenticated": False, "error": str(e)}


@mcp.tool()
def get_profile() -> dict[str, Any]:
    """Get the authenticated user's basic Whoop profile.

    Returns: `user_id` (int), `email`, `first_name`, `last_name`.
    No biometric data. Use `get_body_measurements` for physical stats.
    """
    return _require_client().get_profile()


@mcp.tool()
def get_body_measurements() -> dict[str, Any]:
    """Get the user's body measurements that Whoop uses as baseline.

    Returns:
      - `height_meter` (float)
      - `weight_kilogram` (float)
      - `max_heart_rate` (int) — the max HR Whoop calculated for this user,
        used in zone-percentage calculations.
    """
    return _require_client().get_body_measurement()


@mcp.tool()
def get_latest_cycle() -> dict[str, Any]:
    """Get the most recent physiological cycle (often the one in progress).

    A cycle is one "Whoop day" (wake-to-wake). If the current cycle is
    ongoing its `end` will be `null`. Returned fields include `id`
    (integer), `start`, `end`, `timezone_offset`, `score_state`, and — if
    `score_state == "SCORED"` — a `score` object with:
      - `strain` (0-21, non-linear, Borg-scale-based)
      - `kilojoule` (energy expenditure)
      - `average_heart_rate`, `max_heart_rate` (bpm)

    Use `get_cycles` if you need multiple days of history.
    """
    start, end = _iso_range(1)
    cycles = _require_client().get_cycle_collection(start, end, limit=25)
    if not cycles:
        return {"error": "No cycle data in the last day"}
    return cycles[0]


@mcp.tool()
def get_cycles(days: int = 10) -> list[dict[str, Any]]:
    """Get physiological cycles from the last `days` days, newest first.

    One cycle ~= one Whoop day. Each entry has `id`, time bounds,
    `score_state`, and (when SCORED) a `score` with `strain` (0-21),
    `kilojoule`, `average_heart_rate`, `max_heart_rate`. Pagination is
    handled internally; default window limit=25 per page.

    Args:
        days: How many days back to fetch (default 10).
    """
    start, end = _iso_range(days)
    return _require_client().get_cycle_collection(start, end, limit=25)


@mcp.tool()
def get_cycle_by_id(cycle_id: str) -> dict[str, Any]:
    """Get a single cycle by its integer ID.

    Cycle IDs are integers (unlike sleep/workout IDs which are UUIDs).
    Raises if the cycle doesn't belong to the authenticated user.
    """
    return _require_client().get_cycle(cycle_id)


@mcp.tool()
def get_latest_recovery() -> dict[str, Any]:
    """Get the most recent morning recovery score.

    Recovery is calculated once per cycle, after the main sleep completes.
    Returned `score` (when `score_state == "SCORED"`) contains:
      - `recovery_score` (0-100%; RED <=33, YELLOW 34-66, GREEN >=67)
      - `resting_heart_rate` (bpm)
      - `hrv_rmssd_milli` (heart-rate variability, RMSSD in milliseconds)
      - `spo2_percentage` (blood oxygen saturation)
      - `skin_temp_celsius`
      - `user_calibrating` (bool — true for new users still in the ~4-day
        baseline window; scores during calibration are less reliable)

    Not every cycle has a recovery; users who didn't wear the strap through
    the sleep period won't get one.
    """
    start, end = _iso_range(2)
    recoveries = _require_client().get_recovery_collection(start, end, limit=25)
    if not recoveries:
        return {"error": "No recovery data in the last two days"}
    return recoveries[0]


@mcp.tool()
def get_recoveries(days: int = 10) -> list[dict[str, Any]]:
    """Get recovery records from the last `days` days, newest first.

    Each record has `cycle_id`, `sleep_id`, `score_state`, and (when
    SCORED) a `score` containing `recovery_score` (0-100%),
    `resting_heart_rate`, `hrv_rmssd_milli`, `spo2_percentage`,
    `skin_temp_celsius`, and `user_calibrating`.

    Args:
        days: How many days back to fetch (default 10).
    """
    start, end = _iso_range(days)
    return _require_client().get_recovery_collection(start, end, limit=25)


@mcp.tool()
def get_recovery_for_cycle(cycle_id: str) -> dict[str, Any]:
    """Get the recovery record tied to a specific cycle.

    Same shape as individual recovery entries from `get_recoveries`. Note
    that not every cycle has a scored recovery — returns an error/404 if
    the user didn't wear the strap during the associated sleep or is
    still in calibration.

    Args:
        cycle_id: Integer cycle ID (as a string).
    """
    return _require_client().get_recovery_for_cycle(cycle_id)


@mcp.tool()
def get_sleep_for_cycle(cycle_id: str) -> dict[str, Any]:
    """Get the main sleep activity associated with a specific cycle.

    Returns the nightly sleep (not naps) that initiates the given cycle.
    Response shape matches `get_sleep_by_id` — stage summary, sleep need,
    respiratory rate, performance/consistency/efficiency percentages.

    Args:
        cycle_id: Integer cycle ID (as a string).
    """
    return _require_client().get_sleep_for_cycle(cycle_id)


@mcp.tool()
def get_sleeps(days: int = 10) -> list[dict[str, Any]]:
    """Get sleep records from the last `days` days, newest first.

    Includes both nightly sleeps AND naps — distinguish with the `nap`
    boolean. Each entry has `id` (UUID), `cycle_id`, `start`, `end`,
    `timezone_offset`, `nap`, `score_state`, and (when SCORED) a `score`
    containing:
      - `stage_summary`: milliseconds in each stage
        (`total_in_bed_time_milli`, `total_awake_time_milli`,
         `total_light_sleep_time_milli`, `total_slow_wave_sleep_time_milli`,
         `total_rem_sleep_time_milli`, `total_no_data_time_milli`) plus
         `sleep_cycle_count` and `disturbance_count`.
      - `sleep_needed`: ms breakdown — `baseline_milli`,
        `need_from_sleep_debt_milli`, `need_from_recent_strain_milli`,
        `need_from_recent_nap_milli`.
      - `respiratory_rate` (breaths/min)
      - `sleep_performance_percentage` (slept / needed, 0-100)
      - `sleep_consistency_percentage` (0-100)
      - `sleep_efficiency_percentage` (time asleep / time in bed, 0-100)

    Args:
        days: How many days back to fetch (default 10).
    """
    start, end = _iso_range(days)
    return _require_client().get_sleep_collection(start, end, limit=25)


@mcp.tool()
def get_sleep_by_id(sleep_id: str) -> dict[str, Any]:
    """Get a single sleep record by its UUID.

    Response shape matches entries from `get_sleeps`.

    Args:
        sleep_id: UUID string (v2 uses UUIDs, not integers).
    """
    return _require_client().get_sleep(sleep_id)


@mcp.tool()
def get_workouts(days: int = 10) -> list[dict[str, Any]]:
    """Get workouts from the last `days` days, newest first.

    Each entry has `id` (UUID), `sport_name`, `start`, `end`,
    `timezone_offset`, `score_state`, and (when SCORED) a `score`:
      - `strain` (0-21, workout-level; separate from cycle strain)
      - `average_heart_rate`, `max_heart_rate` (bpm)
      - `kilojoule` (energy)
      - `percent_recorded` (% of the workout duration with HR data)
      - `distance_meter`, `altitude_gain_meter`, `altitude_change_meter`
        (meaningful for outdoor sports; often 0/absent otherwise)
      - `zone_durations`: milliseconds in each HR zone (`zone_zero_milli`
        through `zone_five_milli`). Zones are based on the user's
        `max_heart_rate` from `get_body_measurements`.

    Args:
        days: How many days back to fetch (default 10).
    """
    start, end = _iso_range(days)
    return _require_client().get_workout_collection(start, end, limit=25)


@mcp.tool()
def get_workout_by_id(workout_id: str) -> dict[str, Any]:
    """Get a single workout by its UUID.

    Response shape matches entries from `get_workouts`.

    Args:
        workout_id: UUID string (v2 uses UUIDs, not integers).
    """
    return _require_client().get_workout(workout_id)


@mcp.tool()
def get_average_strain(days: int = 7) -> dict[str, Any]:
    """Mean cycle strain over the last `days` days.

    Strain is 0-21 on a NON-LINEAR Borg-exertion scale (16→17 is much
    harder than 4→5). Whoop bands raw strain as Light (0-9),
    Moderate (10-13), High (14-17), All Out (18-21). This is cycle
    strain (whole-day load), not per-workout strain.

    Skips cycles whose `score_state` is not "SCORED". Returns an error
    if the window contains no scored cycles.

    Args:
        days: How many days back to average over (default 7).

    Returns a dict with `average_strain`, `samples`, `days_requested`.
    """
    start, end = _iso_range(days)
    cycles = _require_client().get_cycle_collection(start, end, limit=25)
    strains = [
        c["score"]["strain"]
        for c in cycles
        if c.get("score_state") == "SCORED" and c.get("score", {}).get("strain") is not None
    ]
    if not strains:
        return {"error": "No scored cycles with strain in the window"}
    return {
        "average_strain": sum(strains) / len(strains),
        "samples": len(strains),
        "days_requested": days,
    }


# ---- startup ------------------------------------------------------------


def _init_client() -> WhoopClient | None:
    env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

    client_id = os.environ.get("WHOOP_CLIENT_ID", "").strip()
    client_secret = os.environ.get("WHOOP_CLIENT_SECRET", "").strip()
    token_path = Path(
        os.environ.get(
            "WHOOP_TOKEN_PATH",
            str(Path(__file__).resolve().parent.parent / "config" / "token.json"),
        )
    )

    if not client_id or not client_secret:
        logger.error("Missing WHOOP_CLIENT_ID or WHOOP_CLIENT_SECRET")
        return None
    if not token_path.exists():
        logger.error(f"Missing token file at {token_path} — run the auth flow first")
        return None

    try:
        client = WhoopClient(
            client_id=client_id,
            client_secret=client_secret,
            token_path=token_path,
        )
        logger.info("Whoop client initialized")
        return client
    except Exception as e:
        logger.error(f"Failed to initialize Whoop client: {e}")
        return None


def main() -> None:
    global _client
    logger.info("Starting Whoop MCP server")
    _client = _init_client()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
