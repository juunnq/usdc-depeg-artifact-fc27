"""Cross-feed trough corroboration for Proposition 1.

The headline result (rational_bound.py / impossibility_window.py) evaluates the bound
against ONE feed: the DeFiLlama composite. This module asks whether the same
impossibility-region firing pattern is corroborated by INDEPENDENT off-chain price feeds
frozen elsewhere in this package: the composite itself (data/price_hourly.csv, symbol ==
"USDC", DeFiLlama), Kraken's native USDC/USD pair (data/n1/kraken_usdcusd/hourly.csv), and
a Binance-derived series (data/binance_usdcusdt_1h.csv, USDCUSDT deflated to USD terms via
two independent USDT/USD legs: Coinbase at data/usdt_usd_hourly.csv, and Kraken at
data/n1/kraken_usdtusd/hourly.csv).

DEFLATION. Binance's "USDCUSDT" symbol quotes USDT per 1 USDC (USDC is the base asset,
USDT the quote asset, confirmed from data_fetch.fetch_binance_klines's own klines call,
symbol=USDCUSDT). So Binance's low/close are already "USDC price expressed in USDT", and
    USDC_in_USD(t) = USDCUSDT_price(t) * USDT_in_USD(t).
Deflation pairs matching statistics on both legs (low x low, close x close) for internal
consistency; this is a design choice, not a given convention, see module docstring.
Because USDT traded at a premium (>$1) during the crisis window, deflation should push
the series UP (less severe depeg) relative to the raw Binance-in-USDT print; verified
below in `verify_deflation_direction`.

FLOOR. floor = rb.floor_worstcase(PHI_SVB), derived (never hardcoded) from
rational_bound.py, matching that module's and impossibility_window.py's convention.

KNOWN GAP. The frozen Binance snapshot does not cover 2023-03-11 06:00-13:00 UTC (its
first row is 14:00 UTC that day), it misses the composite/Kraken trough entirely. This
is reported explicitly wherever it matters, never papered over.

ON-CHAIN HOOK. `onchain_placeholder()` is a structural stub for a future on-chain
corroboration pass to fill; it carries no numbers.

Offline / keyless. Reads only the frozen snapshot.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from constants import DATA_DIR, PHI_SVB, RESULTS_DIR
import rational_bound as rb

KRAKEN_USDC_FILE = DATA_DIR / "n1" / "kraken_usdcusd" / "hourly.csv"
KRAKEN_USDT_FILE = DATA_DIR / "n1" / "kraken_usdtusd" / "hourly.csv"
BINANCE_FILE = DATA_DIR / "binance_usdcusdt_1h.csv"
COINBASE_USDT_FILE = DATA_DIR / "usdt_usd_hourly.csv"
COMPOSITE_FILE = DATA_DIR / "price_hourly.csv"
PHI_DYNAMIC_FILE = RESULTS_DIR / "phi_dynamic.json"

TROUGH_HOURS_UTC = ["2023-03-11T07:00:00Z", "2023-03-11T08:00:00Z"]


# --- loaders -----------------------------------------------------------------------

def _load_composite_usdc() -> pd.DataFrame:
    """Composite USDC series, ascending. NOT hour-aligned, DeFiLlama pulls land at
    irregular seconds within each hour, so `hour` (floor-to-hour) is added for hourly
    joins/bucketing without discarding the raw timestamp."""
    px = pd.read_csv(COMPOSITE_FILE)
    g = px.loc[px["symbol"] == "USDC"].sort_values("timestamp").reset_index(drop=True)
    g["dt"] = pd.to_datetime(g["timestamp"], unit="s", utc=True)
    g["hour"] = g["dt"].dt.floor("h")
    return g


def _load_kraken_usdc() -> pd.DataFrame:
    """Kraken USDC/USD hourly, native USD pair, hour-aligned."""
    k = pd.read_csv(KRAKEN_USDC_FILE).sort_values("hour_utc").reset_index(drop=True)
    k["dt"] = pd.to_datetime(k["hour_utc"], utc=True)
    return k


def _load_binance_deflated(leg: str) -> pd.DataFrame:
    """Binance USDCUSDT deflated into USD terms by one USDT/USD leg.

    leg: 'coinbase' (data/usdt_usd_hourly.csv) or 'kraken' (data/n1/kraken_usdtusd).
    Returns columns dt, usdc_usd_low, usdc_usd_close (deflated), plus the raw
    usdcusdt_low/usdcusdt_close for reference.
    """
    b = pd.read_csv(BINANCE_FILE)
    b["dt"] = pd.to_datetime(b["open_time"], unit="ms", utc=True)

    if leg == "coinbase":
        u = pd.read_csv(COINBASE_USDT_FILE)
        u["dt"] = pd.to_datetime(u["open_time"], unit="s", utc=True)
    elif leg == "kraken":
        u = pd.read_csv(KRAKEN_USDT_FILE)
        u["dt"] = pd.to_datetime(u["hour_utc"], utc=True)
    else:
        raise ValueError(f"unknown leg {leg!r}")

    m = b.merge(u[["dt", "low", "close"]], on="dt", how="inner", suffixes=("", "_usdt"))
    m["usdc_usd_low"] = m["low"] * m["low_usdt"]
    m["usdc_usd_close"] = m["close"] * m["close_usdt"]
    return m.rename(columns={"low": "usdcusdt_low", "close": "usdcusdt_close"})[
        ["dt", "usdcusdt_low", "usdcusdt_close", "low_usdt", "close_usdt",
         "usdc_usd_low", "usdc_usd_close"]
    ]


def verify_deflation_direction(leg: str) -> dict:
    """Sanity check: deflated price should be >= raw Binance-in-USDT print at the raw
    minimum (USDT traded at a premium during the crisis), confirming the multiplication
    was not inverted."""
    m = _load_binance_deflated(leg)
    row = m.loc[m["usdcusdt_low"].idxmin()]
    return {
        "leg": leg,
        "raw_min_low_usdt_terms": round(float(row["usdcusdt_low"]), 6),
        "deflated_low_at_same_hour": round(float(row["usdc_usd_low"]), 6),
        "usdt_in_usd_at_that_hour": round(float(row["low_usdt"]), 6),
        "moved_up": bool(row["usdc_usd_low"] >= row["usdcusdt_low"]),
    }


# --- per-series stats ----------------------------------------------------------------

def _series_stats(df: pd.DataFrame, col: str, floor: float) -> dict:
    """Firing count, first/last firing hour, min print + hour, q* at the min, for one
    (feed, price-convention) series. df must have a 'dt' column and be sorted ascending."""
    below = df[col] < floor
    n = int(below.sum())
    minrow = df.loc[df[col].idxmin()]
    p_min = float(minrow[col])
    out = {
        "n_hours_observed": int(len(df)),
        "firing_count": n,
        "min_print": round(p_min, 6),
        "min_print_utc": minrow["dt"].isoformat(),
        "q_star_at_min": round(rb.implied_failure_prob(p_min, PHI_SVB), 4),
    }
    if n > 0:
        fired = df.loc[below, "dt"]
        out["first_firing_utc"] = fired.iloc[0].isoformat()
        out["last_firing_utc"] = fired.iloc[-1].isoformat()
    else:
        out["first_firing_utc"] = None
        out["last_firing_utc"] = None
    return out


def all_series() -> dict:
    """Every (feed, price-convention) series this module covers, keyed unambiguously."""
    comp = _load_composite_usdc()
    kr = _load_kraken_usdc()
    b_cb = _load_binance_deflated("coinbase")
    b_kr = _load_binance_deflated("kraken")
    return {
        "composite": (comp, "price"),
        "kraken_usdc_low": (kr, "low"),
        "kraken_usdc_close": (kr, "close"),
        "kraken_usdc_vwap": (kr, "vwap"),
        "binance_deflated_coinbase_low": (b_cb, "usdc_usd_low"),
        "binance_deflated_coinbase_close": (b_cb, "usdc_usd_close"),
        "binance_deflated_kraken_low": (b_kr, "usdc_usd_low"),
        "binance_deflated_kraken_close": (b_kr, "usdc_usd_close"),
    }


def firing_report(phi: float = PHI_SVB) -> dict:
    """Firing count / first-last / min / q* for every labeled series."""
    floor = rb.floor_worstcase(phi)
    out = {"floor_worstcase": round(floor, 4), "phi": phi, "series": {}}
    for label, (df, col) in all_series().items():
        out["series"][label] = _series_stats(df, col, floor)
    out["binance_coverage_note"] = (
        "The frozen Binance snapshot's first row is 2023-03-11T14:00:00Z -- it does NOT "
        "cover the 06:00-13:00 UTC window containing the composite/Kraken trough. Binance "
        "firing counts above are computed over its own available window only (131 hours, "
        "14:00 2023-03-11 through 2023-03-17), not the full 9-day panel."
    )
    # an adversarial review: Kraken's low-print firing count (12) is inflated by the hours where
    # low and vwap/close diverge most (739 bps at 03:00, 211 bps at 07:00), a wick, not
    # a clearing level. It is kept below as a field, never removed, but close (9) and
    # vwap (10) are the headline Kraken statistics; nothing here should be read as
    # nominating the low count as primary.
    out["kraken_low_convention_note"] = (
        "kraken_usdc_low's firing_count is NOT the headline Kraken statistic -- it is "
        "inflated by wick hours where the intra-hour low diverges sharply from the "
        "close/vwap (739 bps at 03:00, 211 bps at 07:00). Quote kraken_usdc_close (9) "
        "and kraken_usdc_vwap (10)."
    )
    return out


# --- trough-hour table (07:00, 08:00 UTC, 11 March 2023) -----------------------------

def _composite_hour_print(comp: pd.DataFrame, hour: pd.Timestamp) -> float | None:
    """Composite's print(s) landing in one UTC hour bucket. Multiple DeFiLlama pulls can
    land in the same hour (35 of 212 rows do, across the full window); the MINIMUM print
    in the bucket is used so a below-floor moment within the hour is never hidden by an
    average or a later, recovered print."""
    rows = comp.loc[comp["hour"] == hour, "price"]
    return float(rows.min()) if not rows.empty else None


def trough_hour_table(phi: float = PHI_SVB) -> list:
    """Every feed's print in the 07:00 and 08:00 UTC hours, 11 March 2023, with a
    below-$floor boolean per feed. Binance rows are 'not available' (pre-coverage gap)."""
    floor = rb.floor_worstcase(phi)
    comp = _load_composite_usdc()
    kr = _load_kraken_usdc()
    b_cb = _load_binance_deflated("coinbase")
    b_kr = _load_binance_deflated("kraken")

    rows = []
    for hstr in TROUGH_HOURS_UTC:
        hour = pd.Timestamp(hstr)
        entry = {"hour_utc": hstr, "feeds": {}}

        cp = _composite_hour_print(comp, hour)
        entry["feeds"]["composite"] = (
            {"print": round(cp, 6), "below_floor": bool(cp < floor)} if cp is not None
            else {"print": None, "below_floor": None, "note": "no composite print this hour"}
        )

        krow = kr.loc[kr["dt"] == hour]
        if krow.empty:
            entry["feeds"]["kraken_usdc"] = {"low": None, "close": None, "vwap": None,
                                              "note": "no Kraken USDC row this hour"}
        else:
            r = krow.iloc[0]
            entry["feeds"]["kraken_usdc"] = {
                "low": round(float(r["low"]), 6), "low_below_floor": bool(r["low"] < floor),
                "close": round(float(r["close"]), 6), "close_below_floor": bool(r["close"] < floor),
                "vwap": round(float(r["vwap"]), 6), "vwap_below_floor": bool(r["vwap"] < floor),
            }

        for leg_label, bdf in (("binance_deflated_coinbase", b_cb), ("binance_deflated_kraken", b_kr)):
            brow = bdf.loc[bdf["dt"] == hour]
            if brow.empty:
                entry["feeds"][leg_label] = {
                    "low": None, "close": None,
                    "note": "not available -- Binance snapshot starts 2023-03-11T14:00:00Z",
                }
            else:
                r = brow.iloc[0]
                entry["feeds"][leg_label] = {
                    "low": round(float(r["usdc_usd_low"]), 6),
                    "low_below_floor": bool(r["usdc_usd_low"] < floor),
                    "close": round(float(r["usdc_usd_close"]), 6),
                    "close_below_floor": bool(r["usdc_usd_close"] < floor),
                }
        rows.append(entry)
    return rows


# --- cross-feed agreement --------------------------------------------------------------

def cross_feed_agreement(phi: float = PHI_SVB) -> dict:
    """For each hour Kraken observes, the count of independent feeds below the floor.

    Independent feeds for this count = {composite, kraken_usdc, binance_deflated} (3),
    each represented by its CLOSE (composite has only one price field; close is the
    canonical statistic used for Kraken and for Binance's canonical, Coinbase-deflated
    -- leg, so the count compares like with like). Binance's Kraken-deflated leg is
    reported separately as a robustness check, NOT counted as a fourth independent vote
    (both legs share the same underlying Binance print).
    """
    floor = rb.floor_worstcase(phi)
    comp = _load_composite_usdc()
    kr = _load_kraken_usdc()
    b_cb = _load_binance_deflated("coinbase")
    b_kr = _load_binance_deflated("kraken")

    comp_hourly = comp.groupby("hour")["price"].min()

    rows = []
    for hour in kr["dt"]:
        cp = float(comp_hourly.get(hour)) if hour in comp_hourly.index else None
        kc = float(kr.loc[kr["dt"] == hour, "close"].iloc[0])
        brow_cb = b_cb.loc[b_cb["dt"] == hour, "usdc_usd_close"]
        bc_cb = float(brow_cb.iloc[0]) if not brow_cb.empty else None
        brow_kr = b_kr.loc[b_kr["dt"] == hour, "usdc_usd_close"]
        bc_kr = float(brow_kr.iloc[0]) if not brow_kr.empty else None

        votes = []
        available = 0
        if cp is not None:
            available += 1
            if cp < floor:
                votes.append("composite")
        available += 1
        if kc < floor:
            votes.append("kraken_usdc")
        if bc_cb is not None:
            available += 1
            if bc_cb < floor:
                votes.append("binance_deflated_coinbase")

        rows.append({
            "hour_utc": hour.isoformat(),
            "feeds_available": available,
            "feeds_below_floor": len(votes),
            "below_floor_feeds": votes,
            "binance_deflated_kraken_close": (round(bc_kr, 6) if bc_kr is not None else None),
            "binance_deflated_kraken_below_floor_robustness": (
                bool(bc_kr < floor) if bc_kr is not None else None
            ),
        })

    by_hour = {r["hour_utc"]: r for r in rows}
    at_07 = by_hour.get("2023-03-11T07:00:00+00:00")
    at_08 = by_hour.get("2023-03-11T08:00:00+00:00")
    return {
        "floor_worstcase": round(floor, 4),
        "canonical_statistic": "close (composite has one price field; close used for "
                                "Kraken USDC and for Binance's Coinbase-deflated leg)",
        # an adversarial review: "independent" is qualified, not asserted unconditionally, the
        # composite's own constituents are not enumerated in the frozen record. The two
        # feeds that actually corroborate the trough are the composite and Kraken's
        # fiat-settled USDC/USD close; Binance's deflated legs don't cover the
        # 06:00-13:00 UTC trough window at all and are reported as a robustness check,
        # not a third vote toward this count.
        "feed_count_independent": 2,
        "feed_count_independent_note": "composite + kraken_usdc (fiat-settled); Binance's "
                                        "deflated legs are excluded from this count -- the "
                                        "frozen snapshot doesn't cover the trough window.",
        "hours": rows,
        "at_07_00_utc": at_07,
        "at_08_00_utc": at_08,
    }


# --- independence (AR(1) / effective N) ------------------------------------------------

def _ar1_effective_n(x: np.ndarray) -> dict:
    """Kish-style n_eff = n * (1 - r1) / (1 + r1) on the price level, mirrors
    impossibility_window.effective_n() exactly (same estimator, same formula)."""
    r1 = float(np.corrcoef(x[:-1], x[1:])[0, 1])
    n_eff = float(len(x) * (1.0 - r1) / (1.0 + r1)) if r1 < 1.0 else float("nan")
    return {"n_hours": int(len(x)), "lag1_autocorr": round(r1, 4), "effective_n": round(n_eff, 1)}


def independence_caveat() -> dict:
    """AR(1) / effective-N on the composite (verifying impossibility_window's ~0.96
    claim), also on the Kraken USDC series (close)."""
    comp = _load_composite_usdc()
    kr = _load_kraken_usdc()
    return {
        "note": (
            "Hourly readings are near-unit-root within the crisis episode: consecutive "
            "hours are not independent observations. Firing counts and cross-feed "
            "agreement counts above must be read alongside these effective sample sizes, "
            "not as raw evidence density."
        ),
        "composite": _ar1_effective_n(comp["price"].to_numpy()),
        "kraken_usdc_close": _ar1_effective_n(kr["close"].to_numpy()),
        "kraken_usdc_close_method_note": (
            "Same Kish n_eff = n*(1-r1)/(1+r1) estimator as impossibility_window."
            "effective_n(), applied to Kraken's close series."
        ),
    }


# --- on-chain hook (structural placeholder; no numbers) ---------------------------------

def onchain_placeholder() -> dict:
    """Structural stub for a future on-chain corroboration pass. Carries no numbers,
    only the shape a later fill-in is expected to match."""
    return {
        "available": False,
        "note": "reserved for a future on-chain corroboration pass; not populated here",
        "expected_keys": ["series", "firing_count", "min_print", "min_print_utc", "q_star_at_min"],
    }


def dynamic_floor_check() -> dict:
    """If phi_dynamic.py has produced results/phi_dynamic.json, report firing counts
    against the dynamic floor(t) too; otherwise say so rather than fabricating or
    waiting."""
    if not PHI_DYNAMIC_FILE.exists():
        return {"available": False, "note": "phi_dynamic.json not found, not available this run"}
    import json
    phi_dyn = json.loads(PHI_DYNAMIC_FILE.read_text(encoding="utf-8"))
    return {"available": True, "source": "results/phi_dynamic.json", "raw": phi_dyn}


# --- top-level report --------------------------------------------------------------------

KRAKEN_USDC_TRADES = DATA_DIR / "n1" / "kraken_usdcusd" / "trades_raw.jsonl"


def below_floor_depth(phi: float = PHI_SVB) -> dict:
    """Executed TRADED DEPTH below the worst-case floor, from Kraken's raw tick file.

    Added at the request of the N7 adversarial pass (ruling C1), which established that
    the minimum-print statistic the analysis previously led with is a single 3,373.52-USDC
    trade, roughly $2,948 of notional, 0.01% of its hour. A minimum is one trade by
    construction and says nothing about how much value actually changed hands below the
    floor. This function reports that instead: it is the statistic the manuscript should
    quote, and unlike an hourly firing COUNT it is additive rather than a pseudo-sample,
    so the AR(1) effective-n objection (n_eff ~ 6.7 on this series) does not apply to it.
    """
    floor = rb.floor_worstcase(phi)
    n = 0
    volume_usdc = 0.0
    notional_usd = 0.0
    min_price = None
    min_trade_volume = None
    per_hour: dict[str, dict] = {}
    hour_totals: dict[str, int] = {}

    with open(KRAKEN_USDC_TRADES, encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            price, vol = t["price"], t["volume"]
            hour = datetime.fromtimestamp(t["time"], tz=timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
            hour_totals[hour] = hour_totals.get(hour, 0) + 1
            if price >= floor:
                continue
            n += 1
            volume_usdc += vol
            notional_usd += price * vol
            h = per_hour.setdefault(hour, {"n_trades": 0, "volume_usdc": 0.0})
            h["n_trades"] += 1
            h["volume_usdc"] += vol
            if min_price is None or price < min_price:
                min_price, min_trade_volume = price, vol

    for hour, rec in per_hour.items():
        rec["volume_usdc"] = round(rec["volume_usdc"], 2)
        rec["share_of_hour_trades"] = round(rec["n_trades"] / hour_totals[hour], 4)

    hours = sorted(per_hour)
    return {
        "floor": round(floor, 4),
        "source_file": "data/n1/kraken_usdcusd/trades_raw.jsonl",
        "n_trades_below_floor": n,
        "volume_usdc_below_floor": round(volume_usdc, 2),
        "notional_usd_below_floor": round(notional_usd, 2),
        "first_hour_utc": hours[0] if hours else None,
        "last_hour_utc": hours[-1] if hours else None,
        "min_print": min_price,
        "min_print_trade_volume_usdc": round(min_trade_volume, 8) if min_trade_volume else None,
        # an adversarial review: the $0.8740 headline print is this single trade, wired directly
        # off min_print_trade_volume_usdc above, not recomputed.
        "kraken_low_trade_size_usdc": round(min_trade_volume, 2) if min_trade_volume else None,
        "kraken_low_is_single_trade": min_trade_volume is not None,
        "per_hour": {h: per_hour[h] for h in hours},
        "note": (
            "The minimum print is a single trade and must not be quoted as the headline; "
            "the executed sub-floor depth is the load-bearing number. In the 07:00 UTC "
            "hour every trade cleared below the floor (share_of_hour_trades = 1.0)."
        ),
    }


def report(phi: float = PHI_SVB) -> dict:
    return {
        "phi": phi,
        "floor_worstcase": round(rb.floor_worstcase(phi), 4),
        "deflation_direction_check": {
            "coinbase_leg": verify_deflation_direction("coinbase"),
            "kraken_leg": verify_deflation_direction("kraken"),
        },
        "firing_report": firing_report(phi),
        "below_floor_depth": below_floor_depth(phi),
        "trough_hour_table": trough_hour_table(phi),
        "cross_feed_agreement": cross_feed_agreement(phi),
        "independence": independence_caveat(),
        "dynamic_floor_check": dynamic_floor_check(),
        "onchain": onchain_placeholder(),
    }


if __name__ == "__main__":
    import json
    # results/trough_corroboration.json is persisted here, matching the write
    # convention every other results-writing module in this package uses
    # (specificity_panel.py, report.py, data_fetch.py).
    r = report()
    out_path = RESULTS_DIR / "trough_corroboration.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2, default=str)
    print(f"wrote {out_path}")
