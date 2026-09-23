"""TUSD's 11 March 2023 deviation from par (sec:disc's scope-condition
paragraph and Appendix D's eight-candidate-episodes paragraph).

Offline: reads only the frozen TUSD hourly USD price series
(data/n1/tusd2023/tusd_hourly.csv), fetched from the same DeFiLlama coins endpoint
data_fetch.py uses for USDC/USDT/DAI, over the same 2023-03-08->2023-03-17 window.
Computes the 11 March 2023 (UTC) minimum print and its deviation from par in bps.
"""
import json

import pandas as pd

from constants import DATA_DIR, RESULTS_DIR

TUSD_CSV = DATA_DIR / "n1" / "tusd2023" / "tusd_hourly.csv"
OUT_PATH = RESULTS_DIR / "tusd_2023.json"
TARGET_DAY_START = "2023-03-11"
TARGET_DAY_END = "2023-03-12"


def march11_minimum() -> dict:
    """The lowest TUSD print on 11 March 2023 (UTC) in the frozen series, and its
    deviation from par in bps. Raises if the frozen file has no rows that day."""
    df = pd.read_csv(TUSD_CSV)
    dt = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    day = df[(dt >= TARGET_DAY_START) & (dt < TARGET_DAY_END)]
    if day.empty:
        raise ValueError(f"no {TUSD_CSV} rows fall on {TARGET_DAY_START} (UTC)")
    row = day.loc[day["price"].idxmin()]
    price = float(row["price"])
    return {
        "date": TARGET_DAY_START,
        "min_price": price,
        "min_price_timestamp_utc": pd.Timestamp(row["timestamp"], unit="s", tz="UTC").isoformat(),
        "deviation_from_par_bps": round((1.0 - price) * 1e4, 2),
    }


def report() -> dict:
    m = march11_minimum()
    return {
        "source_file": "data/n1/tusd2023/tusd_hourly.csv",
        "source_url": "https://coins.llama.fi/chart/coingecko:true-usd",
        **m,
    }


if __name__ == "__main__":
    r = report()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(r, f, indent=2)
    print(json.dumps(r, indent=2))
