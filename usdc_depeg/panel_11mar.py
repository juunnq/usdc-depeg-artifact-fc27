"""Data behind Figure 1 (following an adversarial review's recommendation to
corroborate the trough independently): all three coins'
composite hourly readings (data/price_hourly.csv) for 2023-03-11 00:00-24:00 UTC,
plus Kraken USDC/USD close/vwap/low for the same 24 hours. Every hour, every series
gets floor(t), a firing flag (price < floor), and q*(t) = rational_bound
.implied_failure_prob. Also folds in the already-computed cross-coin / cross-window
firing-count summaries from results/impossibility_window.json and
results/placebo_excluded_coins.json (READ, not recomputed) so this file is a
self-contained source for the figure.

FLOOR. results/phi_dynamic.json carries only point values, trough-hour phi, and
the window's max/min phi with their single hours, NOT an hourly phi(t) series.
There is nothing to read a dynamic floor(t) FROM. So this module uses the STATIC
floor from rational_bound.floor_worstcase(PHI_SVB) for every hour, and says so
explicitly in the output (`floor_source`). If a future change adds
an hourly phi(t) series to phi_dynamic.json, _floor_source() below will raise rather
than silently keep using the static floor.

COVERAGE-GAP CHECK, 08:57 UTC. The specificity panel's manuscript language (review/
loci/appD.md:84-87) says USDC's "impossibility region is interrupted at 08:57 by a
recovery to $0.9216", that is an ECONOMIC interruption (a non-firing hour breaking
the breaching-hour streak), not a claim about missing data. Checked directly against
the frozen composite CSV: the 08:57:55 UTC row is PRESENT (price 0.921553, above the
0.92 floor). No genuine data gap exists there; nothing was interpolated. See
_coverage_gap_check().

Offline / keyless. Reads only the frozen snapshot and already-computed results files;
writes only results/panel_11mar.json (never under data/).
"""
import json

import pandas as pd

from constants import DATA_DIR, PHI_SVB, RESULTS_DIR
import rational_bound as rb

DAY_START = pd.Timestamp("2023-03-11T00:00:00Z")
DAY_END = pd.Timestamp("2023-03-12T00:00:00Z")
GAP_CHECK_HOUR = pd.Timestamp("2023-03-11T08:57:00Z")

COMPOSITE_FILE = DATA_DIR / "price_hourly.csv"
KRAKEN_FILE = DATA_DIR / "n1" / "kraken_usdcusd" / "hourly.csv"
PHI_DYNAMIC_FILE = RESULTS_DIR / "phi_dynamic.json"
IMPOSSIBILITY_FILE = RESULTS_DIR / "impossibility_window.json"
PLACEBO_EXCLUDED_FILE = RESULTS_DIR / "placebo_excluded_coins.json"
OUT_FILE = RESULTS_DIR / "panel_11mar.json"


class FrozenPathWriteError(RuntimeError):
    """Raised if any write in this module is ever pointed at usdc_depeg/data/. This
    module only ever writes results/panel_11mar.json; the guard exists so a future
    edit cannot silently start writing into the frozen snapshot."""


def _guard_not_frozen(path) -> None:
    data_dir = DATA_DIR.resolve()
    if data_dir in path.resolve().parents or path.resolve() == data_dir:
        raise FrozenPathWriteError(
            f"refusing to write '{path}': under usdc_depeg/data/, which is read-only. "
            f"panel_11mar.py may only write under results/."
        )


def _floor_source() -> tuple:
    """(floor, floor_source_note). Static PHI_SVB floor unless phi_dynamic.json ever
    grows an hourly phi(t) series (it does not today, see module docstring)."""
    note_no_series = (
        "static (rational_bound.floor_worstcase(PHI_SVB)); results/phi_dynamic.json "
        "exists but carries only point values (variant_i.phi_at_trough, max_phi/"
        "min_phi at single hours) -- no hourly phi(t) series to derive a dynamic "
        "floor(t) from, so the static floor is used for every hour in this panel."
    )
    if not PHI_DYNAMIC_FILE.exists():
        return rb.floor_worstcase(PHI_SVB), (
            "static (rational_bound.floor_worstcase(PHI_SVB)); results/phi_dynamic.json "
            "not found."
        )
    phi_dyn = json.loads(PHI_DYNAMIC_FILE.read_text(encoding="utf-8"))
    hourly_keys = {"hourly", "phi_series", "phi_t", "hourly_phi"}
    if hourly_keys & phi_dyn.keys():
        raise NotImplementedError(
            "phi_dynamic.json now carries a key from "
            f"{sorted(hourly_keys)} -- panel_11mar.py's _floor_source() was written "
            "when no hourly phi(t) series existed; wire the dynamic floor(t) in "
            "before trusting this module's floor column."
        )
    return rb.floor_worstcase(PHI_SVB), note_no_series


def _q_star(price: float) -> float:
    """q*(t) = rational_bound.implied_failure_prob, undefined (None) at/above par."""
    return round(rb.implied_failure_prob(price, PHI_SVB), 4) if price < 1.0 else None


def _composite_day(symbol: str, floor: float) -> list:
    """One symbol's composite hourly readings for 2023-03-11 00:00-24:00 UTC."""
    px = pd.read_csv(COMPOSITE_FILE)
    g = px.loc[px["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
    g["dt"] = pd.to_datetime(g["timestamp"], unit="s", utc=True)
    day = g.loc[(g["dt"] >= DAY_START) & (g["dt"] < DAY_END)]
    rows = []
    for _, r in day.iterrows():
        p = float(r["price"])
        rows.append({
            "hour_utc": r["dt"].isoformat(),
            "price": round(p, 6),
            "floor": round(floor, 4),
            "firing": bool(p < floor),
            "q_star": _q_star(p),
        })
    return rows


def _kraken_day(floor: float) -> list:
    """Kraken USDC/USD close/vwap/low for the same 24 hours, each with its own
    firing flag and q* (the three price conventions can disagree hour to hour)."""
    k = pd.read_csv(KRAKEN_FILE)
    k["dt"] = pd.to_datetime(k["hour_utc"], utc=True)
    day = k.loc[(k["dt"] >= DAY_START) & (k["dt"] < DAY_END)].sort_values("dt")
    rows = []
    for _, r in day.iterrows():
        entry = {"hour_utc": r["dt"].isoformat(), "floor": round(floor, 4)}
        for col in ("close", "vwap", "low"):
            p = float(r[col])
            entry[col] = round(p, 6)
            entry[f"{col}_firing"] = bool(p < floor)
            entry[f"{col}_q_star"] = _q_star(p)
        rows.append(entry)
    return rows


def _coverage_gap_check() -> dict:
    """Is there a genuine DATA gap in the composite USDC series around 08:57 UTC on
    11 March? Checked directly against the frozen CSV rather than assumed."""
    px = pd.read_csv(COMPOSITE_FILE)
    g = px.loc[px["symbol"] == "USDC"].sort_values("timestamp").reset_index(drop=True)
    g["dt"] = pd.to_datetime(g["timestamp"], unit="s", utc=True)
    window = g.loc[(g["dt"] >= GAP_CHECK_HOUR) & (g["dt"] < GAP_CHECK_HOUR + pd.Timedelta(minutes=3))]
    if window.empty:
        return {
            "checked_window_utc": ["2023-03-11T08:57:00Z", "2023-03-11T09:00:00Z"],
            "genuine_data_gap": True,
            "note": (
                "No composite USDC row found in [08:57, 09:00) UTC on 2023-03-11 -- a "
                "real coverage gap. Not interpolated; left as a documented hole."
            ),
        }
    row = window.iloc[0]
    return {
        "checked_window_utc": ["2023-03-11T08:57:00Z", "2023-03-11T09:00:00Z"],
        "genuine_data_gap": False,
        "row_utc": row["dt"].isoformat(),
        "price": round(float(row["price"]), 6),
        "note": (
            "No genuine data gap: the composite USDC row at "
            f"{row['dt'].isoformat()} is present (price {round(float(row['price']), 6)}"
            "). The 'interruption' in review/loci/appD.md:84-87 ('impossibility region "
            "is interrupted at 08:57 by a recovery to $0.9216') is ECONOMIC -- this "
            "hour's price clears the 0.92 floor and so breaks the breaching-hour "
            "streak in impossibility_window.json (contiguous: false) -- not a missing "
            "timestamp. Nothing was interpolated."
        ),
    }


def _cross_coin_summary() -> dict:
    """Cross-coin / cross-window firing-count summaries, READ verbatim from the
    already-computed results files, not recomputed here (per task instruction)."""
    imp = json.loads(IMPOSSIBILITY_FILE.read_text(encoding="utf-8"))
    placebo_excl = json.loads(PLACEBO_EXCLUDED_FILE.read_text(encoding="utf-8"))
    return {
        "source_files": ["results/impossibility_window.json", "results/placebo_excluded_coins.json"],
        "cross_coin_firing_counts_full_record": {
            sym: {
                "breaching_hours": imp["coins"][sym]["breaching_hours"],
                "hours_observed": imp["coins"][sym]["hours_observed"],
                "firing_rate": imp["coins"][sym]["firing_rate"],
            }
            for sym in ("USDC", "DAI", "USDT")
        },
        "excluded_coin_floor_check_0.92": {
            "note": "from impossibility_window.json's excluded_coin_floor_check key "
                    "(the 0.92-floor check across the full 2,086-hour calm+crisis "
                    "excluded-coin panel).",
            **imp["excluded_coin_floor_check"],
        },
        "excluded_coin_1pct_break_threshold_check": {
            "note": "from placebo_excluded_coins.json (the 0.99 / 1%-break-threshold "
                    "calm-vs-crisis check per excluded coin -- a DIFFERENT test from "
                    "the 0.92 floor check above).",
            "break_threshold": placebo_excl["break_threshold"],
            "calm_window_utc": placebo_excl["calm_window_utc"],
            "coins": {c: placebo_excl[c] for c in ("USDP", "GUSD", "BUSD")},
            "interpretation": placebo_excl["interpretation"],
        },
    }


def report() -> dict:
    floor, floor_source = _floor_source()
    return {
        "date_utc": "2023-03-11",
        "phi": PHI_SVB,
        "floor": round(floor, 4),
        "floor_source": floor_source,
        "panel": {
            "composite_USDC": _composite_day("USDC", floor),
            "composite_DAI": _composite_day("DAI", floor),
            "composite_USDT": _composite_day("USDT", floor),
            "kraken_usdcusd": _kraken_day(floor),
        },
        "coverage_gap_check_08_57_utc": _coverage_gap_check(),
        "cross_coin_cross_window_summary": _cross_coin_summary(),
    }


def main():
    _guard_not_frozen(OUT_FILE)
    RESULTS_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(report(), indent=2), encoding="utf-8")
    print(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
