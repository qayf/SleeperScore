"""Thin client for the public Sleeper API (https://docs.sleeper.com), with
on-disk JSON caching so repeated runs don't re-download the ~14MB players
dump or already-final weekly stat lines."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

BASE = "https://api.sleeper.app/v1"
STATS_BASE = "https://api.sleeper.app"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_session = requests.Session()


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, key.replace("/", "_") + ".json")


def _get(url: str, cache_key: str | None = None, ttl: float | None = None,
         fresh: bool = False) -> Any:
    """GET a URL as JSON, optionally cached to disk.

    ttl=None means "cache forever once written" (used for immutable/finalized
    data like past weeks' stats). ttl=seconds re-fetches after expiry.
    fresh=True bypasses the cache entirely — required for ownership
    verification, which must never read a stale value.
    """
    if cache_key and not fresh:
        path = _cache_path(cache_key)
        if os.path.exists(path):
            if ttl is None or (time.time() - os.path.getmtime(path)) < ttl:
                with open(path) as f:
                    return json.load(f)

    data = _get_with_retry(url)

    if cache_key:
        with open(_cache_path(cache_key), "w") as f:
            json.dump(data, f)
    return data


def _get_with_retry(url: str, attempts: int = 4) -> Any:
    """Sleeper will occasionally reset a connection mid-run; a single dropped
    request shouldn't sink an analysis that makes dozens of calls."""
    delay = 0.5
    for attempt in range(1, attempts + 1):
        try:
            resp = _session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout, ValueError):
            if attempt == attempts:
                raise
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            # retry transient server-side/rate-limit failures only
            if status not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def get_user(username_or_id: str) -> dict:
    """Look up a Sleeper account by username OR user_id.

    Note this is a *public* lookup — it takes no password and returns no
    credentials (Sleeper nulls out email/phone/token). It identifies an
    account; it does not authenticate one.
    """
    handle = (username_or_id or "").strip()
    return _get(f"{BASE}/user/{handle}", cache_key=f"user_{handle.lower()}", ttl=3600)


def get_user_leagues(user_id: str, season: str, sport: str = "nfl",
                     fresh: bool = False) -> list[dict]:
    return _get(
        f"{BASE}/user/{user_id}/leagues/{sport}/{season}",
        cache_key=f"userleagues_{user_id}_{sport}_{season}",
        ttl=300,
        fresh=fresh,
    )


def get_league(league_id: str) -> dict:
    return _get(f"{BASE}/league/{league_id}", cache_key=f"league_{league_id}", ttl=300)


def get_users(league_id: str, fresh: bool = False) -> list[dict]:
    return _get(f"{BASE}/league/{league_id}/users", cache_key=f"users_{league_id}",
                ttl=300, fresh=fresh)


def get_rosters(league_id: str) -> list[dict]:
    return _get(f"{BASE}/league/{league_id}/rosters", cache_key=f"rosters_{league_id}", ttl=300)


def get_transactions(league_id: str, week: int) -> list[dict]:
    return _get(
        f"{BASE}/league/{league_id}/transactions/{week}",
        cache_key=f"txn_{league_id}_{week}",
        ttl=600,
    )


def get_traded_picks(league_id: str) -> list[dict]:
    return _get(
        f"{BASE}/league/{league_id}/traded_picks",
        cache_key=f"tradedpicks_{league_id}",
        ttl=300,
    )


def get_drafts(league_id: str) -> list[dict]:
    return _get(f"{BASE}/league/{league_id}/drafts", cache_key=f"drafts_{league_id}", ttl=600)


def get_draft(draft_id: str) -> dict:
    return _get(f"{BASE}/draft/{draft_id}", cache_key=f"draft_{draft_id}", ttl=None)


def get_draft_picks(draft_id: str) -> list[dict]:
    return _get(f"{BASE}/draft/{draft_id}/picks", cache_key=f"draftpicks_{draft_id}", ttl=120)


def get_nfl_state() -> dict:
    return _get(f"{BASE}/state/nfl", cache_key="nfl_state", ttl=1800)


def get_players() -> dict:
    """The full NFL player dictionary. Sleeper asks that this only be
    fetched ~once/day, so we cache it hard on disk."""
    return _get(f"{BASE}/players/nfl", cache_key="players_nfl", ttl=86400)


def get_week_stats(season: str, week: int, season_type: str = "regular", final: bool = True) -> dict:
    """player_id -> raw stat category totals for one week.

    `final=False` (the current/in-progress week) is cached briefly since
    scores are still changing; completed weeks are cached forever.
    """
    if week < 1:
        return {}
    try:
        return _get(
            f"{STATS_BASE}/v1/stats/nfl/{season_type}/{season}/{week}",
            cache_key=f"stats_{season}_{season_type}_{week}",
            ttl=None if final else 300,
        )
    except requests.HTTPError:
        return {}
