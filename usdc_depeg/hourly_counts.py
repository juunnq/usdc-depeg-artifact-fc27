"""Reconciles Table 1's per-coin hourly observation counts (212 USDC / 213 USDT
/ 214 DAI, out of the analysis window's 216 expected hours) against the frozen composite
price series, and names the missing UTC hours explicitly per coin.

Expected index: constants.WINDOW_START_S to WINDOW_END_S at 1h steps is exactly 216 hourly
slots (this matches data_fetch.py's own SPAN_HOURS = (WINDOW_END_S-WINDOW_START_S)//3600,
the value used when the DeFiLlama pull was originally made with ?span=216&period=1h, so
216 is the number the fetch itself targeted, not a number invented for this reconciliation).

Each row in data/price_hourly.csv lands within a few minutes of its target hour mark (max
observed offset 295s across all three coins, verified below) and no two rows round to the
same expected hour for any coin, so rounding each raw timestamp to the nearest hour gives an
unambiguous, lossless map from "row present" to "expected hour present", the diff against
the full 216-hour index is the per-coin missing-hours list.

Offline / keyless. Reads only the frozen snapshot; writes only results/hourly_counts.json.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from constants import DATA_DIR, RESULTS_DIR, WINDOW_START_S, WINDOW_END_S
from data_io import load_price

SPAN_HOURS = (WINDOW_END_S - WINDOW_START_S) // 3600  # 216
COINS = ["USDC", "DAI", "USDT"]
MAX_ROUNDING_OFFSET_S = 1800  # half an hour; offsets this large would make the map ambiguous


def _write_result(path: Path, obj: dict) -> None:
    resolved = path.resolve()
    if DATA_DIR.resolve() in resolved.parents or resolved == DATA_DIR.resolve():
        raise PermissionError(f"refusing to write under frozen data/: {resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_hourly_index() -> list:
    """The full 216-hour expected UTC index, WINDOW_START_S inclusive, step 1h."""
    return [WINDOW_START_S + i * 3600 for i in range(SPAN_HOURS)]


def coin_missing_hours(symbol: str) -> dict:
    """Rounds each observed raw timestamp to its nearest expected hour and diffs against
    the full 216-hour index. Raises if any row's offset from the nearest hour mark is
    large enough to make the rounding ambiguous, or if two rows round to the same hour
    (both would silently corrupt the count otherwise)."""
    expected = expected_hourly_index()
    expected_set = set(expected)
    df = load_price(symbol)
    ts = df["timestamp"].to_numpy()

    offsets = (ts - WINDOW_START_S) % 3600
    signed_offset = np.where(offsets > 1800, offsets - 3600, offsets)
    max_abs_offset = int(np.max(np.abs(signed_offset))) if len(signed_offset) else 0
    if max_abs_offset > MAX_ROUNDING_OFFSET_S:
        raise ValueError(
            f"{symbol}: a raw timestamp is {max_abs_offset}s from its nearest hour mark "
            f"(> {MAX_ROUNDING_OFFSET_S}s) -- rounding to hour would be ambiguous; "
            "reconciliation needs a different method for this series."
        )

    rounded = (np.round((ts - WINDOW_START_S) / 3600).astype(np.int64) * 3600
               + WINDOW_START_S)
    n_dupes = len(rounded) - len(set(int(x) for x in rounded))
    if n_dupes:
        raise ValueError(f"{symbol}: {n_dupes} raw rows round to a duplicate expected hour")

    present = set(int(x) for x in rounded)
    outside = sorted(present - expected_set)
    if outside:
        raise ValueError(f"{symbol}: {len(outside)} rows round outside the expected window")

    missing = sorted(expected_set - present)
    return {
        "n_observed": int(len(df)),
        "n_expected": SPAN_HOURS,
        "n_missing": len(missing),
        "max_abs_rounding_offset_s": max_abs_offset,
        "missing_hours_utc": [_fmt(x) for x in missing],
    }


def report() -> dict:
    per_coin = {c: coin_missing_hours(c) for c in COINS}
    all_missing = {frozenset(per_coin[c]["missing_hours_utc"]) for c in COINS}
    common = sorted(set.intersection(*(set(per_coin[c]["missing_hours_utc"]) for c in COINS)))
    return {
        "manuscript_reference": "fc27.tex Table 1 (line 257: 'USDC/USDT/DAI price (1h) & "
                                "DeFiLlama coins API & 212/213/214 pts') and fc27.tex:407-410",
        "window": {
            "start_utc": _fmt(WINDOW_START_S),
            "end_utc": _fmt(WINDOW_END_S),
            "expected_hours": SPAN_HOURS,
            "source": "constants.WINDOW_START_S / WINDOW_END_S",
        },
        "source_file": "data/price_hourly.csv",
        "per_coin": per_coin,
        "hours_missing_in_all_three_coins": common,
        "counts_match_manuscript": {
            "USDC": per_coin["USDC"]["n_observed"] == 212,
            "DAI": per_coin["DAI"]["n_observed"] == 214,
            "USDT": per_coin["USDT"]["n_observed"] == 213,
        },
    }


if __name__ == "__main__":
    r = report()
    _write_result(RESULTS_DIR / "hourly_counts.json", r)
    print(json.dumps(r, indent=2))
