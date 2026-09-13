# Sleeper Trade Analyzer

Analyzes every trade in a Sleeper fantasy league and ranks managers by net
fantasy points gained/lost from trading.

## No accounts, by design

There is no login, and there should not be one. The app is **strictly
read-only**: every Sleeper call is a `GET`, it never writes anything, and it
never acts on anyone's behalf. There is no privileged action to protect, so
there is nothing to authenticate — and no credentials to store, leak, or get
wrong.

You type a Sleeper username only so the app can list which leagues to
analyze:

- `GET /v1/user/<username>` resolves the account to a `user_id` (public).
- `GET /v1/user/<user_id>/leagues/nfl/<season>` lists that user's leagues.

Everything it shows is data your leaguemates can already see in Sleeper.

**Never add a "Sleeper password" field.** Sleeper's public API has no login,
token, OAuth, or session endpoint — every one of those paths 404s, and the
user object nulls out `email`, `phone`, and `token`. There is nothing a
Sleeper password could be checked against, so such a box could only ever
collect a credential it cannot verify. That is a phishing pattern, not a
login.

**If you ever add a feature that acts for a user**, this calculus changes and
you need real accounts again. An earlier version of this app had them
(scrypt hashes, server-side lockout, Postgres-backed store, and Sleeper
ownership verification via a one-time team-name code). Re-add that before
shipping any write capability, not after.

## Theme

`.streamlit/config.toml` carries a palette taken from sleeper.com:

| Role | Colour | Where it shows |
| --- | --- | --- |
| Canvas | `#0D141F` | page background |
| Panels | `#1A2434` | inputs, expanders, cards |
| Sidebar | `#0A1017` | sits darker than the page, as Sleeper's does |
| Primary | `#00D2AA` | mint-teal — active tab, buttons, focus rings, chips |
| Secondary | `#8B8BF7` | periwinkle — `st.info` (Sleeper's "TRADE" button colour) |
| Alert | `#FF4D6A` | coral — `st.error`, negative deltas (its LIVE badge) |

Buttons are full pills and panels use a 0.7rem radius, matching Sleeper's
shapes. The leaderboard gradient runs mint → coral by rank, tinted at 0.22
alpha so rows stay dark enough for light text.

Theme changes only load at **server start** — restart Streamlit after editing
the config, a hot reload won't pick them up.

## How it works

- Pulls league users/rosters, all `type == "trade"` transactions, traded
  draft picks, and the league's draft results from the [Sleeper API](https://docs.sleeper.com).
- For each traded player, sums real weekly fantasy points scored **from the
  trade's week through the most recently completed week**, using the
  league's own `scoring_settings` (not a generic PPR/standard guess).
- For each traded **draft pick**, the pick is worth 0 points until the
  rookie draft happens and it resolves to an actual player (matched via the
  league's draft `slot_to_roster_id` mapping); after that, the drafted
  player's points from the draft onward count toward the trade.
- Per trade, each side's net = points received − points sent. The side with
  the higher net "wins" the trade.
- The leaderboard sums each manager's net across all their trades and ranks
  best trader → worst. Every roster appears, including ones that have never
  traded (they sit at 0).

## Trade History

Each trade is a collapsible card.

- **Collapsed** shows week, date, the teams involved, and the verdict as
  *"👑 Current winner: &lt;team&gt;"* — not raw net numbers. Trades where no
  points separate the sides yet read *"Even so far"*.
- **Expanded** gives one panel per team (a CSS grid, so 2- and 3-team trades
  both lay out cleanly): team name with a crown if it's currently winning,
  manager beneath, and a net pill — mint when positive, coral when negative,
  grey at exactly zero.
- Inside each panel, `RECEIVED` and `SENT` sections list every asset with a
  +/− badge, the name on top and a muted line beneath carrying position and
  team (`WR · LAR`) or pick origin (`Not yet drafted`), plus the points that
  asset has scored since the trade.

`Asset` carries `name` and `meta` separately for this; `Asset.label` is a
derived one-line form still used by the player search filter.

## Trade Calculator

A third tab grades a **hypothetical** trade between any two teams in the
league using [KeepTradeCut](https://keeptradecut.com) dynasty values. It is a
what-if tool: nothing is sent, offered, or saved anywhere.

- Both sides are freely selectable — it evaluates a scenario, it doesn't act
  for anyone. The verdict names the team it favours rather than saying "you".
- Values auto-match the league's shape: **superflex** values when the roster
  has a `SUPER_FLEX` slot, and **TE-premium** values when scoring has
  `bonus_rec_te`. Detected per league.
- Each side's assets are its real Sleeper roster plus the future rookie picks
  it currently owns (base ownership adjusted by the `traded_picks` feed).
- Totals apply a **depth discount** — each additional piece counts ~8% less
  than the one above it — because three good players aren't worth one great
  one when you can only start so many. Both raw and adjusted totals are shown.
- Future picks are valued at the neutral **Mid** slot, since no one knows
  final standings yet. Picks past KTC's horizon fall back to its furthest-out
  season; players outside KTC's ranked pool count as 0 and are called out.
- Changing a team clears that side's asset picks, so one team's players can
  never linger in a scenario built for another.

## Deploying it for your league

There is no database, no secrets, and no per-user state — the app only reads
public Sleeper data — so deployment is just "run the Streamlit app".

1. Push to GitHub (public or private, your call — nothing sensitive is in the
   repo).
2. Deploy on [Streamlit Community Cloud](https://share.streamlit.io): point it
   at the repo, main file `app.py`. Free, with HTTPS.
3. Share the URL. Anyone in the league types their Sleeper username and picks
   a league.

Notes:

- The ~17MB cache (mostly Sleeper's player dump) lives on local disk and is
  rebuilt on first request after a restart. On a host with an ephemeral disk
  that means the first visitor after a restart waits a few seconds; everyone
  after that hits it warm. Nothing is lost when it's wiped.
- Anyone with the URL can look up any league. That's the same visibility the
  Sleeper API already gives the public, but if you'd rather not have it open,
  put it behind whatever access control your host provides.

## Run it

```bash
cd /Users/qayf/Desktop/fantasy-trade-analyzer
./venv/bin/streamlit run app.py
```

Then open the URL Streamlit prints (usually http://localhost:8501), enter
your Sleeper username, and pick a league. Use **Switch league** to jump
between leagues, or **Change user** to look up someone else.

## Notes / limitations

- Analysis is scoped to one `league_id` (one season) at a time. Dynasty
  leagues get a new `league_id` each season (linked via
  `previous_league_id`); the season picker on the first screen lets you reach the
  older ones, but totals aren't carried across seasons.
- Weekly stats and the player directory are cached to disk under `cache/`
  to avoid hammering Sleeper. Use the sidebar "Clear cache" button, or
  delete the `cache/` folder, to force a full refresh.
- A trade's points start accruing from the week it was processed, not from
  the exact moment during that week it happened.
- KTC has no public API, so values are read from the `playersArray` embedded
  in its rankings page and cached for 24h. If that page's layout ever
  changes, the Trade Calculator reports the failure and falls back to the last
  cached values rather than showing wrong numbers.
- KTC ranks ~500 players. Kickers, defenses, and deep-bench players aren't
  ranked; they're treated as 0 dynasty value, which is approximately true.
