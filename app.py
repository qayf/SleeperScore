import datetime as dt
import html
import re
import shutil

import pandas as pd
import streamlit as st


import sleeper_client as sc
from analysis import SCHEMA_VERSION, LeagueData, MAX_WEEK
from ktc import KTCValues
from proposal import SideValuation, ValuedAsset, evaluate

st.set_page_config(page_title="Sleeper Trade Analyzer", page_icon="\U0001F3C8", layout="wide")


@st.cache_resource(show_spinner=False, ttl=300)
def load(league_id: str, schema_version: int):
    """`schema_version` is unused in the body on purpose: it exists so the
    cache key changes when analysis.py's dataclasses do. Streamlit hashes
    this function's own source and its arguments, and would otherwise hand
    freshly-deployed render code objects built by the old definitions."""
    ld = LeagueData(league_id)
    trades = ld.all_trades()
    board = ld.leaderboard(trades)
    return ld, trades, board


@st.cache_data(show_spinner=False, ttl=1800)
def seasons_to_offer() -> list[str]:
    state = sc.get_nfl_state()
    current = int(state.get("season") or dt.date.today().year)
    return [str(s) for s in range(current, current - 4, -1)]


esc = html.escape  # for raw-HTML contexts; md() below is for markdown contexts


# Trade-history card styling. Colours mirror .streamlit/config.toml so the
# hand-rolled cards sit in the same Sleeper palette as the themed widgets.
TRADE_CARD_CSS = """
<style>
.th-card { padding: 2px 0 4px; }
.th-card-head {
  display: flex; align-items: center; justify-content: space-between;
  margin: 0 0 14px; font-size: 0.72rem; letter-spacing: 0.08em;
}
.th-status { display: inline-flex; align-items: center; gap: 8px;
  color: #8B8BF7; font-weight: 700; }
.th-dot { width: 7px; height: 7px; border-radius: 50%; background: #8B8BF7; }
.th-card-when { color: #7E8CA0; letter-spacing: 0.02em; }

.th-grid {
  display: grid; gap: 14px;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
}
.th-side {
  background: #141D2B; border: 1px solid #243149;
  border-radius: 0.7rem; padding: 16px 16px 14px;
}
.th-side-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 10px; border-bottom: 1px solid #223047; padding-bottom: 12px; margin-bottom: 12px;
}
.th-side-name { font-size: 1.02rem; font-weight: 700; color: #E8EEF7; line-height: 1.25; }
.th-side-mgr { font-size: 0.78rem; color: #7E8CA0; margin-top: 3px; }
.th-crown { font-size: 0.9rem; }

.th-pill {
  flex: none; padding: 5px 11px; border-radius: 999px;
  font-size: 0.82rem; font-weight: 700; font-variant-numeric: tabular-nums;
}
.th-pill-pos { background: rgba(0,210,170,0.16); color: #3BE8C6; }
.th-pill-neg { background: rgba(255,77,106,0.16); color: #FF8095; }
.th-pill-zero { background: rgba(126,140,160,0.14); color: #A7B4C6; }

.th-sec {
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.09em;
  margin: 12px 0 8px;
}
.th-sec:first-of-type { margin-top: 0; }
.th-sec-recv { color: #00D2AA; }
.th-sec-sent { color: #FF4D6A; }

.th-asset { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 9px; }
.th-badge {
  flex: none; width: 22px; height: 22px; border-radius: 7px; margin-top: 1px;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 0.85rem; font-weight: 700; line-height: 1;
}
.th-badge-recv { background: rgba(0,210,170,0.16); color: #3BE8C6; }
.th-badge-sent { background: rgba(255,77,106,0.16); color: #FF8095; }
.th-asset-text { display: flex; flex-direction: column; min-width: 0; }
.th-asset-name { font-size: 0.9rem; font-weight: 600; color: #E8EEF7; line-height: 1.3; }
.th-asset-sub { font-size: 0.75rem; color: #7E8CA0; margin-top: 1px; }
.th-empty { color: #55637A; font-size: 0.85rem; margin-bottom: 9px; }
</style>
"""

_MD_SPECIAL = re.compile(r"([\\`*_\[\]$~|<>])")


def md(text) -> str:
    """Make user-supplied text safe to drop into a markdown string.

    League and team names are arbitrary — Sleeper has one here literally named
    'Novi ' with a trailing space, which turns f"**{name}**" into '**Novi **'
    and renders the asterisks instead of bolding. Names containing * _ $ or
    backticks break formatting the same way.
    """
    return _MD_SPECIAL.sub(r"\\\1", str(text or "").strip())


def sign_out():
    for key in ("user", "league_id", "season"):
        st.session_state.pop(key, None)


def lookup_sleeper(handle: str):
    """(user, error_message). Distinguishes 'no such account' from 'couldn't
    reach Sleeper' so a network fault isn't reported as a bad username."""
    try:
        found = sc.get_user(handle)
    except Exception as e:
        return None, f"Couldn't reach Sleeper to look up {handle!r}: {e}"
    if not found or not found.get("user_id"):
        return None, f"No Sleeper account found for {handle!r}. Check the spelling."
    return found, None


# ------------------------------------------------------------- find a league
# No accounts, no passwords: this app only ever reads public Sleeper data and
# never acts on anyone's behalf, so there is nothing to authenticate. The
# username is just how you look up which leagues to analyze.
if "user" not in st.session_state:
    st.title("\U0001F3C8 Sleeper Trade Analyzer")
    st.caption(
        "Trade grades, a manager leaderboard, and a what-if calculator for any "
        "Sleeper league. Read-only \u2014 nothing here changes your team."
    )

    with st.form("find"):
        handle = st.text_input(
            "Your Sleeper username",
            placeholder="e.g. QayfRasul",
            help="Used only to list the leagues you're in.",
        )
        season = st.selectbox("Season", seasons_to_offer())
        submitted = st.form_submit_button("Find my leagues", type="primary")

    if submitted:
        if not handle.strip():
            st.error("Enter your Sleeper username to continue.")
            st.stop()
        found, error = lookup_sleeper(handle)
        if error:
            st.error(error)
            st.stop()
        st.session_state["user"] = found
        st.session_state["season"] = season
        st.rerun()

    st.caption(
        "This app never asks for a password \u2014 not Sleeper's, not its own. It "
        "reads only data your leaguemates can already see, and it cannot send "
        "trades, edit rosters, or change anything in Sleeper."
    )
    st.stop()

user = st.session_state["user"]

# ------------------------------------------------------------ league pick
if "league_id" not in st.session_state:
    st.title(f"Welcome, {md(user.get('display_name'))}")
    season = st.session_state.get("season") or seasons_to_offer()[0]

    try:
        leagues = sc.get_user_leagues(user["user_id"], season)
    except Exception as e:
        st.error(f"Couldn't load your leagues: {e}")
        leagues = []

    st.subheader(f"Choose a league — {season}")
    if not leagues:
        st.warning(f"You aren't in any {season} NFL leagues on Sleeper. Try another season.")
        new_season = st.selectbox("Season", seasons_to_offer(), index=seasons_to_offer().index(season))
        if new_season != season:
            st.session_state["season"] = new_season
            st.rerun()
    else:
        for lg in leagues:
            box = st.container(border=True)
            with box:
                left, right = st.columns([4, 1])
                left.markdown(f"**{md(lg.get('name'))}**")
                left.caption(
                    f"{lg.get('total_rosters')} teams · {lg.get('season')} · "
                    f"{(lg.get('status') or '').replace('_', ' ')}"
                )
                if right.button("Open", key=f"open_{lg['league_id']}", type="primary"):
                    st.session_state["league_id"] = lg["league_id"]
                    st.rerun()

    st.divider()
    st.button("Change user", on_click=sign_out)
    st.stop()

# ------------------------------------------------------------- main app
with st.sidebar:
    st.title("\U0001F3C8 Trade Analyzer")
    if user.get("avatar"):
        st.image(f"https://sleepercdn.com/avatars/thumbs/{user['avatar']}", width=48)
    st.markdown(f"Viewing as **{md(user.get('display_name'))}**")

    if st.button("Switch league"):
        st.session_state.pop("league_id", None)
        st.rerun()
    st.button("Change user", on_click=sign_out)

    st.divider()
    if st.button("Clear cache"):
        shutil.rmtree(sc.CACHE_DIR, ignore_errors=True)
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("Cache cleared.")
    st.caption(
        "Points are computed from real weekly NFL stats using your league's own "
        "scoring settings (not a generic PPR/standard guess)."
    )
    st.caption(
        "Traded draft picks are worth 0 points until the rookie draft happens and "
        "the pick turns into an actual player — then that player's points (from "
        "the draft onward) count toward the trade."
    )

with st.spinner("Pulling league data, transactions, and weekly stats from Sleeper..."):
    try:
        ld, trades, board = load(st.session_state["league_id"], SCHEMA_VERSION)
    except Exception as e:
        st.error(f"Couldn't load league {st.session_state['league_id']!r}: {e}")
        if st.button("Back to my leagues"):
            st.session_state.pop("league_id", None)
            st.rerun()
        st.stop()

my_roster_id = ld.roster_for_user(user["user_id"])

league = ld.league
st.title(md(league.get("name", "League")))
if my_roster_id:
    st.caption(f"Your team in this league: **{md(ld.team_name(my_roster_id))}**")
else:
    st.caption("You don't have a roster in this league — viewing in read-only mode.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Season", league.get("season"))
c2.metric("Current week", ld.state.get("week"))
c3.metric("Trades found", len(trades))
c4.metric("Teams", league.get("total_rosters") or len(ld.rosters))

tab_board, tab_trades, tab_machine = st.tabs(
    ["\U0001F3C6 Trader Leaderboard", "\U0001F504 Trade History", "\U0001F9EE Trade Calculator"]
)

with tab_board:
    if not board:
        st.info("No completed trades found for this league yet.")
    else:
        df = pd.DataFrame(board)[
            ["rank", "team_name", "manager", "net_points", "trades", "wins", "losses", "pushes"]
        ]
        df.columns = ["Rank", "Team", "Manager", "Net Points", "Trades", "Won", "Lost", "Even"]

        st.subheader("Net trade points (best trader → worst)")

        # Sleeper's own accents: mint-teal for the top of the board fading to
        # its live-coral at the bottom. Tinted at low alpha so the rows stay
        # dark enough for light text to read cleanly.
        GREEN = (0, 210, 170)   # #00D2AA
        RED = (255, 77, 106)    # #FF4D6A

        def _rank_color(rank: int, total: int) -> str:
            t = (rank - 1) / max(total - 1, 1)
            r = round(GREEN[0] + (RED[0] - GREEN[0]) * t)
            g = round(GREEN[1] + (RED[1] - GREEN[1]) * t)
            b = round(GREEN[2] + (RED[2] - GREEN[2]) * t)
            return f"background-color: rgba({r},{g},{b},0.22)"

        def _highlight(row):
            return [_rank_color(row["Rank"], len(df))] * len(row)

        st.dataframe(
            df.style.apply(_highlight, axis=1).format({"Net Points": "{:+.2f}"}),
            width="stretch",
            hide_index=True,
            height=(len(df) + 1) * 35 + 3,
        )

with tab_trades:
    if not trades:
        st.info("No completed trades found for this league yet.")
    else:
        all_teams = sorted({s.team_name for t in trades for s in t.sides})
        all_weeks = sorted({t.week for t in trades})

        f1, f2, f3 = st.columns([2, 1, 2])
        with f1:
            team_filter = st.multiselect("Team", all_teams, placeholder="All teams")
        with f2:
            week_filter = st.multiselect("Week", all_weeks, placeholder="All weeks")
        with f3:
            player_filter = st.text_input("Player", placeholder="Search by player name")

        def _trade_matches(t) -> bool:
            if team_filter and not any(s.team_name in team_filter for s in t.sides):
                return False
            if week_filter and t.week not in week_filter:
                return False
            needle = player_filter.strip().lower()
            if needle:
                hit = any(
                    needle in a.label.lower() or needle in (a.detail or "").lower()
                    for s in t.sides
                    for a in (s.received + s.sent)
                )
                if not hit:
                    return False
            return True

        filtered = [t for t in trades if _trade_matches(t)]
        st.caption(f"Showing {len(filtered)} of {len(trades)} trades")

        if (team_filter or week_filter or player_filter) and not filtered:
            st.info("No trades match these filters.")

        st.markdown(TRADE_CARD_CSS, unsafe_allow_html=True)

        def _pill(net: float) -> str:
            tone = "zero" if abs(net) < 1e-9 else ("pos" if net > 0 else "neg")
            return f'<span class="th-pill th-pill-{tone}">{net:+.2f}</span>'

        def _asset_row(a, direction: str) -> str:
            sign = "+" if direction == "recv" else "−"
            sub = " · ".join(bit for bit in (esc(a.meta), f"{a.points:+.2f} pts") if bit)
            return (
                f'<div class="th-asset">'
                f'<span class="th-badge th-badge-{direction}">{sign}</span>'
                f'<span class="th-asset-text">'
                f'<span class="th-asset-name">{esc(a.name)}</span>'
                f'<span class="th-asset-sub">{sub}</span>'
                f"</span></div>"
            )

        def _side_panel(s, is_winner: bool) -> str:
            crown = ' <span class="th-crown">\U0001F451</span>' if is_winner else ""
            out = [
                '<div class="th-side">',
                '<div class="th-side-head"><div>',
                f'<div class="th-side-name">{esc(s.team_name)}{crown}</div>',
                f'<div class="th-side-mgr">{esc(s.manager)}</div>',
                f"</div>{_pill(s.net)}</div>",
            ]
            for direction, assets in (("recv", s.received), ("sent", s.sent)):
                word = "RECEIVED" if direction == "recv" else "SENT"
                out.append(f'<div class="th-sec th-sec-{direction}">{word} · {len(assets)}</div>')
                if assets:
                    out += [_asset_row(a, direction) for a in assets]
                else:
                    out.append('<div class="th-empty">—</div>')
            out.append("</div>")
            return "".join(out)

        for t in sorted(filtered, key=lambda t: t.created, reverse=True):
            stamp = dt.datetime.fromtimestamp(t.created / 1000)
            winner = t.winner
            teams = " ↔ ".join(md(s.team_name) for s in t.sides)
            verdict = (
                f"\U0001F451 Current winner: {md(winner.team_name)}"
                if winner
                else "Even so far — no points separate these sides yet"
            )
            label = (
                f"Week {t.week:02d} · {stamp.strftime('%b %d, %Y · %H:%M')} · "
                f"{teams} — {verdict}"
            )

            with st.expander(label):
                panels = "".join(
                    _side_panel(s, bool(winner) and s.roster_id == winner.roster_id)
                    for s in t.sides
                )
                st.markdown(
                    '<div class="th-card">'
                    '<div class="th-card-head">'
                    '<span class="th-status"><span class="th-dot"></span>COMPLETED</span>'
                    f'<span class="th-card-when">{esc(stamp.strftime("%b %d · %H:%M"))}</span>'
                    "</div>"
                    f'<div class="th-grid">{panels}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )


@st.cache_resource(show_spinner=False)
def load_ktc(superflex: bool, te_premium: bool):
    return KTCValues(superflex=superflex, te_premium=te_premium)


with tab_machine:
    st.caption(
        "Score any hypothetical trade between two teams in this league. "
        "Nothing is sent or saved — this is a what-if calculator."
    )

    try:
        ktc = load_ktc(ld.is_superflex, ld.has_te_premium)
    except Exception as e:
        st.error(f"Couldn't load KeepTradeCut values: {e}")
        st.stop()

    fmt = "Superflex" if ld.is_superflex else "1QB"
    tep = " + TE premium" if ld.has_te_premium else ""
    st.caption(f"Using KeepTradeCut **{fmt}{tep}** dynasty values, auto-matched to your league settings.")

    picks_by_roster = ld.owned_picks()
    roster_ids = sorted(ld.roster_by_id)
    labels = {rid: f"{ld.team_name(rid)} ({ld.manager_name(rid)})" for rid in roster_ids}

    def asset_options(roster_id: int) -> dict[str, ValuedAsset]:
        """Every tradeable asset this roster controls, best value first."""
        options: dict[str, ValuedAsset] = {}

        for pid in (ld.roster_by_id[roster_id].get("players") or []):
            player = ld.players.get(pid) or {}
            name = player.get("full_name") or f"{player.get('first_name','')} {player.get('last_name','')}".strip()
            if not name:
                continue
            value = ktc.player_value(name)
            pos = player.get("position") or "?"
            team = player.get("team") or "FA"
            if value is None:
                options[f"{name} ({pos}-{team}) — unranked"] = ValuedAsset(
                    f"{name} ({pos})", 0, ranked=False, note="outside KTC's top 500"
                )
            else:
                options[f"{name} ({pos}-{team}) — {value}"] = ValuedAsset(f"{name} ({pos})", value)

        for season, rnd, original in picks_by_roster.get(roster_id, []):
            value, used = ktc.pick_value(season, rnd)
            origin = "" if original == roster_id else f" via {ld.team_name(original)}"
            ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(rnd, f"{rnd}th")
            base = f"{season} {ordinal}{origin}"
            if value is None:
                options[f"{base} — unranked"] = ValuedAsset(base, 0, ranked=False, note="not valued by KTC")
            else:
                options[f"{base} — {value}"] = ValuedAsset(base, value, note=used)

        return dict(sorted(options.items(), key=lambda kv: kv[1].value, reverse=True))

    # Any two teams — this grades a hypothetical, it doesn't act for anyone.
    default_a = roster_ids.index(my_roster_id) if my_roster_id in roster_ids else 0

    col_you, col_them = st.columns(2)
    with col_you:
        your_id = st.selectbox("Team A", roster_ids, index=default_a,
                               format_func=lambda r: labels[r],
                               key=f"tm_a_{ld.league_id}")
    with col_them:
        others = [r for r in roster_ids if r != your_id]
        their_id = st.selectbox("Team B", others, format_func=lambda r: labels[r],
                                key=f"tm_b_{ld.league_id}")

    your_options = asset_options(your_id)
    their_options = asset_options(their_id)

    # Scope the asset pickers to the specific rosters they're drawn from, so
    # changing a team (or league) starts clean instead of carrying over
    # selections that are no longer valid options.
    send_key = f"tm_a_assets_{ld.league_id}_{your_id}"
    recv_key = f"tm_b_assets_{ld.league_id}_{their_id}"

    pick_col1, pick_col2 = st.columns(2)
    with pick_col1:
        st.markdown(f"**{md(ld.team_name(your_id))} gives up**")
        send = st.multiselect("Team A assets", list(your_options), key=send_key,
                              label_visibility="collapsed", placeholder="Choose players or picks")
    with pick_col2:
        st.markdown(f"**{md(ld.team_name(their_id))} gives up**")
        recv = st.multiselect("Team B assets", list(their_options), key=recv_key,
                              label_visibility="collapsed", placeholder="Choose players or picks")

    if not send and not recv:
        st.info("Pick at least one asset on each side to grade the trade.")
    else:
        you_side = SideValuation([your_options[k] for k in send])
        them_side = SideValuation([their_options[k] for k in recv])
        verdict = evaluate(you_side, them_side)

        name_a, name_b = ld.team_name(your_id), ld.team_name(their_id)
        md_a, md_b = md(name_a), md(name_b)
        m1, m2, m3 = st.columns(3)
        m1.metric(f"{name_a} gives up", f"{you_side.adjusted_total:,.0f}")
        m2.metric(f"{name_a} gets back", f"{them_side.adjusted_total:,.0f}")
        m3.metric(f"Net to {name_a}",
                  f"{them_side.adjusted_total - you_side.adjusted_total:+,.0f}",
                  delta=f"{verdict.pct_diff:+.1f}%")

        # The verdict is written from Team A's perspective; say so plainly now
        # that neither side is necessarily "you".
        winner = md(name_a) if verdict.pct_diff > 0 else md(name_b)
        if abs(verdict.pct_diff) < 3:
            st.info(f"**Even trade.** The two sides are within {abs(verdict.pct_diff):.1f}% "
                    f"of each other — decide it on roster fit, not value.")
        elif abs(verdict.pct_diff) < 8:
            st.info(f"**Slight edge to {winner}** — {abs(verdict.pct_diff):.1f}%. "
                    "Close enough that both sides can reasonably say yes.")
        elif abs(verdict.pct_diff) < 18:
            st.success(f"**Clear win for {winner}** — {abs(verdict.pct_diff):.1f}%. "
                       "The other side should ask for another piece.")
        else:
            st.error(f"**Lopsided toward {winner}** — {abs(verdict.pct_diff):.1f}%. "
                     "Realistically this gets rejected.")

        unranked = [a for a in you_side.assets + them_side.assets if not a.ranked]
        if unranked:
            st.caption(
                "Counted as 0: " + ", ".join(md(a.label) for a in unranked)
                + " — outside KeepTradeCut's ranked pool, so their dynasty trade value is negligible."
            )

        with st.expander("How this was calculated"):
            st.markdown(
                f"- Raw KTC totals: **{md_a} {you_side.raw_total:,}** vs "
                f"**{md_b} {them_side.raw_total:,}**.\n"
                f"- Adjusted totals apply a depth discount (each extra piece counts ~8% less than the "
                f"one above it), because three good players aren't worth one great one in a league "
                f"where you can only start so many.\n"
                f"- Future picks are valued at the neutral **Mid** slot — no one knows final standings yet."
            )

st.divider()
st.caption(
    "Data via the public Sleeper API. Player points reflect games played from the trade's "
    "week through the most recently completed week. Weeks beyond the regular season "
    f"({MAX_WEEK}) aren't scanned."
)
