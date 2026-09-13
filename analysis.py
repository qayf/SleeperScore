"""Core trade-analysis logic: pulls every trade in a Sleeper league, scores
each traded asset (player or draft pick) by real fantasy points scored
*after* the trade using the league's own scoring settings, and ranks
managers by net points gained/lost across all their trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sleeper_client as sc

MAX_WEEK = 18  # sleeper "leg" ceiling for a regular NFL season+playoffs


@dataclass
class Asset:
    kind: str  # "player" | "pick"
    name: str  # headline, e.g. "Cam Skattebo" / "2029 Round 1 pick"
    player_id: str | None  # underlying scorable player, if any (pick may resolve to one)
    points: float
    meta: str = ""  # muted subline, e.g. "RB · NYG" / "Original team · Lover Boys"
    detail: str = ""  # e.g. "-> drafted Ashton Jeanty" or "not yet drafted"

    @property
    def label(self) -> str:
        """Flat one-line form, still used for text search in the filters."""
        bits = [self.name]
        if self.meta:
            bits.append(self.meta)
        if self.detail:
            bits.append(self.detail)
        return " · ".join(bits)


@dataclass
class TradeSide:
    roster_id: int
    manager: str
    team_name: str
    received: list[Asset] = field(default_factory=list)
    sent: list[Asset] = field(default_factory=list)

    @property
    def points_in(self) -> float:
        return round(sum(a.points for a in self.received), 2)

    @property
    def points_out(self) -> float:
        return round(sum(a.points for a in self.sent), 2)

    @property
    def net(self) -> float:
        return round(self.points_in - self.points_out, 2)


@dataclass
class Trade:
    transaction_id: str
    week: int
    created: int
    sides: list[TradeSide]

    @property
    def winner(self) -> TradeSide | None:
        if len(self.sides) < 2:
            return None
        best = max(self.sides, key=lambda s: s.net)
        # only call it a "win" if nets actually differ
        if all(abs(s.net - best.net) < 1e-9 for s in self.sides):
            return None
        return best


class LeagueData:
    """Fetches and indexes everything needed once per analysis run."""

    def __init__(self, league_id: str):
        self.league_id = league_id
        self.league = sc.get_league(league_id)
        self.season = self.league["season"]
        self.scoring_settings: dict[str, float] = self.league.get("scoring_settings", {})

        self.users = sc.get_users(league_id)
        self.rosters = sc.get_rosters(league_id)
        self.state = sc.get_nfl_state()

        self.user_by_id = {u["user_id"]: u for u in self.users}
        self.roster_by_id = {r["roster_id"]: r for r in self.rosters}

        self.players = sc.get_players()

        self._stats_cache: dict[int, dict] = {}
        self._resolved_picks: dict[tuple, dict] | None = None

    # ---------- league shape (drives which KTC value set applies) ----------

    @property
    def is_superflex(self) -> bool:
        positions = self.league.get("roster_positions") or []
        return "SUPER_FLEX" in positions or positions.count("QB") > 1

    @property
    def has_te_premium(self) -> bool:
        return float(self.scoring_settings.get("bonus_rec_te") or 0) > 0

    @property
    def draft_rounds(self) -> int:
        return int((self.league.get("settings") or {}).get("draft_rounds") or 4)

    # ---------- draft pick ownership ----------

    def future_pick_seasons(self) -> list[str]:
        """Seasons whose rookie drafts haven't happened yet, so their picks
        are still tradeable assets."""
        traded = sc.get_traded_picks(self.league_id)
        seasons = {int(t["season"]) for t in traded}
        first = int(self.season) + 1
        last = max([*seasons, first + 2])
        return [str(s) for s in range(first, last + 1)]

    def owned_picks(self) -> dict[int, list[tuple[str, int, int]]]:
        """roster_id -> list of (season, round, original_roster_id) it owns.

        Every roster starts owning its own pick in each round/season; the
        traded_picks feed then reassigns the ones that have changed hands.
        """
        seasons = self.future_pick_seasons()
        owner: dict[tuple, int] = {}
        for season in seasons:
            for rnd in range(1, self.draft_rounds + 1):
                for roster_id in self.roster_by_id:
                    owner[(season, rnd, roster_id)] = roster_id

        for t in sc.get_traded_picks(self.league_id):
            key = (t["season"], t["round"], t["roster_id"])
            if key in owner:
                owner[key] = t["owner_id"]

        result: dict[int, list[tuple[str, int, int]]] = {r: [] for r in self.roster_by_id}
        for (season, rnd, original), current in owner.items():
            if current in result:
                result[current].append((season, rnd, original))

        for picks in result.values():
            picks.sort(key=lambda p: (p[0], p[1], p[2]))
        return result

    def roster_for_user(self, user_id: str) -> int | None:
        """Which roster this user controls — as owner, or as a co-owner."""
        for roster in self.rosters:
            if roster.get("owner_id") == user_id:
                return roster["roster_id"]
        for roster in self.rosters:
            if user_id in (roster.get("co_owners") or []):
                return roster["roster_id"]
        return None

    # ---------- display helpers ----------

    def manager_name(self, roster_id: int) -> str:
        roster = self.roster_by_id.get(roster_id)
        if not roster:
            return f"Roster {roster_id}"
        user = self.user_by_id.get(roster["owner_id"])
        if not user:
            return f"Roster {roster_id}"
        return user.get("display_name", f"Roster {roster_id}")

    def team_name(self, roster_id: int) -> str:
        roster = self.roster_by_id.get(roster_id)
        if not roster:
            return f"Team {roster_id}"
        user = self.user_by_id.get(roster["owner_id"])
        if not user:
            return f"Team {roster_id}"
        meta = user.get("metadata") or {}
        return meta.get("team_name") or user.get("display_name", f"Team {roster_id}")

    def player_name(self, player_id: str) -> str:
        p = self.players.get(player_id)
        if not p:
            return f"Player {player_id}"
        name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        pos = p.get("position") or ""
        team = p.get("team") or "FA"
        return f"{name} ({pos}-{team})" if pos else name

    def player_bare_name(self, player_id: str) -> str:
        """Just the name — no position/team suffix."""
        p = self.players.get(player_id)
        if not p:
            return f"Player {player_id}"
        return (
            p.get("full_name")
            or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            or f"Player {player_id}"
        )

    def player_meta(self, player_id: str) -> str:
        """'RB · NYG' — the muted line under a player's name."""
        p = self.players.get(player_id) or {}
        bits = [b for b in (p.get("position"), p.get("team") or "FA") if b]
        return " · ".join(bits)

    # ---------- points ----------

    def _week_stats(self, week: int) -> dict:
        if week not in self._stats_cache:
            current_week = int(self.state.get("week") or 1)
            is_final = week < current_week or self.state.get("season") != self.season
            self._stats_cache[week] = sc.get_week_stats(self.season, week, final=is_final)
        return self._stats_cache[week]

    def player_points_week(self, player_id: str, week: int) -> float:
        wk = self._week_stats(week)
        raw = wk.get(player_id)
        if not raw:
            return 0.0
        return round(
            sum(raw.get(stat, 0.0) * weight for stat, weight in self.scoring_settings.items()),
            2,
        )

    def player_points_since(self, player_id: str, start_week: int, end_week: int) -> float:
        return round(
            sum(self.player_points_week(player_id, w) for w in range(max(start_week, 1), end_week + 1)),
            2,
        )

    def current_scoreable_week(self) -> int:
        """Last week we have (or could have) final stats for."""
        current_week = int(self.state.get("week") or 1)
        if self.state.get("season") != self.season:
            return MAX_WEEK
        return max(current_week, 1)

    # ---------- draft pick resolution ----------

    def _draft_index(self) -> dict[tuple, dict]:
        """Maps (season, round, original_roster_id) -> {'player_id':..., 'picked_by_roster':...}
        for every pick in every completed draft this league has run."""
        if self._resolved_picks is not None:
            return self._resolved_picks

        index: dict[tuple, dict] = {}
        for d in sc.get_drafts(self.league_id):
            draft_id = d["draft_id"]
            season = d["season"]
            draft_obj = sc.get_draft(draft_id)
            slot_to_roster = {int(k): v for k, v in (draft_obj.get("slot_to_roster_id") or {}).items()}
            roster_to_slot = {v: k for k, v in slot_to_roster.items()}
            picks = sc.get_draft_picks(draft_id)
            by_round_slot = {(p["round"], p["draft_slot"]): p for p in picks}

            for orig_roster_id, slot in roster_to_slot.items():
                for rnd in range(1, (draft_obj.get("settings") or {}).get("rounds", 0) + 1):
                    pick = by_round_slot.get((rnd, slot))
                    if pick and pick.get("player_id"):
                        index[(season, rnd, orig_roster_id)] = {
                            "player_id": pick["player_id"],
                            "picked_by_roster": pick.get("roster_id"),
                        }
        self._resolved_picks = index
        return index

    def resolve_pick(self, season: str, round_: int, original_roster_id: int) -> dict | None:
        return self._draft_index().get((season, round_, original_roster_id))

    def pick_asset(self, season: str, round_: int, original_roster_id: int, trade_week: int) -> Asset:
        name = f"{season} Round {round_} pick"
        origin = f"Original team · {self.team_name(original_roster_id)}"
        resolved = self.resolve_pick(season, round_, original_roster_id)
        if not resolved:
            return Asset(kind="pick", name=name, player_id=None, points=0.0,
                         meta="Not yet drafted", detail="not yet drafted")

        pid = resolved["player_id"]
        pname = self.player_bare_name(pid)
        if season != self.season:
            # drafted in a season we don't have stats context for (shouldn't
            # normally happen for a single league_id, kept as a safe fallback)
            pts = 0.0
            detail = f"-> drafted {pname} (different season, points not tracked)"
        else:
            end_week = self.current_scoreable_week()
            pts = self.player_points_since(pid, 1, end_week)
            detail = f"-> drafted {pname}"
        return Asset(kind="pick", name=name, player_id=pid, points=pts,
                     meta=f"{origin} · became {pname}", detail=detail)

    # ---------- trades ----------

    def all_trades(self) -> list[Trade]:
        end_week = min(self.current_scoreable_week(), MAX_WEEK)
        seen_ids = set()
        trades: list[Trade] = []

        for week in range(1, end_week + 1):
            for txn in sc.get_transactions(self.league_id, week):
                if txn.get("type") != "trade" or txn.get("status") != "complete":
                    continue
                if txn["transaction_id"] in seen_ids:
                    continue
                seen_ids.add(txn["transaction_id"])
                trades.append(self._build_trade(txn, week))

        trades.sort(key=lambda t: t.created)
        return trades

    def _build_trade(self, txn: dict, week: int) -> Trade:
        roster_ids = txn.get("roster_ids") or []
        adds = txn.get("adds") or {}
        drops = txn.get("drops") or {}
        draft_picks = txn.get("draft_picks") or []

        sides = {
            rid: TradeSide(roster_id=rid, manager=self.manager_name(rid), team_name=self.team_name(rid))
            for rid in roster_ids
        }

        def _player_asset(player_id: str) -> Asset:
            pts = self.player_points_since(player_id, week, self.current_scoreable_week())
            return Asset(
                kind="player",
                name=self.player_bare_name(player_id),
                player_id=player_id,
                points=pts,
                meta=self.player_meta(player_id),
            )

        for player_id, to_roster in adds.items():
            if to_roster in sides:
                sides[to_roster].received.append(_player_asset(player_id))

        for player_id, from_roster in drops.items():
            if from_roster in sides:
                sides[from_roster].sent.append(_player_asset(player_id))

        for dp in draft_picks:
            season = dp["season"]
            round_ = dp["round"]
            orig_roster = dp["roster_id"]
            new_owner = dp["owner_id"]
            prev_owner = dp["previous_owner_id"]
            asset = self.pick_asset(season, round_, orig_roster, week)
            if new_owner in sides:
                sides[new_owner].received.append(asset)
            if prev_owner in sides:
                # separate Asset instance so points aren't accidentally shared/mutated
                sides[prev_owner].sent.append(
                    Asset(kind="pick", name=asset.name, player_id=asset.player_id,
                          points=asset.points, meta=asset.meta, detail=asset.detail)
                )

        return Trade(
            transaction_id=txn["transaction_id"],
            week=week,
            created=txn.get("created", 0),
            sides=list(sides.values()),
        )

    # ---------- leaderboard ----------

    def leaderboard(self, trades: list[Trade]) -> list[dict]:
        totals: dict[int, dict] = {}
        # seed every roster in the league, even ones that have never traded
        for roster_id in self.roster_by_id:
            totals[roster_id] = {
                "roster_id": roster_id, "manager": self.manager_name(roster_id),
                "team_name": self.team_name(roster_id),
                "net_points": 0.0, "trades": 0, "wins": 0, "losses": 0, "pushes": 0,
            }

        for t in trades:
            for s in t.sides:
                row = totals.setdefault(
                    s.roster_id,
                    {"roster_id": s.roster_id, "manager": s.manager, "team_name": s.team_name,
                     "net_points": 0.0, "trades": 0, "wins": 0, "losses": 0, "pushes": 0},
                )
                row["net_points"] = round(row["net_points"] + s.net, 2)
                row["trades"] += 1
                winner = t.winner
                if winner is None:
                    row["pushes"] += 1
                elif winner.roster_id == s.roster_id:
                    row["wins"] += 1
                else:
                    row["losses"] += 1

        board = list(totals.values())
        board.sort(key=lambda r: r["net_points"], reverse=True)
        for i, row in enumerate(board, start=1):
            row["rank"] = i
        return board
