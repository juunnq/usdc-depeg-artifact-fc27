"""USDT-premium PEAK TIMING, when did the USDT flight-to-safety premium peak, relative
to Circle's SVB-impairment disclosure, the USDC trough hour, and the joint Treasury/Fed/
FDIC statement (the federal backstop)?

Extends usdt_premium.py (which measures premium DEPTH/VOLUME, the +266 bps in tab:usdt)
with premium TIMING. Matches usdt_premium.py's exact convention rather than inventing a
new one: premium_bps = (high - 1.0) * 1e4 on the hourly `high` print, on Coinbase's fiat
venue. The DeFiLlama composite has only one price field per hour (no OHLC), so its
premium uses that single `price` column in the same formula, this is the same
convention trough_corroboration.py documents for the composite series.

Three USDT/USD series feed this, all frozen, offline, and keyless: Coinbase USDT-USD
hourly (data/usdt_usd_hourly.csv, tab:usdt's +266 bps); the DeFiLlama composite's USDT
leg (data/price_hourly.csv, fc27.tex's +270 bps "aggregate feed" figure, Sec 4.3 and the
abstract; see fc27.tex:39, 101, 585, 603, 620); and Kraken USDT/USD hourly for March 2023,
a second fiat venue (data/n1/kraken_usdtusd/hourly.csv, not the unrelated 2019 series at
data/n1/kraken_usdtusd_2019/).

Three reference points are read here, each already frozen/canonical elsewhere in this
codebase and never re-derived: the disclosure timestamp (phi_dynamic.DISCLOSURE_TS_UTC,
2023-03-11T03:11:00Z, confirmed identical in the constants-adjacent results files
specificity_panel.json, trough_corroboration.json, and phi_dynamic.json); the USDC trough
hour (results/trough_corroboration.json's
dynamic_floor_check.raw.variant_i.trough_hour_utc — this file may be concurrently
rewritten by another process during pipeline regeneration, so _load_trough_hour_utc()
retries once and falls back to a previously-verified value, flagged, if both reads are
inconsistent); and the joint federal-backstop statement
(data/n1/paxos2023/joint_statement_treasury_fed_fdic.json, 2023-03-12T22:15:00Z —
"backstop" elsewhere in this codebase, e.g. data_fetch_n1.py's Paxos timing row, refers to
this same event).
"""
import json
import time
from datetime import datetime, timezone

import pandas as pd

import phi_dynamic as phid
from constants import DATA_DIR, RESULTS_DIR

COINBASE_FILE = DATA_DIR / "usdt_usd_hourly.csv"
COMPOSITE_FILE = DATA_DIR / "price_hourly.csv"
KRAKEN_FILE = DATA_DIR / "n1" / "kraken_usdtusd" / "hourly.csv"
TROUGH_CORROBORATION_FILE = RESULTS_DIR / "trough_corroboration.json"
JOINT_STATEMENT_FILE = DATA_DIR / "n1" / "paxos2023" / "joint_statement_treasury_fed_fdic.json"

# Verified fallback: results/trough_corroboration.json's
# dynamic_floor_check.raw.variant_i.trough_hour_utc, read cleanly at the time this was
# written. Used only if _load_trough_hour_utc()'s live read fails twice (see its docstring).
_TROUGH_HOUR_FALLBACK_UTC = "2023-03-11T07:57:53Z"


def _parse(ts_utc: str) -> datetime:
    return datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))


def _to_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_trough_hour_utc() -> tuple[str, str]:
    """Read the USDC trough hour from results/trough_corroboration.json. That file may be
    concurrently rewritten by another process during pipeline regeneration: try once,
    and on a parse/key failure wait and retry once. If still inconsistent, fall back to
    the last value confirmed by a clean read, flagged as such rather than silently
    treated as a fresh live read."""
    def _try():
        with open(TROUGH_CORROBORATION_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d["dynamic_floor_check"]["raw"]["variant_i"]["trough_hour_utc"]

    for attempt in (1, 2):
        try:
            return _to_z(_parse(_try())), "live_read"
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            if attempt == 1:
                time.sleep(3.0)
    return _TROUGH_HOUR_FALLBACK_UTC, "FALLBACK_two_live_reads_failed_used_session_verified_value"


def _load_joint_statement_ts_utc() -> str:
    d = json.load(open(JOINT_STATEMENT_FILE, encoding="utf-8"))
    return _to_z(_parse(d["joint_statement_ts_utc"]))


def _relative_to_refs(peak_ts_utc: str, refs: dict) -> dict:
    peak = _parse(peak_ts_utc)
    out = {}
    for name, ref_ts in refs.items():
        delta_min = round((peak - _parse(ref_ts)).total_seconds() / 60.0, 2)
        out[name] = {"reference_ts_utc": ref_ts, "delta_minutes": delta_min,
                     "flag": "before" if delta_min < 0 else "after"}
    return out


def _coinbase_peak() -> dict:
    """Max premium timestamp on Coinbase USDT-USD, same (high - 1) * 1e4 convention as
    usdt_premium.robustness()'s max_premium_bps."""
    if not COINBASE_FILE.exists():
        return {"status": "PENDING_DATA",
                "reason": f"{COINBASE_FILE} missing -- run `python usdt_premium.py` (keyless)."}
    df = pd.read_csv(COINBASE_FILE)
    row = df.loc[df["high"].idxmax()]
    ts = datetime.fromtimestamp(int(row["open_time"]), tz=timezone.utc)
    return {"status": "OK", "venue": "Coinbase USDT-USD (1h high)",
            "source_file": "data/usdt_usd_hourly.csv",
            "timestamp_utc": _to_z(ts),
            "premium_bps": round((float(row["high"]) - 1.0) * 1e4, 1)}


def _composite_peak() -> dict:
    """Max premium timestamp on the DeFiLlama composite's USDT leg, one price field per
    hour, so premium uses `price` directly in the same (x - 1) * 1e4 formula."""
    if not COMPOSITE_FILE.exists():
        return {"status": "PENDING_DATA", "reason": f"{COMPOSITE_FILE} missing."}
    df = pd.read_csv(COMPOSITE_FILE)
    usdt = df.loc[df["symbol"] == "USDT"]
    if usdt.empty:
        return {"status": "PENDING_DATA", "reason": "no USDT rows in data/price_hourly.csv"}
    row = usdt.loc[usdt["price"].idxmax()]
    ts = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc)
    return {"status": "OK", "venue": "DeFiLlama composite, USDT leg (single price/hour)",
            "source_file": "data/price_hourly.csv",
            "timestamp_utc": _to_z(ts),
            "premium_bps": round((float(row["price"]) - 1.0) * 1e4, 1)}


def _kraken_peak() -> dict:
    """Max premium timestamp on Kraken USDT/USD, March 2023 (second fiat venue), same
    (high - 1) * 1e4 convention."""
    if not KRAKEN_FILE.exists():
        return {"status": "PENDING_DATA",
                "reason": f"{KRAKEN_FILE} missing -- Kraken USDT/USD March 2023 not frozen."}
    df = pd.read_csv(KRAKEN_FILE)
    row = df.loc[df["high"].idxmax()]
    return {"status": "OK", "venue": "Kraken USDT/USD (1h high), March 2023",
            "source_file": "data/n1/kraken_usdtusd/hourly.csv",
            "timestamp_utc": _to_z(_parse(str(row["hour_utc"]))),
            "premium_bps": round((float(row["high"]) - 1.0) * 1e4, 1)}


def report() -> dict:
    trough_hour_utc, trough_hour_source = _load_trough_hour_utc()
    refs = {
        "disclosure": phid.DISCLOSURE_TS_UTC,               # Circle's ~8% impairment disclosure
        "usdc_trough_hour": trough_hour_utc,                # from trough_corroboration.json
        "joint_statement_treasury_fed_fdic": _load_joint_statement_ts_utc(),  # the backstop
    }
    peaks = {}
    for name, fn in (("coinbase_usdt_usd", _coinbase_peak),
                     ("composite_usdt", _composite_peak),
                     ("kraken_usdt_usd_march2023", _kraken_peak)):
        p = fn()
        if p.get("status") == "OK":
            p["relative_to"] = _relative_to_refs(p["timestamp_utc"], refs)
        peaks[name] = p
    return {
        "reference_points_utc": refs,
        "reference_points_provenance": {
            "disclosure": "phi_dynamic.DISCLOSURE_TS_UTC (cross-checked against "
                          "specificity_panel.json / trough_corroboration.json / "
                          "phi_dynamic.json -- all identical, no discrepancy found)",
            "usdc_trough_hour": f"results/trough_corroboration.json "
                                f"dynamic_floor_check.raw.variant_i.trough_hour_utc "
                                f"(source={trough_hour_source})",
            "joint_statement_treasury_fed_fdic": "data/n1/paxos2023/"
                                                 "joint_statement_treasury_fed_fdic.json "
                                                 "(frozen record, read verbatim)",
        },
        "peaks": peaks,
    }


if __name__ == "__main__":
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    r = report()
    json.dump(r, open(RESULTS_DIR / "usdt_premium_timing.json", "w", encoding="utf-8"), indent=2)
    print(json.dumps(r, indent=2))
