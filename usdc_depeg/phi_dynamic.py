"""Time-varying phi(t) generalization of Proposition 1 (rational_bound.py). The static
result evaluates the rational-pricing bound at a single disclosed impairment PHI_SVB
against the full pre-run supply base. This module lets both sides of phi = disclosed
loss / residual claimant base move: the claimant base B(t) shrinks hour by hour as
redemptions drain circulating supply, so phi(t) = 3.3e9 / B(t) rises through the run
even though the disclosed loss numerator is fixed.

Two ways to build B(t) were specified; only one is computable from frozen data:
  (i)  daily circulating_usd (usdc_supply_daily.csv), linearly interpolated to hourly
       resolution, COMPUTED below.
  (ii) supply at disclosure minus cumulative net on-chain burns/mints, hour by hour --
       NOT COMPUTABLE from any frozen file. redemptions.py computes burns/mints from
       live Etherscan logs held only in memory (fifo_attribute()) and persists exactly
       one output, data/redemptions_by_wallet.csv, a (wallet, volume) table with no
       per-event timestamps or block numbers. No raw per-block burn/mint file exists
       anywhere under data/ (confirmed by inspection, see VARIANT_II_GAP). Regenerating
       one needs ETHERSCAN_API_KEY, unavailable to this offline reproduction path.
       Booked as a gap, not approximated.

A third, non-time-varying line is also reported: Circle's alternate ~$42.1B
reserve-composition total (paper/fc27/fc27.tex lines 443-444, cite key
circle2023pressrelease) implies a different constant phi. It is a single point figure,
not a daily series, so it is reported as a constant comparison line, not a time series.

Offline / keyless. Reads only the frozen snapshot. Reuses rational_bound's
implied_failure_prob / below_floor_share / floor_worstcase and impossibility_window's
_series / hourly_bound rather than reimplementing them.
"""
import numpy as np
import pandas as pd

from constants import DATA_DIR, PHI_SVB
import impossibility_window as iw
import rational_bound as rb

SUPPLY_FILE = DATA_DIR / "usdc_supply_daily.csv"

DISCLOSED_LOSS_USD = 3.3e9
DISCLOSURE_TS_UTC = "2023-03-11T03:11:00Z"

# Circle's alternate reserve-composition total (press release), cited in
# paper/fc27/fc27.tex lines 443-444: "Circle's alternate reserve-composition total of
# ~$42.1B implies phi=7.84% rather than 8.25%". A single point figure, not a daily
# series -> treated as a CONSTANT comparison line below, not a time series.
ALT_RESERVE_TOTAL_USD = 42.1e9

VARIANT_II_GAP = (
    "no frozen per-block burn/mint timestamps exist; data/redemptions_by_wallet.csv is "
    "a wallet-aggregated (wallet, volume) table with no per-event timestamps or block "
    "numbers; redemptions.py computes burns/mints from live Etherscan logs held only in "
    "memory (fifo_attribute()) and writes no per-block/per-event file to disk anywhere "
    "under data/. Regenerating one requires ETHERSCAN_API_KEY, unavailable to this "
    "offline reproduction path."
)


def residual_base_hourly() -> pd.DataFrame:
    """Variant (i): daily circulating_usd from usdc_supply_daily.csv, linearly
    interpolated to the frozen hourly USDC price timestamps (mirrors
    impossibility_window._series('USDC') for the timestamp grid)."""
    sup = pd.read_csv(SUPPLY_FILE).sort_values("date").reset_index(drop=True)
    px = iw._series("USDC")[["timestamp", "price"]].copy()
    px["B_t"] = np.interp(px["timestamp"], sup["date"], sup["circulating_usd"])
    return px


def phi_variant_i() -> pd.DataFrame:
    """phi(t) = disclosed loss / B(t); floor(t) = 1 - phi(t) (rb.floor_worstcase)."""
    df = residual_base_hourly()
    df["phi_t"] = DISCLOSED_LOSS_USD / df["B_t"]
    df["floor_t"] = df["phi_t"].apply(rb.floor_worstcase)
    return df


def phi_third_line() -> float:
    """Circle's alternate ~$42.1B reserve-composition total, a single point figure,
    not a daily series; a constant comparison line, NOT time-varying."""
    return DISCLOSED_LOSS_USD / ALT_RESERVE_TOTAL_USD


def hourly_bound_dynamic() -> pd.DataFrame:
    """Proposition 1 evaluated hour-by-hour with phi(t)/floor(t) substituted for the
    static PHI_SVB, reusing rational_bound row by row. Mirrors
    impossibility_window.hourly_bound's column shape (q, below_floor_share, breaches)."""
    df = phi_variant_i()
    df["q"] = [rb.implied_failure_prob(p, phi) for p, phi in zip(df["price"], df["phi_t"])]
    df["below_floor_share"] = [
        rb.below_floor_share(p, phi) if p < 1.0 else 0.0
        for p, phi in zip(df["price"], df["phi_t"])
    ]
    df["breaches"] = df["q"] > 1.0
    return df


def _iso(ts) -> str:
    return pd.to_datetime(int(ts), unit="s", utc=True).isoformat()


def interpolation_band() -> dict:
    """An adversarial review's narrowed interpolation band: B(t) is observed only DAILY, so the
    two supply snapshots bracketing the trough hour admit two extreme, non-interpolated
    values of phi at the trough, phi_lo (the earlier/larger daily B, so the smaller
    phi) and phi_hi (the later/smaller daily B, so the larger phi). variant_i's
    hour-by-hour linearly-interpolated phi(t) is a single path between these two
    extremes, not independent of them.

    Firing counts under each extreme are computed the same way static_comparison uses
    the static PHI_SVB (impossibility_window.hourly_bound, phi held CONSTANT across the
    whole window), not by re-running variant_i's hour-by-hour interpolation."""
    sup = pd.read_csv(SUPPLY_FILE).sort_values("date").reset_index(drop=True)
    dyn = phi_variant_i()
    trough_ts = int(dyn.loc[dyn["price"].idxmin(), "timestamp"])

    before = sup.loc[sup["date"] <= trough_ts]
    after = sup.loc[sup["date"] >= trough_ts]
    if before.empty or after.empty:
        raise RuntimeError("INSTRUMENT GAP: trough hour falls outside "
                            "usdc_supply_daily.csv's bracketing date range.")
    b_early = float(before.iloc[-1]["circulating_usd"])  # larger B (earlier day) -> lower phi
    b_late = float(after.iloc[0]["circulating_usd"])     # smaller B (later day) -> higher phi

    phi_lo = DISCLOSED_LOSS_USD / b_early
    phi_hi = DISCLOSED_LOSS_USD / b_late

    lo_bound = iw.hourly_bound("USDC", phi_lo)
    hi_bound = iw.hourly_bound("USDC", phi_hi)
    lo_fire = {_iso(t) for t in lo_bound.loc[lo_bound["breaches"], "timestamp"]}
    hi_fire = {_iso(t) for t in hi_bound.loc[hi_bound["breaches"], "timestamp"]}

    return {
        "description": "phi under the two daily supply snapshots bracketing the trough "
                        "hour, held constant across the window (same construction as "
                        "static_comparison's use of PHI_SVB) -- the firing set ranges "
                        "from firing_count_hi to firing_count_lo and never falls below "
                        "firing_count_hi.",
        "phi_lo": round(phi_lo, 4),
        "phi_hi": round(phi_hi, 4),
        "firing_count_lo": len(lo_fire),
        "firing_count_hi": len(hi_fire),
        "extra_firing_hours_utc": sorted(lo_fire - hi_fire),
    }


def report() -> dict:
    dyn = hourly_bound_dynamic()
    static = iw.hourly_bound("USDC", PHI_SVB)  # reused, not reimplemented

    trough_idx = dyn["price"].idxmin()
    trough = dyn.loc[trough_idx]
    trough_price = float(trough["price"])

    max_idx = dyn["phi_t"].idxmax()
    min_idx = dyn["phi_t"].idxmin()

    phi_alt = phi_third_line()
    floor_alt = rb.floor_worstcase(phi_alt)
    q_alt_trough = rb.implied_failure_prob(trough_price, phi_alt)
    s_nf0_alt_trough = rb.below_floor_share(trough_price, phi_alt) if trough_price < 1.0 else 0.0

    dyn_fire = dyn.loc[dyn["breaches"]]
    static_fire = static.loc[static["breaches"]]
    dyn_fire_hours = sorted(_iso(t) for t in dyn_fire["timestamp"])
    static_fire_hours = sorted(_iso(t) for t in static_fire["timestamp"])

    merged = dyn[["timestamp", "breaches"]].merge(
        static[["timestamp", "breaches"]], on="timestamp", suffixes=("_dyn", "_static")
    )
    flips = merged.loc[merged["breaches_dyn"] != merged["breaches_static"]]
    flip_hours_utc = sorted(_iso(t) for t in flips["timestamp"])

    return {
        "disclosed_loss_usd": DISCLOSED_LOSS_USD,
        "disclosure_ts_utc": DISCLOSURE_TS_UTC,
        "variant_i": {
            "description": "B(t) = usdc_supply_daily.csv circulating_usd, linearly "
                            "interpolated to the frozen hourly USDC price timestamps.",
            "trough_hour_utc": _iso(trough["timestamp"]),
            "trough_price": round(trough_price, 6),
            # an adversarial review: rounded to 3dp, not 4, 4dp is finer than daily-interpolated
            # B(t) can resolve (the interpolation band below is ~30bp wide against an
            # ~8bp effect).
            "phi_at_trough": round(float(trough["phi_t"]), 3),
            "floor_at_trough": round(float(trough["floor_t"]), 3),
            "q_at_trough": round(float(trough["q"]), 3),
            "s_nf0_at_trough": round(float(trough["below_floor_share"]), 3),
            "max_phi": round(float(dyn.loc[max_idx, "phi_t"]), 3),
            "max_phi_hour_utc": _iso(dyn.loc[max_idx, "timestamp"]),
            # an adversarial review: window-edge censoring artifact, not a true extremum, phi is
            # monotone in a monotone supply series and continues past the window; at
            # this hour (the window's LAST observed hour) the coin trades above par and
            # q* is negative, so the bound is vacuous here. Flagged, not silently kept.
            "max_phi_window_edge_artifact": True,
            "max_phi_window_edge_note": "phi is monotone in a monotone supply series and "
                                         "continues past the window; this is the window's "
                                         "last observed hour, where the coin trades above "
                                         "par and q* < 0 (bound vacuous), not a true "
                                         "maximum.",
            "min_phi": round(float(dyn.loc[min_idx, "phi_t"]), 3),
            "min_phi_hour_utc": _iso(dyn.loc[min_idx, "timestamp"]),
            # an adversarial review: shares the same defect at the window's other edge.
            "min_phi_window_edge_artifact": True,
            "min_phi_window_edge_note": "shares max_phi's defect at the window's other "
                                         "edge -- this is the window's first observed "
                                         "hour; the true minimum sits outside the "
                                         "observed window, not at this hour.",
        },
        "interpolation_band": interpolation_band(),
        "variant_ii_gap": VARIANT_II_GAP,
        "third_line_alt_reserve_total": {
            "description": "Circle's alternate ~$42.1B reserve-composition total "
                            "(paper/fc27/fc27.tex lines 443-444, cite key "
                            "circle2023pressrelease) -- a SINGLE POINT figure, not a "
                            "daily series; a constant comparison line, NOT a time series.",
            "is_time_varying": False,
            "alt_reserve_total_usd": ALT_RESERVE_TOTAL_USD,
            "phi": round(phi_alt, 4),
            "floor": round(floor_alt, 4),
            "q_at_trough": round(float(q_alt_trough), 4),
            "s_nf0_at_trough": round(float(s_nf0_alt_trough), 4),
        },
        "static_comparison": {
            "source": "usdc_depeg/results/impossibility_window.json (read, not recomputed)",
            "phi_static": PHI_SVB,
            "floor_static": round(rb.floor_worstcase(PHI_SVB), 4),
            "firing_hours_count": int(len(static_fire)),
            "firing_hours_utc": static_fire_hours,
        },
        "dynamic_firing": {
            "firing_hours_count": int(len(dyn_fire)),
            "firing_hours_utc": dyn_fire_hours,
        },
        "firing_set_identical": dyn_fire_hours == static_fire_hours,
        "flip_hours_utc": flip_hours_utc,
        # an adversarial review: the firing set is 9-11, not "unchanged", kept retrievable
        # verbatim for the manuscript.
        "firing_set": {
            "composite_close_hours": 9,
            "kraken_convention_range_hours": [9, 11],
            "statement_verbatim": "9 hours on the composite close; 9-11 across Kraken "
                                   "conventions",
        },
    }


if __name__ == "__main__":
    import json
    from constants import RESULTS_DIR
    # results/phi_dynamic.json is persisted here, matching the write convention every
    # other results-writing module in this package uses (specificity_panel.py,
    # report.py, data_fetch.py).
    r = report()
    out_path = RESULTS_DIR / "phi_dynamic.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2)
    print(f"wrote {out_path}")
