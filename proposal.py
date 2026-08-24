"""Valuing a proposed trade with KeepTradeCut dynasty values.

Two things matter beyond looking values up:

1. Values don't add linearly. Three 4000-value pieces are not worth one
   12000-value piece — you can only start so many players, and roster spots
   are finite. We apply a depth discount so extra pieces count for
   progressively less, which is the "consolidation premium" every dynasty
   trade calculator models in some form.

2. Future picks have no known slot, so they're valued at the neutral 'Mid'
   tier rather than pretending we know a team's final standing.
"""
from __future__ import annotations

from dataclasses import dataclass

# Each additional piece beyond the best one contributes progressively less.
DEPTH_DECAY = 0.92


@dataclass
class ValuedAsset:
    label: str
    value: int
    ranked: bool = True
    note: str = ""


@dataclass
class SideValuation:
    assets: list[ValuedAsset]

    @property
    def raw_total(self) -> int:
        return sum(a.value for a in self.assets)

    @property
    def adjusted_total(self) -> float:
        ordered = sorted((a.value for a in self.assets), reverse=True)
        return round(sum(v * (DEPTH_DECAY ** i) for i, v in enumerate(ordered)), 1)


@dataclass
class Verdict:
    headline: str
    detail: str
    tone: str  # "good" | "fair" | "bad"
    pct_diff: float


def evaluate(you: SideValuation, them: SideValuation) -> Verdict:
    """Judge the trade from *your* perspective: you receive `them`'s assets
    and give up `you`'s."""
    getting = them.adjusted_total
    giving = you.adjusted_total
    delta = getting - giving
    larger = max(getting, giving)

    if larger <= 0:
        return Verdict(
            headline="Nothing to evaluate",
            detail="Add assets to both sides to get a recommendation.",
            tone="fair",
            pct_diff=0.0,
        )

    pct = (delta / larger) * 100
    magnitude = abs(pct)

    if magnitude < 3:
        return Verdict(
            "Fair trade — go for it",
            f"The two sides are within {magnitude:.1f}% of each other. This is the kind "
            "of deal that gets accepted; decide it on roster fit, not value.",
            "fair", pct,
        )
    if magnitude < 8:
        if delta > 0:
            return Verdict(
                "Slight win for you — worth offering",
                f"You come out about {magnitude:.1f}% ahead. Close enough that the other "
                "manager can still reasonably say yes.",
                "good", pct,
            )
        return Verdict(
            "Slight overpay — acceptable if you need the fit",
            f"You give up about {magnitude:.1f}% more than you get. Defensible if it fills "
            "a real hole in your lineup.",
            "fair", pct,
        )
    if magnitude < 18:
        if delta > 0:
            return Verdict(
                "Clear win for you — but expect pushback",
                f"You gain roughly {magnitude:.1f}%. Good for you, so don't be surprised "
                "if it gets declined or countered.",
                "good", pct,
            )
        return Verdict(
            "You're losing this one",
            f"You give up roughly {magnitude:.1f}% more value than you receive. "
            "Ask for another piece to balance it.",
            "bad", pct,
        )
    if delta > 0:
        return Verdict(
            "Lopsided in your favor — unlikely to be accepted",
            f"You'd gain about {magnitude:.1f}%. Realistically this gets rejected; "
            "consider adding a sweetener if you actually want it to land.",
            "good", pct,
        )
    return Verdict(
        "Don't make this trade",
        f"You'd give up about {magnitude:.1f}% more than you get back. This is a clear loss.",
        "bad", pct,
    )
