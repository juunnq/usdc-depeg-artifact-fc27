"""Executed sub-floor depth WHILE THE DISCLOSURE WAS LIVE.

The floor 1 - phi only exists once the impairment has been disclosed, and it stops being
the operative bound once the backstop removes the exposure. An unrestricted count of
sub-floor trades therefore includes prints made before any floor existed. This module
restricts the count to the live window and reports the excluded remainder separately, so
the two can never be conflated.

Window: [disclosure, backstop)
  disclosure  2023-03-11T03:11:00Z  Circle's tweet quantifying the SVB exposure. The
              frozen capture of that tweet carries NO to-the-second publication
              timestamp (its filename timestamp is the Wayback capture time, not the
              post time), so the minute-resolution 03:11 is the finest defensible bound.
  backstop    2023-03-12T22:15:00Z  joint federal statement guaranteeing SVB depositors.

UNITS, which the existing figure gets wrong by name. trough_corroboration.json's
below_floor_depth reports BOTH volume_usdc_below_floor (169,898,078.33, a count of COINS)
and notional_usd_below_floor (154,007,404.51, the dollars actually paid). The first is
frequently quoted as "$169.9M", which labels a coin quantity as dollars. This module
reports both, named for what they are, and never emits a bare currency figure for the coin
count.

READ-ONLY on data/.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from constants import DATA_DIR, RESULTS_DIR

TRADES = DATA_DIR / "n1" / "kraken_usdcusd" / "trades_raw.jsonl"
OUT_PATH = RESULTS_DIR / "subfloor_depth_live.json"

FLOOR = 0.92
DISCLOSURE_UTC = "2023-03-11T03:11:00Z"
BACKSTOP_UTC = "2023-03-12T22:15:00Z"


class FrozenFileError(RuntimeError):
    """Raised if this module is ever pointed at a frozen data/ path for writing."""


def _assert_safe_write_path(path) -> None:
    path = Path(path).resolve()
    data_dir = DATA_DIR.resolve()
    if data_dir in path.parents or path == data_dir:
        raise FrozenFileError(
            f"refusing to write '{path}': it is under the frozen data/ directory."
        )


def load_trades() -> pd.DataFrame:
    """Every trade in the frozen Kraken USDC/USD tape, with a UTC timestamp.

    `time` is UNIX SECONDS. A milliseconds parse of this column succeeds silently and
    yields 1970 dates, so the unit is pinned here rather than inferred.
    """
    recs = []
    with TRADES.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    df = pd.DataFrame(recs)
    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["price"] = pd.to_numeric(df["price"])
    df["volume"] = pd.to_numeric(df["volume"])
    df["notional_usd"] = df["price"] * df["volume"]
    return df.sort_values("ts").reset_index(drop=True)


def _summarise(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n_trades": 0, "volume_usdc": 0.0, "notional_usd": 0.0,
                "clock_hours_touched": 0, "first_trade_utc": None, "last_trade_utc": None}
    return {
        "n_trades": int(len(df)),
        "volume_usdc": round(float(df["volume"].sum()), 2),
        "notional_usd": round(float(df["notional_usd"].sum()), 2),
        "clock_hours_touched": int(df["ts"].dt.floor("h").nunique()),
        "first_trade_utc": df["ts"].min().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_trade_utc": df["ts"].max().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build() -> dict:
    df = load_trades()
    sub = df[df["price"] < FLOOR]

    disclosure = pd.Timestamp(DISCLOSURE_UTC)
    backstop = pd.Timestamp(BACKSTOP_UTC)
    live = sub[(sub["ts"] >= disclosure) & (sub["ts"] < backstop)]
    before = sub[sub["ts"] < disclosure]
    after = sub[sub["ts"] >= backstop]

    live_s = _summarise(live)
    unrestricted = _summarise(sub)

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_file": TRADES.relative_to(DATA_DIR.parent).as_posix(),
        "n_trades_in_tape": int(len(df)),
        "floor": FLOOR,
        "window": {
            "start_utc": DISCLOSURE_UTC,
            "start_basis": "Circle's tweet quantifying the SVB exposure; the frozen "
                            "capture carries no to-the-second publication timestamp, so "
                            "this is minute resolution",
            "end_utc": BACKSTOP_UTC,
            "end_basis": "joint federal statement guaranteeing SVB depositors",
            "interval": "[start, end)",
        },
        "live": live_s,
        "excluded_before_disclosure": _summarise(before),
        "excluded_after_backstop": _summarise(after),
        "unrestricted": unrestricted,
        "units_note": (
            "volume_usdc counts COINS; notional_usd is dollars paid. The unrestricted "
            "volume_usdc of 169,898,078.33 is a coin count and must not be written as "
            "'$169.9M'; the unrestricted dollar figure is notional_usd."
        ),
        "cross_check_vs_trough_corroboration": {
            "expected_n_trades_below_floor": 37303,
            "expected_volume_usdc_below_floor": 169898078.33,
            "expected_notional_usd_below_floor": 154007404.51,
            "matches": (unrestricted["n_trades"] == 37303
                        and abs(unrestricted["volume_usdc"] - 169898078.33) < 0.5
                        and abs(unrestricted["notional_usd"] - 154007404.51) < 0.5),
        },
    }


if __name__ == "__main__":
    result = build()
    _assert_safe_write_path(OUT_PATH)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"wrote {OUT_PATH}")
