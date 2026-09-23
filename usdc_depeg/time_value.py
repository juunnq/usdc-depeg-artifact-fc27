"""Time-value discount magnitudes for the redemption-delay channel
(p.7's wrapper-risk clause; sec:disc's "How large can rational friction be?" paragraph).

Offline: reads only the frozen U.S. Treasury daily par yield curve for 10 Mar 2023
(data/n1/treasury2023/daily_treasury_yield_curve_2023.csv) plus the already-frozen
floor/trough inputs the rest of the pipeline uses (rational_bound.floor_worstcase,
data_io.observed_trough), no number here is typed from memory.

Computes, all via the simple-interest approximation discount = rate * (days/365)
(appropriate for a short delay against a par-yield input):
  (i)   the time-value discount, in bps, for a 2-day and a 3-day delay at the 1-month
        and 3-month par yields;
  (ii)  the delay length, in days, that would discount the disclosed floor (1-phi) to
        the observed composite trough at those same yields;
  (iii) the annualised rate that would rationalise the FULL par-to-trough gap over a
        3-day delay, the rhetorical "how absurd would the rate have to be" check.
"""
import csv
import json

import data_io
from constants import DATA_DIR, PHI_SVB, RESULTS_DIR
from rational_bound import floor_worstcase

TREASURY_CSV = DATA_DIR / "n1" / "treasury2023" / "daily_treasury_yield_curve_2023.csv"
TARGET_DATE = "03/10/2023"  # de-peg trough day; the CFP-cited yield-curve snapshot
OUT_PATH = RESULTS_DIR / "time_value.json"
DAYS_PER_YEAR = 365


def par_yields(date: str = TARGET_DATE) -> dict:
    """1-month and 3-month par yields (annualised, as decimals) for `date`, read from
    the frozen Treasury daily par yield curve. Raises if the date is not in the file."""
    with open(TREASURY_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Date"] == date:
                return {"1mo": float(row["1 Mo"]) / 100.0, "3mo": float(row["3 Mo"]) / 100.0}
    raise ValueError(f"{date} not found in frozen {TREASURY_CSV}")


def discount_bps(rate: float, days: int) -> float:
    """Simple-interest time-value discount, in bps, for a `days`-long delay at
    annualised par yield `rate`: discount = rate * (days / 365)."""
    return rate * (days / DAYS_PER_YEAR) * 1e4


def delay_days_to_trough(rate: float, floor: float, trough: float) -> float:
    """Delay length, in days, at annualised par yield `rate` that discounts `floor` to
    `trough`: trough = floor * (1 - rate*T)  =>  T = (1 - trough/floor) / rate (years)."""
    required_discount = 1.0 - trough / floor
    return (required_discount / rate) * DAYS_PER_YEAR


def annualised_rate_for_gap(days: int, gap_bps: float) -> float:
    """Annualised rate that would rationalise the full par-to-trough gap (bps) via pure
    time value over `days`: gap = rate * (days/365)  =>  rate = gap / (days/365)."""
    return (gap_bps / 1e4) / (days / DAYS_PER_YEAR)


def report() -> dict:
    yields = par_yields()
    floor = floor_worstcase(PHI_SVB)
    trough = data_io.observed_trough("USDC")
    gap_bps = (1.0 - trough) * 1e4

    delay_bps = {f"{tenor}_{d}d": round(discount_bps(r, d), 4)
                 for tenor, r in yields.items() for d in (2, 3)}
    delay_days = {tenor: round(delay_days_to_trough(r, floor, trough), 2)
                  for tenor, r in yields.items()}

    return {
        "source_file": "data/n1/treasury2023/daily_treasury_yield_curve_2023.csv",
        "source_url": ("https://home.treasury.gov/resource-center/data-chart-center/"
                        "interest-rates/daily-treasury-rates.csv/2023/all?"
                        "type=daily_treasury_yield_curve&field_tdr_date_value=2023"
                        "&page&_format=csv"),
        "source_date": TARGET_DATE,
        "par_yields": yields,
        "floor": floor,
        "trough": trough,
        "par_to_trough_gap_bps": round(gap_bps, 2),
        "discount_bps_by_tenor_and_days": delay_bps,
        "discount_bps_range_2to3d": [min(delay_bps.values()), max(delay_bps.values())],
        "delay_days_to_trough_by_tenor": delay_days,
        "delay_days_to_trough_range": [min(delay_days.values()), max(delay_days.values())],
        "annualised_rate_for_full_gap_over_3d": round(annualised_rate_for_gap(3, gap_bps), 4),
    }


if __name__ == "__main__":
    r = report()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(r, f, indent=2)
    print(json.dumps(r, indent=2))
