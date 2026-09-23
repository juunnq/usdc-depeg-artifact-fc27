"""phi x trough-convention sensitivity grid for Proposition 1.

Applies rational_bound.py's floor_worstcase / implied_failure_prob / below_floor_share
(reused verbatim, not reimplemented) to every combination of:
  - 3 phi conventions already established in results/phi_dynamic.json and
    results/specificity_panel.json: 0.08 (PHI_SVB), 0.0784 (Circle's alternate
    ~$42.1B reserve-composition total), 0.0825 ($3.3B / $40.0B headline reserve total).
  - 5 trough-price readings, each read fresh from a frozen data/ file (no literal
    trusted without independent computation):
      composite_min_headline    -- data/price_hourly.csv, USDC, global min
      composite_second_lowest   -- data/price_hourly.csv, USDC, second-lowest print
                                    (paper/fc27/fc27.tex:402's "$0.8949")
      kraken_close_min          -- data/n1/kraken_usdcusd/hourly.csv, close column min
      kraken_vwap_min           -- data/n1/kraken_usdcusd/hourly.csv, vwap column min
      binance_usdcusdt_low_min  -- data/binance_usdcusdt_1h.csv, low column min

Per the ruling on this point: the non-fundamental share s_nf(0) = below_floor_share
must be reported as a CROSS-FEED RANGE at phi=0.08, not a single point. This module
computes that range across all 5 trough readings in the grid.

READ-ONLY on data/: every loader below only opens frozen CSVs; nothing under data/ is
ever written. _assert_safe_write_path() is a defense-in-depth guard, enforced in code
(not just observed), against this module's own output path ever colliding with a
MANIFEST.json-tracked frozen file or landing under DATA_DIR at all.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from constants import DATA_DIR, PHI_SVB, RESULTS_DIR, WINDOW_END_S, WINDOW_START_S
from rational_bound import below_floor_share, floor_worstcase, implied_failure_prob

N1_DIR = DATA_DIR / "n1"
OUT_PATH = RESULTS_DIR / "phi_denominator_sensitivity.json"

# The four genuinely distinct price FEEDS, as opposed to composite_second_lowest
# (a second print within the composite feed).
FEED_TROUGH_LABELS = [
    "composite_min_headline",
    "kraken_close_min",
    "kraken_vwap_min",
    "binance_usdcusdt_low_min",
]


class FrozenFileError(RuntimeError):
    """Raised if this module is ever asked to write under DATA_DIR or over a file
    MANIFEST.json already lists as frozen, see data_fetch.py's _refuse_if_manifested
    for the same pattern applied to the actual fetch scripts."""


def _assert_safe_write_path(path) -> None:
    path = path.resolve()
    data_dir = DATA_DIR.resolve()
    if data_dir in path.parents or path == data_dir:
        raise FrozenFileError(
            f"refusing to write '{path}': it is under the frozen data/ directory. "
            f"This module writes only to results/."
        )
    manifest_path = DATA_DIR / "MANIFEST.json"
    if manifest_path.exists():
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        manifested_names = set(manifest.get("files", {}).keys())
        manifested_names |= {s["file"] for s in manifest.get("sources", []) if "file" in s}
        if path.name in manifested_names:
            raise FrozenFileError(
                f"refusing to write '{path.name}': already frozen and listed in "
                f"MANIFEST.json (files{{}} or sources[])."
            )


# --------------------------------------------------------------------------------------
# Phi conventions, literal values matching results/phi_dynamic.json /
# results/specificity_panel.json's established labels (not re-derived; specificity_panel
# consumes phi_dynamic.json's already-rounded 0.0784, so this grid does the same).
# --------------------------------------------------------------------------------------
def phi_conventions() -> list:
    return [
        {
            "phi_label": "static_phi_svb_0.08",
            "phi": PHI_SVB,
            "source": "constants.PHI_SVB -- Circle-disclosed $3.3B / ~$40B reserves (~8%)",
        },
        {
            "phi_label": "static_phi_altreserve_42.1B_0.0784",
            "phi": 0.0784,
            "source": "results/phi_dynamic.json third_line_alt_reserve_total.phi -- Circle's "
                      "alternate ~$42.1B reserve-composition total (fc27.tex:443-444)",
        },
        {
            "phi_label": "static_phi_3.3_over_40.0_0.0825",
            "phi": 3.3e9 / 40.0e9,
            "source": "$3.3B disclosed loss / $40.0B headline reserve total",
        },
    ]


# --------------------------------------------------------------------------------------
# Trough readings, each computed fresh from its frozen source file.
# --------------------------------------------------------------------------------------
def _iso(ts_s: int) -> str:
    return datetime.fromtimestamp(int(ts_s), tz=timezone.utc).isoformat()


def trough_readings() -> list:
    readings = []

    # composite (DeFiLlama), USDC, global min and second-lowest print.
    composite = pd.read_csv(DATA_DIR / "price_hourly.csv")
    composite = composite[composite["symbol"] == "USDC"].sort_values("price").reset_index(drop=True)
    row_min, row_2nd = composite.iloc[0], composite.iloc[1]
    readings.append({
        "trough_label": "composite_min_headline",
        "trough_price": float(row_min["price"]),
        "trough_hour_utc": _iso(row_min["timestamp"]),
        "source_file": "data/price_hourly.csv",
        "source_column": "price (symbol=USDC, global min)",
    })
    readings.append({
        "trough_label": "composite_second_lowest",
        "trough_price": float(row_2nd["price"]),
        "trough_hour_utc": _iso(row_2nd["timestamp"]),
        "source_file": "data/price_hourly.csv",
        "source_column": "price (symbol=USDC, second-lowest print; fc27.tex:402's \"$0.8949\")",
    })

    # Kraken USDC/USD hourly, close and vwap column minima, crisis window only.
    kraken = pd.read_csv(N1_DIR / "kraken_usdcusd" / "hourly.csv")
    kraken["ts"] = pd.to_datetime(kraken["hour_utc"]).astype("int64") // 10**9
    kraken_win = kraken[(kraken["ts"] >= WINDOW_START_S) & (kraken["ts"] < WINDOW_END_S)]
    close_row = kraken_win.loc[kraken_win["close"].idxmin()]
    vwap_row = kraken_win.loc[kraken_win["vwap"].idxmin()]
    readings.append({
        "trough_label": "kraken_close_min",
        "trough_price": float(close_row["close"]),
        "trough_hour_utc": close_row["hour_utc"],
        "source_file": "data/n1/kraken_usdcusd/hourly.csv",
        "source_column": "close (crisis window min)",
    })
    readings.append({
        "trough_label": "kraken_vwap_min",
        "trough_price": float(vwap_row["vwap"]),
        "trough_hour_utc": vwap_row["hour_utc"],
        "source_file": "data/n1/kraken_usdcusd/hourly.csv",
        "source_column": "vwap (crisis window min)",
    })

    # Binance USDCUSDT 1h klines, low column min (whole frozen series is the window).
    binance = pd.read_csv(DATA_DIR / "binance_usdcusdt_1h.csv")
    low_row = binance.loc[binance["low"].idxmin()]
    readings.append({
        "trough_label": "binance_usdcusdt_low_min",
        "trough_price": float(low_row["low"]),
        "trough_hour_utc": _iso(int(low_row["open_time"]) // 1000),
        "source_file": "data/binance_usdcusdt_1h.csv",
        "source_column": "low (global min)",
    })
    return readings


# --------------------------------------------------------------------------------------
# Grid, 3 phi x 5 trough = 15 combinations, via rational_bound.py.
# --------------------------------------------------------------------------------------
def _cell(phi_label: str, phi: float, trough_label: str, trough_price: float,
          trough_hour_utc: str) -> dict:
    floor = floor_worstcase(phi)
    q_star = implied_failure_prob(trough_price, phi, 0.0)
    s_nf0 = below_floor_share(trough_price, phi)
    return {
        "phi_label": phi_label,
        "phi": round(phi, 4),
        "floor": round(floor, 4),
        "trough_label": trough_label,
        "trough_price": trough_price,
        "trough_hour_utc": trough_hour_utc,
        "q_star_rho0": round(q_star, 4),
        "s_nf0": round(s_nf0, 4),
        "fires": trough_price < floor,
    }


def build_grid() -> dict:
    phis = phi_conventions()
    troughs = trough_readings()

    grid = [
        _cell(p["phi_label"], p["phi"], t["trough_label"], t["trough_price"], t["trough_hour_utc"])
        for p in phis
        for t in troughs
    ]

    # The phi=0.08 cross-feed range across all 5 trough readings. NOTE: this block
    # mixes 4 genuinely distinct price FEEDS with composite_second_lowest, a second print
    # within the composite feed rather than a fifth feed, kept as-is for backward
    # compatibility; see feed_trough_only_at_phi_0.08 below for the feed-restricted
    # version.
    at_phi_08 = [c for c in grid if c["phi_label"] == "static_phi_svb_0.08"]
    s_nf0_min_cell = min(at_phi_08, key=lambda c: c["s_nf0"])
    s_nf0_max_cell = max(at_phi_08, key=lambda c: c["s_nf0"])
    cross_feed_range = {
        "phi": PHI_SVB,
        "phi_label": "static_phi_svb_0.08",
        "n_trough_readings": len(at_phi_08),
        "s_nf0_min": s_nf0_min_cell["s_nf0"],
        "s_nf0_min_trough": s_nf0_min_cell["trough_label"],
        "s_nf0_max": s_nf0_max_cell["s_nf0"],
        "s_nf0_max_trough": s_nf0_max_cell["trough_label"],
        "note": "Non-fundamental share stated as a cross-feed range, not a single point.",
    }

    # The phi=0.08 range restricted to the
    # four genuinely distinct price FEEDS, composite, Kraken close, Kraken VWAP,
    # Binance/USDT-deflated, excluding composite_second_lowest, which is a second print
    # within the composite feed, not a fifth feed. This is the block share_range.tex's
    # first sentence must cite; cross_feed_range_at_phi_0.08 above stays unchanged for any
    # existing consumer.
    feed_trough_cells = [c for c in at_phi_08 if c["trough_label"] in FEED_TROUGH_LABELS]
    ft_min_cell = min(feed_trough_cells, key=lambda c: c["s_nf0"])
    ft_max_cell = max(feed_trough_cells, key=lambda c: c["s_nf0"])
    feed_trough_only = {
        "phi": PHI_SVB,
        "phi_label": "static_phi_svb_0.08",
        "n_trough_readings": len(feed_trough_cells),
        "trough_labels_included": FEED_TROUGH_LABELS,
        "excludes": ["composite_second_lowest"],
        "excludes_reason": "a second print within the composite feed, not a distinct feed",
        "s_nf0_min": ft_min_cell["s_nf0"],
        "s_nf0_min_trough": ft_min_cell["trough_label"],
        "s_nf0_max": ft_max_cell["s_nf0"],
        "s_nf0_max_trough": ft_max_cell["trough_label"],
    }

    return {
        "method_source": "rational_bound.py (floor_worstcase, implied_failure_prob, "
                          "below_floor_share)",
        "phi_conventions": phis,
        "trough_readings": troughs,
        "grid": grid,
        "cross_feed_range_at_phi_0.08": cross_feed_range,
        "feed_trough_only_at_phi_0.08": feed_trough_only,
    }


if __name__ == "__main__":
    result = build_grid()
    _assert_safe_write_path(OUT_PATH)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"wrote {OUT_PATH}")
