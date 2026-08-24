"""KeepTradeCut dynasty trade values.

KTC has no public API, but its dynasty-rankings page embeds the full value
table as a `var playersArray = [...]` JSON literal. We fetch that page once
and cache it to disk for a day, then expose lookups by player name and by
draft pick.

Values come in variants that we pick to match the league:
  - superflex vs 1QB  (league has a SUPER_FLEX slot?)
  - TE premium        (league's scoring has bonus_rec_te?)
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

RANKINGS_URL = "https://keeptradecut.com/dynasty-rankings"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
CACHE_PATH = os.path.join(CACHE_DIR, "ktc_values.json")
CACHE_TTL = 86400  # be a good citizen: one fetch per day

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Fold a player name to a match key: lowercase alphanumerics, no
    punctuation, no generational suffix. 'A.J. Brown' -> 'ajbrown'."""
    cleaned = re.sub(r"[^a-zA-Z ]", "", name or "").lower().strip()
    parts = [p for p in cleaned.split() if p and p not in _SUFFIXES]
    return "".join(parts)


def _fetch_raw() -> list[dict]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached = os.path.exists(CACHE_PATH)
    if cached and (time.time() - os.path.getmtime(CACHE_PATH)) < CACHE_TTL:
        with open(CACHE_PATH) as f:
            return json.load(f)

    try:
        resp = requests.get(RANKINGS_URL, headers={"User-Agent": _UA}, timeout=30)
        resp.raise_for_status()
        match = re.search(r"var playersArray\s*=\s*(\[.*?\]);", resp.text, re.S)
        if not match:
            raise RuntimeError(
                "KTC's page layout changed — the embedded value table could not be found."
            )
        data = json.loads(match.group(1))
    except Exception:
        # values a day or two old still beat no values at all
        if cached:
            with open(CACHE_PATH) as f:
                return json.load(f)
        raise

    with open(CACHE_PATH, "w") as f:
        json.dump(data, f)
    return data


class KTCValues:
    """Value lookups for one league configuration."""

    def __init__(self, superflex: bool = True, te_premium: bool = False):
        self.superflex = superflex
        self.te_premium = te_premium
        raw = _fetch_raw()

        self.by_name: dict[str, dict] = {}
        self.picks: dict[str, int] = {}

        for entry in raw:
            value = self._extract(entry)
            if entry.get("position") == "RDP":
                self.picks[entry["playerName"]] = value
            else:
                key = normalize_name(entry.get("playerName", ""))
                if key and key not in self.by_name:
                    self.by_name[key] = {
                        "value": value,
                        "position": entry.get("position"),
                        "team": entry.get("team"),
                        "name": entry.get("playerName"),
                    }

        self.pick_seasons = sorted(
            {int(m.group(1)) for name in self.picks if (m := re.match(r"^(\d{4})", name))}
        )

    def _extract(self, entry: dict) -> int:
        bucket = entry.get("superflexValues" if self.superflex else "oneQBValues") or {}
        if self.te_premium and isinstance(bucket.get("tep"), dict):
            return int(bucket["tep"].get("value") or 0)
        return int(bucket.get("value") or 0)

    # ---------- lookups ----------

    def player_value(self, full_name: str) -> int | None:
        """None means "not in KTC's top 500" — effectively negligible dynasty
        value, but we surface that as unknown rather than pretending it's 0."""
        hit = self.by_name.get(normalize_name(full_name))
        return hit["value"] if hit else None

    def pick_value(self, season: str | int, round_: int, tier: str = "Mid") -> tuple[int | None, str]:
        """Value a rookie pick. Returns (value, label_actually_used).

        Future picks have no known slot yet, so 'Mid' is the neutral default.
        Seasons past KTC's horizon fall back to its furthest-out season.
        """
        season = int(season)
        note = ""
        if self.pick_seasons and season > self.pick_seasons[-1]:
            note = f" (valued as {self.pick_seasons[-1]})"
            season = self.pick_seasons[-1]

        ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(round_, f"{round_}th")
        key = f"{season} {tier} {ordinal}"
        if key in self.picks:
            return self.picks[key], key + note

        # rounds beyond KTC's coverage (it stops at the 4th) are ~worthless
        return None, f"{season} Round {round_}"
