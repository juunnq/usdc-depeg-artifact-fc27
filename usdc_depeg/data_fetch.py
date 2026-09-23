"""Fetch-then-freeze: pull the keyless raw series (DeFiLlama price + supply, Binance
klines), write versioned CSVs + a MANIFEST.json recording source URL, retrieval UTC,
and SHA-256 per file. After freezing, all analysis runs OFFLINE on the snapshot.

Redemptions are a separate, keyed pull, see redemptions.py (FIFO burn-attribution).
This script PRESERVES any existing redemptions entry in the MANIFEST when re-run, so
refreshing the keyless series never silently drops Number 2's provenance.

Two entry points (subcommands):
    python data_fetch.py verify   # offline: recompute SHA-256 of every MANIFEST.json
                                   # files{} entry; the first command a reproducer runs.
                                   # No network, no key.
    python data_fetch.py fetch    # live network pull: price + supply + klines.
                                   # Refuses to overwrite any file already listed in
                                   # MANIFEST.json's files{}, once frozen, a file is
                                   # read-only; re-running `fetch` on an already-frozen
                                   # snapshot fails loudly instead of silently
                                   # overwriting it and rewriting its own hashes.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

import second_event
import usdt_premium
from constants import (CALM_END_S, CALM_START_S, DATA_DIR, DLL_COINS,
                       DLL_STABLECOIN_IDS, EXCLUDED_COINS, WINDOW_END_MS,
                       WINDOW_END_S, WINDOW_START_MS, WINDOW_START_S)
from constants import DAI_BACKING_FILE, REDEMPTIONS_FILE  # paths, not the excluded modules

TIMEOUT = 60
SPAN_HOURS = (WINDOW_END_S - WINDOW_START_S) // 3600  # 216

# Filenames this module's own fetch functions write (the keyless freeze).
OWNED_FILES = {"price_hourly.csv", "binance_usdcusdt_1h.csv", "usdc_supply_daily.csv",
              "excluded_coins_hourly.csv", "excluded_coins_volume.csv"}

# The nine files the original (pre-N1) freeze tracked, derived from each
# producing module's own output-filename constant rather than re-declared as a
# duplicate literal set, so a rename in any producing module updates this
# automatically instead of silently desyncing (fixes R2: tests/test_data_fetch.py
# previously hardcoded its own copy of this set).
ORIGINAL_NINE_FILES = OWNED_FILES | {
    REDEMPTIONS_FILE.name, DAI_BACKING_FILE.name,
    second_event.TERRA_FILE.name, usdt_premium.USDT_USD_FILE.name,
}


class FrozenFileError(RuntimeError):
    """Raised by `fetch` when asked to overwrite a file MANIFEST.json's files{}
    already lists as frozen. Frozen files are read-only, use `verify` instead."""


def _refuse_if_manifested(path, existing_files: dict) -> None:
    if path.name in existing_files:
        raise FrozenFileError(
            f"refusing to overwrite '{path.name}': already frozen and listed in "
            f"MANIFEST.json files{{}}. Frozen files are read-only -- run "
            f"`python data_fetch.py verify` to check it instead of `fetch`."
        )


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_price_hourly(manifest, existing_files: dict) -> "os.PathLike":
    """DeFiLlama coins API — hourly USD price for USDC/USDT/DAI.
    A failure on a control coin (USDT/DAI) is logged and skipped; USDC is required."""
    rows = []
    for sym, coin in DLL_COINS.items():
        url = (f"https://coins.llama.fi/chart/{coin}"
               f"?start={WINDOW_START_S}&span={SPAN_HOURS}&period=1h")
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            prices = r.json()["coins"][coin]["prices"]
            for p in prices:
                rows.append({"symbol": sym, "timestamp": p["timestamp"], "price": p["price"]})
            manifest["sources"].append({"file": "price_hourly.csv", "symbol": sym,
                                        "url": url, "retrieved_utc": _utcnow(),
                                        "n_points": len(prices)})
        except Exception as e:  # control coins are best-effort
            print(f"[price {sym}] FAILED: {e}", file=sys.stderr)
            manifest["sources"].append({"file": "price_hourly.csv", "symbol": sym,
                                        "url": url, "retrieved_utc": _utcnow(),
                                        "error": str(e)})
    if not any(r["symbol"] == "USDC" for r in rows):
        raise RuntimeError("USDC price fetch failed — cannot freeze the headline input.")
    df = pd.DataFrame(rows).sort_values(["symbol", "timestamp"])
    out = DATA_DIR / "price_hourly.csv"
    _refuse_if_manifested(out, existing_files)
    df.to_csv(out, index=False)
    return out


def fetch_binance_klines(manifest, existing_files: dict) -> "os.PathLike":
    """Binance USDCUSDT 1h klines — an independent price cross-check."""
    url = (f"https://api.binance.com/api/v3/klines?symbol=USDCUSDT&interval=1h"
           f"&startTime={WINDOW_START_MS}&endTime={WINDOW_END_MS}&limit=1000")
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    k = r.json()
    df = pd.DataFrame([{"open_time": x[0], "open": float(x[1]), "high": float(x[2]),
                        "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])}
                       for x in k])
    out = DATA_DIR / "binance_usdcusdt_1h.csv"
    _refuse_if_manifested(out, existing_files)
    df.to_csv(out, index=False)
    manifest["sources"].append({"file": "binance_usdcusdt_1h.csv", "url": url,
                                "retrieved_utc": _utcnow(), "n_points": len(k)})
    return out


def fetch_usdc_supply(manifest, existing_files: dict) -> "os.PathLike":
    """DeFiLlama stablecoins API — USDC daily circulating supply (context only)."""
    sid = DLL_STABLECOIN_IDS["USDC"]
    url = f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={sid}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    rows = []
    for d in r.json():
        try:
            ts = int(d["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if WINDOW_START_S - 86400 <= ts <= WINDOW_END_S + 86400:
            circ = d.get("totalCirculating", {})
            val = circ.get("peggedUSD") if isinstance(circ, dict) else None
            rows.append({"date": ts, "circulating_usd": val})
    df = pd.DataFrame(rows).sort_values("date")
    out = DATA_DIR / "usdc_supply_daily.csv"
    _refuse_if_manifested(out, existing_files)
    df.to_csv(out, index=False)
    manifest["sources"].append({"file": "usdc_supply_daily.csv", "url": url,
                                "retrieved_utc": _utcnow(), "n_points": len(df)})
    return out


def fetch_excluded_coins(manifest, existing_files: dict) -> list:
    """Freeze the evidence behind the placebo EXCLUSIONS (USDP / GUSD / BUSD).

    (a) excluded_coins_hourly.csv — DeFiLlama hourly USD price for each coin over
        BOTH the calm baseline window (Feb 16 - Mar 8) and the crisis window
        (Mar 8 - Mar 17), tagged by a ``window`` column. Same error-tolerant
        per-coin pattern as fetch_price_hourly (every leg is best-effort).
    (b) excluded_coins_volume.csv — best-effort keyless venue volumes for the
        crisis window: Binance 1h klines (USDPUSDT, BUSDUSDT; USDT-quoted volume
        ~= USD at par) and Gemini GUSD/USD 1h candles. Gemini's v2 candles
        endpoint only serves recent history, so a March-2023 pull may legitimately
        return nothing — logged and skipped, never fabricated.
    Returns both paths for MANIFEST hashing."""
    rows = []
    windows = [("calm", CALM_START_S, (CALM_END_S - CALM_START_S) // 3600),
               ("crisis", WINDOW_START_S, SPAN_HOURS)]
    for sym, coin in EXCLUDED_COINS.items():
        for wname, start, span in windows:
            url = (f"https://coins.llama.fi/chart/{coin}"
                   f"?start={start}&span={span}&period=1h")
            try:
                r = requests.get(url, timeout=TIMEOUT)
                r.raise_for_status()
                prices = r.json()["coins"][coin]["prices"]
                for p in prices:
                    rows.append({"symbol": sym, "window": wname,
                                 "timestamp": p["timestamp"], "price": p["price"]})
                manifest["sources"].append({"file": "excluded_coins_hourly.csv",
                                            "symbol": sym, "window": wname, "url": url,
                                            "retrieved_utc": _utcnow(),
                                            "n_points": len(prices)})
            except Exception as e:  # excluded coins are best-effort
                print(f"[excluded {sym} {wname}] FAILED: {e}", file=sys.stderr)
                manifest["sources"].append({"file": "excluded_coins_hourly.csv",
                                            "symbol": sym, "window": wname, "url": url,
                                            "retrieved_utc": _utcnow(), "error": str(e)})
    df = pd.DataFrame(rows).sort_values(["symbol", "window", "timestamp"])
    out_px = DATA_DIR / "excluded_coins_hourly.csv"
    _refuse_if_manifested(out_px, existing_files)
    df.to_csv(out_px, index=False)

    vrows = []
    for sym in ("USDPUSDT", "BUSDUSDT"):
        url = (f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h"
               f"&startTime={WINDOW_START_MS}&endTime={WINDOW_END_MS}&limit=1000")
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            k = r.json()
            for x in k:  # kline: [open_time, open, high, low, close, volume, ...]
                vrows.append({"venue": "Binance", "symbol": sym, "open_time": x[0],
                              "low": float(x[3]), "close": float(x[4]),
                              "volume": float(x[5])})
            manifest["sources"].append({"file": "excluded_coins_volume.csv",
                                        "venue": "Binance", "symbol": sym, "url": url,
                                        "retrieved_utc": _utcnow(), "n_points": len(k)})
        except Exception as e:
            print(f"[excluded volume {sym}] FAILED: {e}", file=sys.stderr)
            manifest["sources"].append({"file": "excluded_coins_volume.csv",
                                        "venue": "Binance", "symbol": sym, "url": url,
                                        "retrieved_utc": _utcnow(), "error": str(e)})
    gurl = "https://api.gemini.com/v2/candles/gusdusd/1hr"
    try:
        r = requests.get(gurl, timeout=TIMEOUT)
        r.raise_for_status()
        # candle: [time_ms, open, high, low, close, volume]; filter to the crisis window
        cands = [c for c in r.json() if WINDOW_START_MS <= c[0] < WINDOW_END_MS]
        for c in cands:
            vrows.append({"venue": "Gemini", "symbol": "GUSDUSD", "open_time": c[0],
                          "low": float(c[3]), "close": float(c[4]),
                          "volume": float(c[5])})
        manifest["sources"].append({"file": "excluded_coins_volume.csv",
                                    "venue": "Gemini", "symbol": "GUSDUSD", "url": gurl,
                                    "retrieved_utc": _utcnow(), "n_points": len(cands)})
    except Exception as e:
        print(f"[excluded volume GUSDUSD] FAILED: {e}", file=sys.stderr)
        manifest["sources"].append({"file": "excluded_coins_volume.csv",
                                    "venue": "Gemini", "symbol": "GUSDUSD", "url": gurl,
                                    "retrieved_utc": _utcnow(), "error": str(e)})
    out_vol = DATA_DIR / "excluded_coins_volume.csv"
    _refuse_if_manifested(out_vol, existing_files)
    pd.DataFrame(vrows, columns=["venue", "symbol", "open_time", "low", "close",
                                 "volume"]).to_csv(out_vol, index=False)
    return [out_px, out_vol]


def preserve_foreign_entries(manifest: dict, existing: dict, owned: set) -> list:
    """Carry over manifest entries owned by OTHER modules (redemptions.py,
    dai_contagion.py, second_event.py, usdt_premium.py) so re-running this
    keyless refresh never drops their provenance. This routine only owns the
    files it just fetched; everything else in the prior manifest survives."""
    preserved = []
    for s in existing.get("sources", []):
        if s.get("file") not in owned:
            manifest["sources"].append(s)
    for name, meta in existing.get("files", {}).items():
        if name not in owned:
            manifest["files"][name] = meta
            preserved.append(name)
    return preserved


def fetch():
    """Live network pull: freeze price + supply + klines. Refuses (per-file, via
    _refuse_if_manifested) to overwrite anything MANIFEST.json already lists as
    frozen, run `verify` instead to check an existing snapshot."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    mpath = DATA_DIR / "MANIFEST.json"
    existing = json.load(open(mpath)) if mpath.exists() else {}
    existing_files = existing.get("files", {})

    manifest = {
        "frozen_utc": _utcnow(),
        "window_utc": ["2023-03-08T00:00:00Z", "2023-03-17T00:00:00Z"],
        "sources": [], "files": {},
    }
    files = ([fetch_price_hourly(manifest, existing_files),
             fetch_binance_klines(manifest, existing_files),
             fetch_usdc_supply(manifest, existing_files)]
             + fetch_excluded_coins(manifest, existing_files))
    for f in files:
        manifest["files"][f.name] = {"sha256": _sha256(f), "bytes": f.stat().st_size}

    preserved = preserve_foreign_entries(manifest, existing, {f.name for f in files})

    with open(mpath, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print("Frozen:", ", ".join(f.name for f in files))
    if preserved:
        print("Preserved existing entries (owned by other modules):", ", ".join(preserved))
    print("Manifest:", mpath)


def verify() -> bool:
    """Offline: recompute SHA-256 of every file MANIFEST.json's files{} lists
    (regardless of which script produced it) and report any mismatch. This is
    the first command a reproducer runs, no network, no key, no write."""
    mpath = DATA_DIR / "MANIFEST.json"
    if not mpath.exists():
        print(f"VERIFY FAILED: no MANIFEST.json at {mpath}", file=sys.stderr)
        return False
    files = json.load(open(mpath)).get("files", {})
    if not files:
        print(f"VERIFY FAILED: MANIFEST.json has no files{{}} entries ({mpath})", file=sys.stderr)
        return False
    ok = True
    for name, meta in sorted(files.items()):
        path = DATA_DIR / name
        if not path.is_file():
            print(f"MISSING: {name} (listed in MANIFEST.json, absent on disk)", file=sys.stderr)
            ok = False
            continue
        actual = _sha256(path)
        expected = meta.get("sha256")
        if actual != expected:
            print(f"MISMATCH: {name}\n  expected sha256={expected}\n  actual   sha256={actual}",
                  file=sys.stderr)
            ok = False
    if ok:
        print(f"VERIFY OK: {len(files)} frozen file(s) match MANIFEST.json ({mpath})")
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="Freeze (network) or verify (offline) the keyless price/supply/klines snapshot.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="live network pull; refuses to overwrite an already-frozen file")
    sub.add_parser("verify", help="offline: recompute SHA-256 of every MANIFEST.json files{} entry")
    args = ap.parse_args()
    if args.command == "fetch":
        fetch()
    else:
        sys.exit(0 if verify() else 1)


if __name__ == "__main__":
    main()
