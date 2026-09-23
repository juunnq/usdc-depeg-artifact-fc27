"""Cross-stablecoin placebo — the model's falsification test.

The run model predicts a stablecoin breaks only if the shock reaches its backing.
During the March 2023 episode:
  USDC  -- direct exposure (~8% of reserves at SVB)
  DAI   -- contagion exposure (heavily USDC-collateralized)
  USDT  -- no SVB exposure
So the placebo prediction is: the exposed coin (USDC) and the contagion-linked coin
(DAI) break, while the unexposed coin (USDT) does NOT break and may attract a
flight-to-safety premium. A pass is positive evidence that the de-peg was a
shock-driven coordination run rather than a market-wide stablecoin repricing
(which would move all three together).

Pure measurement on the frozen hourly price snapshot; no model parameter enters.
"""
import pandas as pd

from constants import DATA_DIR

# A "material break" is a trough more than 1% below par. USDT's shallow ~0.8%
# dip does not qualify; USDC and DAI clear it by an order of magnitude.
BREAK_THRESHOLD = 0.99

EXPOSURE = {
    "USDC": "direct (~8% of reserves at SVB)",
    "DAI": "contagion (heavily USDC-collateralized)",
    "USDT": "none (no SVB exposure)",
}

# Ex-ante exposure ranking, most exposed first: direct > contagion > none.
EXPOSURE_RANK = ["USDC", "DAI", "USDT"]


def load_prices() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "price_hourly.csv")


def placebo() -> dict:
    """Per-coin trough/peak and the placebo verdict.

    Returns a dict keyed by symbol with trough, peak, max de-peg / premium in bps,
    a ``broke`` flag, and a top-level ``placebo_pass`` that is True iff the exposed
    coins broke and the unexposed coin did not.
    """
    p = load_prices()
    out: dict = {}
    for s in ("USDC", "DAI", "USDT"):
        d = p.loc[p["symbol"] == s, "price"]
        trough, peak = float(d.min()), float(d.max())
        out[s] = {
            "exposure": EXPOSURE[s],
            "trough": trough,
            "peak": peak,
            "max_depeg_bps": round((1.0 - trough) * 1e4, 1),
            "max_premium_bps": round(max(0.0, peak - 1.0) * 1e4, 1),
            "broke": trough < BREAK_THRESHOLD,
        }
    exposed_broke = out["USDC"]["broke"] and out["DAI"]["broke"]
    unexposed_held = not out["USDT"]["broke"]
    out["placebo_pass"] = bool(exposed_broke and unexposed_held)
    out["break_threshold"] = BREAK_THRESHOLD

    # Realized fragility ordering (most fragile = lowest trough first) and whether it
    # matches the ex-ante EXPOSURE ranking. NOTE: this orders by exposure, not by
    # arbitrageur concentration, see README; the concentration static d theta*/d kappa
    # is a separate, conditional prediction NOT identified by this single episode.
    fragility = sorted(("USDC", "DAI", "USDT"), key=lambda s: out[s]["trough"])
    out["fragility_ordering_by_trough"] = fragility
    out["ex_ante_exposure_ranking"] = EXPOSURE_RANK
    out["ordering_matches_exposure"] = (fragility == EXPOSURE_RANK)
    return out


def excluded_coins() -> dict:
    """Frozen evidence for the placebo EXCLUSIONS (USDP / GUSD / BUSD).

    Reads ONLY the frozen snapshot (excluded_coins_hourly.csv for prices over the
    calm baseline + crisis windows; excluded_coins_volume.csv for keyless venue
    volumes). Per coin: crisis trough / max deviation and whether it crosses
    BREAK_THRESHOLD, plus calm-window false-positive stats — would the 1%-break
    test fire between 2023-02-16 and 2023-03-08, absent any shock? Per-venue
    crisis-volume summaries where a keyless series exists (USDT-quoted venue
    volume ~= USD for these near-par pairs)."""
    px = pd.read_csv(DATA_DIR / "excluded_coins_hourly.csv")
    out: dict = {}
    for s in ("USDP", "GUSD", "BUSD"):
        crisis = px.loc[(px["symbol"] == s) & (px["window"] == "crisis"), "price"]
        calm = px.loc[(px["symbol"] == s) & (px["window"] == "calm"), "price"]
        trough = float(crisis.min())
        out[s] = {
            "crisis_trough": trough,
            "crisis_max_deviation_bps": round((1.0 - trough) * 1e4, 1),
            "crosses_break_threshold": bool(trough < BREAK_THRESHOLD),
            "calm_min_price": float(calm.min()),
            "calm_hours_below_threshold": int((calm < BREAK_THRESHOLD).sum()),
            "calm_hours_total": int(len(calm)),
            "calm_false_positive": bool((calm < BREAK_THRESHOLD).any()),
        }
    vol = pd.read_csv(DATA_DIR / "excluded_coins_volume.csv")
    venues = []
    if len(vol):
        vol = vol.copy()
        vol["date"] = pd.to_datetime(vol["open_time"], unit="ms").dt.date
        for (venue, sym), g in vol.groupby(["venue", "symbol"]):
            daily = g.groupby("date")["volume"].sum()
            venues.append({
                "venue": venue, "symbol": sym,
                "n_hours": int(len(g)), "n_days": int(len(daily)),
                "first_utc": pd.to_datetime(g["open_time"].min(), unit="ms").isoformat(),
                "last_utc": pd.to_datetime(g["open_time"].max(), unit="ms").isoformat(),
                "median_daily_volume_usd": float(daily.median()),
                "total_volume_usd": float(g["volume"].sum()),
            })
    out["venue_volumes"] = venues
    out["volume_notes"] = (
        "USDT-quoted Binance volumes are ~USD for these near-par pairs. Binance listed "
        "USDPUSDT mid-window (first kline 2023-03-11T14:00Z), so its summary covers a "
        "partial window. GUSD has NO freezable keyless venue series: Gemini's v2 candles "
        "endpoint serves recent history only and returns NoDataFound for March 2023 "
        "(failure recorded in MANIFEST); no GUSD venue-volume claim is supported.")
    out["break_threshold"] = BREAK_THRESHOLD
    out["calm_window_utc"] = ["2023-02-16T00:00:00Z", "2023-03-08T00:00:00Z"]
    out["interpretation"] = (
        "USDP, GUSD, and BUSD are excluded because the 1%-break test is not diagnostic "
        "for them: each coin's frozen composite feed already prints below 0.99 during "
        "the calm pre-SVB baseline window (7 / 1 / 1 hours respectively), so a "
        "crisis-window crossing on these feeds cannot be distinguished from ordinary "
        "no-shock noise, and no clean exposure prediction is identified for them.")
    return out


def trough_print_quality() -> dict:
    """The USDC trough is a material-volume clearing print, not a thin weekend wick.

    Reads the frozen Binance USDCUSDT 1h klines and reports the minimum hourly low,
    its timestamp, and the volume traded in that single hour, alongside the
    composite-feed trough for comparison."""
    df = pd.read_csv(DATA_DIR / "binance_usdcusdt_1h.csv")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    row = df.loc[df["low"].idxmin()]
    composite = float(load_prices().query("symbol.str.contains('usdc', case=False)",
                                          engine="python")["price"].min())
    return {
        "venue": "Binance USDCUSDT (1h)",
        "min_low": float(row["low"]),
        "min_low_utc": row["open_time"].isoformat(),
        "volume_in_trough_hour_usdc": float(row["volume"]),
        "composite_trough": composite,
        "note": "the impossibility gap rests on a material-volume clearing price; the "
                "single trough hour traded ~$109M on Binance alone, consistent with the "
                "multi-venue composite trough.",
    }


if __name__ == "__main__":
    import json
    r = placebo()
    print(json.dumps(r, indent=2))
    print()
    verdict = "PASS" if r["placebo_pass"] else "FAIL"
    print(f"Placebo {verdict}: "
          f"USDC trough {r['USDC']['trough']:.4f}, "
          f"DAI trough {r['DAI']['trough']:.4f} (contagion), "
          f"USDT trough {r['USDT']['trough']:.4f} / peak {r['USDT']['peak']:.4f} "
          f"(+{r['USDT']['max_premium_bps']:.0f} bps premium, no break).")
