"""Hourly evaluation of Proposition 1 across the frozen price panel, the DURATION and
SPECIFICITY of the impossibility region, not a density claim.

The headline result evaluates the rational-pricing bound at a single point (the trough
print, q = 1.5416 > 1). The frozen snapshot already carries 639 crisis coin-hours
(USDC/DAI/USDT) plus 2,086 excluded-coin hours over calm + crisis windows, so the same
bound can be evaluated at EVERY hour with no new data, no MANIFEST change, and no
re-pull. That yields three things the point estimate cannot:

  1. DURATION   -- the impossibility region is a dated, contiguous window with an onset,
                   a peak and a resolution, not an instant.
  2. ORDERING   -- breaching-hour COUNTS across coins (USDC > DAI > USDT = 0) restate the
                   placebo as a rate comparison rather than a trough comparison.
  3. SPECIFICITY-- USDT's worst hour sits an order of magnitude below the firing
                   threshold, and the excluded coins never breach the 1-phi floor at all.

WHAT THIS IS NOT. Hourly prices inside a single episode are heavily autocorrelated, so
212 hourly observations are NOT 212 independent observations. effective_n() reports the
AR(1)-adjusted sample size (~4 for USDC) and it is dumped alongside every count in
report(). Any use of these series as an "evidence base" must quote the effective N in the
same breath as the raw hour count. The bound still rests on ONE episode and ONE disclosed
phi; this module measures how long the impossibility persisted, not how much independent
evidence supports it.

Offline / keyless. Reads only the frozen snapshot.
"""
import numpy as np
import pandas as pd

from constants import DATA_DIR, PHI_SVB
import rational_bound as rb

CRISIS_FILE = DATA_DIR / "price_hourly.csv"
EXCLUDED_FILE = DATA_DIR / "excluded_coins_hourly.csv"


def _series(symbol: str) -> pd.DataFrame:
    """Frozen hourly price series for one crisis-window coin, ascending by timestamp."""
    px = pd.read_csv(CRISIS_FILE)
    g = px.loc[px["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
    if g.empty:
        raise ValueError(f"no frozen series for {symbol!r}")
    return g


def _share(p_obs: float, phi: float) -> float:
    """below_floor_share guarded for at/above-par prices, which the point estimator
    rejects (it is defined only for a de-pegged print). At or above par nothing lies
    below the floor, so the non-fundamental share is zero by construction."""
    return rb.below_floor_share(p_obs, phi) if p_obs < 1.0 else 0.0


def hourly_bound(symbol: str, phi: float = PHI_SVB) -> pd.DataFrame:
    """Proposition 1 evaluated at every frozen hour: implied unremediated-loss
    probability q and below-floor non-fundamental share. Breaches where q > 1."""
    g = _series(symbol)
    g["q"] = g["price"].apply(lambda x: rb.implied_failure_prob(x, phi))
    g["below_floor_share"] = g["price"].apply(lambda x: _share(x, phi))
    g["breaches"] = g["q"] > 1.0
    return g


def effective_n(symbol: str) -> dict:
    """AR(1)-adjusted effective sample size, the honesty statistic.

    Kish-style n_eff = n * (1 - r1) / (1 + r1) on the price level. Hourly prices within
    one episode are near-unit-root, so n_eff collapses far below n. This exists so the
    hour counts can never be quoted as an evidence base without their discount.
    """
    x = _series(symbol)["price"].to_numpy()
    r1 = float(np.corrcoef(x[:-1], x[1:])[0, 1])
    n_eff = float(len(x) * (1.0 - r1) / (1.0 + r1)) if r1 < 1.0 else float("nan")
    return {"n_hours": int(len(x)), "lag1_autocorr": round(r1, 4),
            "effective_n": round(n_eff, 1)}


def impossibility_region(symbol: str = "USDC", phi: float = PHI_SVB) -> dict:
    """Onset, peak, resolution and duration of the region where the bound fires."""
    g = hourly_bound(symbol, phi)
    br = g.loc[g["breaches"]]
    if br.empty:
        return {"symbol": symbol, "breaches": False, "breaching_hours": 0,
                "max_q": round(float(g["q"].max()), 4)}
    peak = g.loc[g["q"].idxmax()]
    iso = lambda ts: pd.to_datetime(int(ts), unit="s", utc=True).isoformat()
    first, last = int(br["timestamp"].iloc[0]), int(br["timestamp"].iloc[-1])
    return {
        "symbol": symbol,
        "breaches": True,
        "breaching_hours": int(len(br)),
        "hours_observed": int(len(g)),
        "firing_rate": round(len(br) / len(g), 4),
        "first_breach_utc": iso(first),
        "peak_utc": iso(peak["timestamp"]),
        "last_breach_utc": iso(last),
        "span_hours": round((last - first) / 3600.0, 1),
        "contiguous": bool(len(br) == round((last - first) / 3600.0) + 1),
        "max_q": round(float(peak["q"]), 4),
        "max_below_floor_share": round(float(g["below_floor_share"].max()), 4),
        "trough_price": round(float(g["price"].min()), 6),
    }


def floor_specificity(phi: float = PHI_SVB) -> dict:
    """Do the EXCLUDED coins (USDP/GUSD/BUSD) ever breach the 1-phi floor?

    Distinct from placebo.excluded_coins(), which applies the 1% BREAK_THRESHOLD (0.99).
    This applies the BOUND's floor (1 - phi = 0.92). A coin can cross the 1% break test
    without coming near the impossibility floor, and all three do exactly that.
    """
    px = pd.read_csv(EXCLUDED_FILE)
    floor = rb.floor_worstcase(phi)
    out = {"floor": round(floor, 4), "phi": phi, "coins": {}}
    for sym in sorted(px["symbol"].unique()):
        per = {}
        for win in sorted(px.loc[px["symbol"] == sym, "window"].unique()):
            s = px.loc[(px["symbol"] == sym) & (px["window"] == win), "price"]
            per[win] = {"n_hours": int(len(s)), "min_price": round(float(s.min()), 6),
                        "hours_below_floor": int((s < floor).sum())}
        out["coins"][sym] = per
    out["total_hours"] = int(len(px))
    out["total_hours_below_floor"] = int((px["price"] < floor).sum())
    return out


def report(phi: float = PHI_SVB) -> dict:
    """Full hourly-window result. Every hour count is paired with its effective N."""
    coins = {}
    for sym in ("USDC", "DAI", "USDT"):
        g = hourly_bound(sym, phi)
        coins[sym] = {
            "hours_observed": int(len(g)),
            "breaching_hours": int(g["breaches"].sum()),
            "firing_rate": round(float(g["breaches"].mean()), 4),
            "min_price": round(float(g["price"].min()), 6),
            "max_q": round(float(g["q"].max()), 4),
            "max_below_floor_share": round(float(g["below_floor_share"].max()), 4),
            "independence": effective_n(sym),
        }
    return {
        "phi": phi,
        "floor_worstcase": round(rb.floor_worstcase(phi), 4),
        "coins": coins,
        "impossibility_region_usdc": impossibility_region("USDC", phi),
        "excluded_coin_floor_check": floor_specificity(phi),
        "interpretation": (
            "DURATION and SPECIFICITY result, NOT an evidence-density result. Hourly "
            "prices within one episode are near-unit-root; see coins[*].independence for "
            "the AR(1)-adjusted effective sample size, which is roughly 4 for USDC. The "
            "identification still rests on one episode and one disclosed phi."
        ),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(report(), indent=2))
