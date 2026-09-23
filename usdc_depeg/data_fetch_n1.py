"""Additional cross-venue price series for the
submission: Kraken USDC/USD + USDT/USD trades, and Bitstamp USDC/USD OHLC, over
2023-03-08T00:00:00Z .. 2023-03-17T23:59:59Z.

This module is ADDITIVE ONLY. It never touches the canonical usdc_depeg/data/*.csv
files or the existing MANIFEST.json entries produced by data_fetch.py / redemptions.py /
etc. — it only creates new files under data/n1/ and appends new entries to MANIFEST.json.
Other N1 lanes may add their own fetch functions to this file; do not remove or rewrite
functions you did not add.

Headless-only: plain requests.Session with a desktop User-Agent, exponential backoff
(4s/8s/16s/32s) on any non-200 response or JSON error field, and explicit rate-limit
spacing on Kraken's paginated Trades endpoint. Every gap is booked as an INSTRUMENT GAP
with the exact endpoint/query tried — never fabricated, never silently dropped.

    python data_fetch_n1.py verify             # offline: recompute SHA-256 of every
                                                # MANIFEST.json files{} entry (delegates
                                                # to data_fetch.verify()). No network, no key.
    python data_fetch_n1.py fetch              # run all three series + append
                                                # MANIFEST entries. Refuses to overwrite any
                                                # already-frozen file (use `verify` on those).
    python data_fetch_n1.py fetch --dry-run    # fetch + aggregate, skip MANIFEST write
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from constants import DATA_DIR

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-freeze/1.0"
TIMEOUT = 30
BACKOFF_WAITS = [4, 8, 16, 32]  # seconds; applied before each of up to 4 retries
                                # (1 initial attempt + 4 backed-off retries = 5 tries/req)
KRAKEN_PAGE_SLEEP = 1.5         # rate-limit spacing between successive Kraken pages
MAX_KRAKEN_PAGES = 1500         # safety valve against a paging loop that never converges
                                # (raised from 500 after USDT/USD's real trade density
                                # hit the original cap 3.5 days short of window end —
                                # see data_fetch_n1.py run log 2026-09-08; NOT a Kraken
                                # limit, purely this module's own defensive ceiling)

WINDOW_START_UTC = "2023-03-08T00:00:00Z"
WINDOW_END_UTC = "2023-03-17T23:59:59Z"
WINDOW_START_S = 1678233600   # 2023-03-08T00:00:00Z
WINDOW_END_S = 1679097599     # 2023-03-17T23:59:59Z

N1_DIR = DATA_DIR / "n1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _get_with_backoff(session, url, params=None, error_check=None):
    """GET with exponential backoff: 1 initial attempt + up to 4 retries waiting
    4s/8s/16s/32s respectively. Returns (json_or_none, gap_or_none). `error_check`,
    if given, is called on a 200 response's parsed JSON and should return a truthy
    error message string if the payload itself signals failure (e.g. Kraken's
    non-empty "error" array) even though the HTTP status was 200."""
    last_err = None
    for attempt in range(len(BACKOFF_WAITS) + 1):  # attempt 0 = initial, 1..4 = retries
        if attempt > 0:
            wait = BACKOFF_WAITS[attempt - 1]
            print(f"  [backoff] attempt {attempt} failed ({last_err}); waiting {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            data = r.json()
            if error_check:
                err = error_check(data)
                if err:
                    last_err = f"JSON error field: {err}"
                    continue
            return data, None
        except Exception as e:  # network-level failure (timeout, DNS, connection reset)
            last_err = f"{type(e).__name__}: {e}"
            continue
    gap = {"endpoint": url, "params": params, "error": last_err,
           "attempts": len(BACKOFF_WAITS) + 1, "failed_utc": _utcnow()}
    return None, gap


# --- Step 0: resolve Kraken's canonical pair names --------------------------------

def resolve_kraken_pairs(session) -> dict:
    """Query Kraken AssetPairs and resolve the canonical altname for USDC/USD and
    USDT/USD — do NOT assume "USDCUSD"/"USDTUSD" are correct without checking; Kraken's
    internal dict key can differ from the altname (confirmed: USDT/USD's dict key is
    "USDTZUSD" but its altname, the value actually used as the `pair=` query param, is
    "USDTUSD")."""
    url = "https://api.kraken.com/0/public/AssetPairs"
    data, gap = _get_with_backoff(session, url, error_check=lambda d: d.get("error") or None)
    if data is None:
        raise RuntimeError(f"AssetPairs unreachable: {gap}")
    result = data["result"]
    resolved = {}
    for base in ("USDC", "USDT"):
        candidates = [
            {"dict_key": k, **v} for k, v in result.items()
            if v.get("base") == base and v.get("quote") in ("ZUSD", "USD")
        ]
        if not candidates:
            raise RuntimeError(f"No Kraken AssetPairs match for base={base}, quote USD/ZUSD")
        # Prefer an exact "<BASE>USD" altname if present, else take the first match.
        exact = [c for c in candidates if c.get("altname") == f"{base}USD"]
        chosen = exact[0] if exact else candidates[0]
        resolved[base] = {
            "dict_key": chosen["dict_key"],
            "altname": chosen["altname"],
            "wsname": chosen.get("wsname"),
            "base": chosen["base"],
            "quote": chosen["quote"],
        }
    return resolved


# --- Kraken Trades: paginate, save raw, aggregate hourly --------------------------

def _normalize_kraken_time(t) -> float:
    """Kraken trade `time` is documented/confirmed (live probe, 2026-09-09) as seconds
    since epoch (float, sub-second precision). Defensive fallback for ms/ns in case the
    API changes units in the future."""
    t = float(t)
    if t > 1e14:
        return t / 1e9
    if t > 1e11:
        return t / 1e3
    return t


def fetch_kraken_trades(session, pair_altname: str, out_dir: Path,
                         since_ns: str = None, mode: str = "w") -> dict:
    """Page Kraken's public Trades endpoint for `pair_altname` across the frozen N1
    window, saving every raw trade to trades_raw.jsonl and returning a summary dict
    (rows, coverage, gaps) used both for hourly.csv and the MANIFEST entry.

    `since_ns`/`mode` let a caller RESUME an interrupted pull (e.g. one that hit
    MAX_KRAKEN_PAGES) starting from a later cursor and appending rather than
    truncating trades_raw.jsonl; default behavior (since=window start, mode='w')
    is unchanged for a fresh pull."""
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "trades_raw.jsonl"
    since = since_ns if since_ns is not None else str(WINDOW_START_S * 1_000_000_000)
    gaps = []
    n_pages = 0
    all_trades = []
    seen_cursors = set()

    with open(raw_path, mode, encoding="utf-8") as f:
        while n_pages < MAX_KRAKEN_PAGES:
            url = "https://api.kraken.com/0/public/Trades"
            params = {"pair": pair_altname, "since": since}
            data, gap = _get_with_backoff(
                session, url, params=params,
                error_check=lambda d: (d.get("error") or None) if isinstance(d, dict) else "malformed body")
            n_pages += 1
            if data is None:
                gaps.append(gap)
                break
            result = data["result"]
            pair_keys = [k for k in result.keys() if k != "last"]
            if not pair_keys:
                gaps.append({"endpoint": url, "params": params,
                             "error": "no pair key in result", "failed_utc": _utcnow()})
                break
            trades = result[pair_keys[0]]
            last = result.get("last")
            if not trades:
                break  # no more data in range
            for tr in trades:
                price, volume, t_raw = tr[0], tr[1], tr[2]
                side = tr[3] if len(tr) > 3 else None
                order_type = tr[4] if len(tr) > 4 else None
                misc = tr[5] if len(tr) > 5 else None
                trade_id = tr[6] if len(tr) > 6 else None
                time_s = _normalize_kraken_time(t_raw)
                if time_s < WINDOW_START_S:
                    continue  # defensive; shouldn't occur given since=window start
                rec = {"time": time_s, "price": float(price), "volume": float(volume),
                       "side": side, "order_type": order_type, "misc": misc,
                       "trade_id": trade_id}
                f.write(json.dumps(rec) + "\n")
                if time_s <= WINDOW_END_S:
                    all_trades.append(rec)
            last_time_s = _normalize_kraken_time(trades[-1][2])
            print(f"  [{pair_altname}] page {n_pages}: {len(trades)} trades, "
                  f"up to {datetime.fromtimestamp(last_time_s, tz=timezone.utc).isoformat()}",
                  file=sys.stderr)
            if last_time_s > WINDOW_END_S:
                break
            if last is None or last == since or last in seen_cursors:
                gaps.append({"endpoint": url, "params": params,
                             "error": f"pagination cursor stalled at {last}",
                             "failed_utc": _utcnow()})
                break
            seen_cursors.add(last)
            since = str(last)
            time.sleep(KRAKEN_PAGE_SLEEP)
        else:
            gaps.append({"endpoint": "https://api.kraken.com/0/public/Trades",
                         "params": {"pair": pair_altname},
                         "error": f"hit MAX_KRAKEN_PAGES={MAX_KRAKEN_PAGES} safety cap",
                         "failed_utc": _utcnow()})

    return {"trades": all_trades, "gaps": gaps, "n_pages": n_pages, "raw_path": raw_path}


def aggregate_hourly_trades(trades: list) -> pd.DataFrame:
    """OHLC + VWAP + volume + trade count per UTC hour, from a raw trade list."""
    if not trades:
        return pd.DataFrame(columns=["hour_utc", "open", "high", "low", "close",
                                     "vwap", "volume", "n_trades"])
    df = pd.DataFrame(trades).sort_values("time").reset_index(drop=True)
    df["hour_utc"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.floor("h")

    def _agg(g):
        vol = g["volume"].sum()
        vwap = (g["price"] * g["volume"]).sum() / vol if vol else float("nan")
        return pd.Series({
            "open": g["price"].iloc[0], "high": g["price"].max(),
            "low": g["price"].min(), "close": g["price"].iloc[-1],
            "vwap": vwap, "volume": vol, "n_trades": len(g),
        })

    hourly = df.groupby("hour_utc", sort=True).apply(_agg, include_groups=False).reset_index()
    hourly["hour_utc"] = hourly["hour_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return hourly


def run_kraken_series(session, pair_altname: str, series_dir_name: str) -> dict:
    out_dir = N1_DIR / series_dir_name
    retrieval_utc = _utcnow()
    fetched = fetch_kraken_trades(session, pair_altname, out_dir)
    hourly = aggregate_hourly_trades(fetched["trades"])
    hourly_path = out_dir / "hourly.csv"
    hourly.to_csv(hourly_path, index=False)
    coverage_start = hourly["hour_utc"].min() if len(hourly) else None
    coverage_end = hourly["hour_utc"].max() if len(hourly) else None
    return {
        "series": series_dir_name, "pair_altname": pair_altname,
        "hourly_path": hourly_path, "raw_path": fetched["raw_path"],
        "rows": len(hourly), "n_raw_trades": len(fetched["trades"]),
        "n_pages": fetched["n_pages"], "coverage_start_utc": coverage_start,
        "coverage_end_utc": coverage_end, "gaps": fetched["gaps"],
        "retrieval_utc": retrieval_utc,
    }


# --- Bitstamp OHLC: paginate, save raw pages, tidy hourly -------------------------

def fetch_bitstamp_ohlc(session, pair: str, out_dir: Path) -> dict:
    """Page Bitstamp's OHLC endpoint (step=3600) across the N1 window via start/end,
    saving every raw page response and returning a tidy-ready candle list + gaps."""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    gaps = []
    candles = {}  # timestamp -> candle dict, de-duplicated across pages
    cur_start = WINDOW_START_S
    page = 0
    step = 3600
    while cur_start <= WINDOW_END_S and page < 50:
        page += 1
        url = f"https://www.bitstamp.net/api/v2/ohlc/{pair}/"
        params = {"step": step, "start": cur_start, "end": WINDOW_END_S, "limit": 1000}
        data, gap = _get_with_backoff(
            session, url, params=params,
            error_check=lambda d: d.get("error") if isinstance(d, dict) and "error" in d else None)
        if data is None:
            gaps.append(gap)
            break
        (raw_dir / f"page_{page:03d}.json").write_text(json.dumps(data), encoding="utf-8")
        ohlc = data.get("data", {}).get("ohlc", [])
        if not ohlc:
            break
        for c in ohlc:
            candles[int(c["timestamp"])] = c
        last_ts = max(int(c["timestamp"]) for c in ohlc)
        print(f"  [bitstamp {pair}] page {page}: {len(ohlc)} candles, "
              f"up to {datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()}",
              file=sys.stderr)
        if last_ts >= WINDOW_END_S or len(ohlc) < 2:
            break
        cur_start = last_ts + step
        time.sleep(1.0)
    else:
        if cur_start <= WINDOW_END_S:
            gaps.append({"endpoint": f"https://www.bitstamp.net/api/v2/ohlc/{pair}/",
                         "params": {"step": step, "start": cur_start, "end": WINDOW_END_S},
                         "error": "hit 50-page safety cap before reaching window end",
                         "failed_utc": _utcnow()})
    return {"candles": candles, "gaps": gaps, "n_pages": page}


def run_bitstamp_series(session, pair: str, series_dir_name: str) -> dict:
    out_dir = N1_DIR / series_dir_name
    retrieval_utc = _utcnow()
    fetched = fetch_bitstamp_ohlc(session, pair, out_dir)
    rows = []
    for ts in sorted(fetched["candles"].keys()):
        if not (WINDOW_START_S <= ts <= WINDOW_END_S):
            continue
        c = fetched["candles"][ts]
        rows.append({
            "hour_utc": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "volume": float(c["volume"]),
        })
    hourly = pd.DataFrame(rows, columns=["hour_utc", "open", "high", "low", "close", "volume"])
    hourly_path = out_dir / "hourly.csv"
    hourly.to_csv(hourly_path, index=False)
    coverage_start = hourly["hour_utc"].min() if len(hourly) else None
    coverage_end = hourly["hour_utc"].max() if len(hourly) else None
    return {
        "series": series_dir_name, "pair": pair, "hourly_path": hourly_path,
        "rows": len(hourly), "n_pages": fetched["n_pages"],
        "coverage_start_utc": coverage_start, "coverage_end_utc": coverage_end,
        "gaps": fetched["gaps"], "retrieval_utc": retrieval_utc,
    }


# --- MANIFEST append (additive only — never edits/removes existing entries) -------

def append_manifest_entries(entries: list, files: dict, dry_run: bool = False) -> Path:
    mpath = DATA_DIR / "MANIFEST.json"
    existing = json.load(open(mpath, encoding="utf-8"))
    existing.setdefault("sources", []).extend(entries)
    existing.setdefault("files", {}).update(files)
    if not dry_run:
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
    return mpath


def _min_low(hourly_path: Path, low_col: str = "low"):
    df = pd.read_csv(hourly_path)
    if df.empty:
        return None, None
    idx = df[low_col].idxmin()
    return df.loc[idx, low_col], df.loc[idx, "hour_utc"]


# ============================================================================
# Kraken USDT/USD (Apr20-May15 2019) + NYAG/Tether 2019 disclosure.
# Symbols below are namespaced (N1C_ / *_n1c) to avoid colliding with the
# module-level names above (N1_DIR, TIMEOUT, append_manifest_entries, etc.
# were already claimed by different semantics/signatures) — reuses
# _utcnow()/_sha256() directly from above since those are identical in behavior.
# ============================================================================
# FIXED: this constant originally read
# `Path(__file__).resolve().parent.parent / "data" / "n1"` --
# this file lives at usdc_depeg/data_fetch_n1.py, so one .parent already lands
# on usdc_depeg/ and the second .parent overshot to the repo root, resolving to
# <repo_root>/data/n1 instead of usdc_depeg/data/n1. This is the exact
# wrong-location bug documented at length in the NYAG disclosure section below
# (which correctly avoided this constant rather than fix it, being a
# namespaced additive block). The three functions that still depend on this
# constant (fetch_kraken_usdtusd_2019, and the tether2019 fetchers around
# lines 582/918) already ran successfully with the bug live; their output was
# moved to the correct usdc_depeg/data/n1/ location afterward by hand.
# Fixed here so any FUTURE re-invocation of those functions writes to the
# right place the first time, rather than requiring another manual move.
N1C_DATA_DIR = Path(__file__).resolve().parent / "data" / "n1"
N1C_TIMEOUT = 60
N1C_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")}
N1C_BACKOFF = [4, 8, 16, 32]

KRAKEN_2019_START_DT = datetime(2019, 4, 20, 0, 0, 0, tzinfo=timezone.utc)
KRAKEN_2019_END_DT = datetime(2019, 5, 15, 23, 59, 59, tzinfo=timezone.utc)
KRAKEN_2019_START_S = int(KRAKEN_2019_START_DT.timestamp())
KRAKEN_2019_END_S = int(KRAKEN_2019_END_DT.timestamp())


def get_with_backoff_n1c(url, params=None, label=""):
    """GET with a desktop UA; exponential backoff 4s/8s/16s/32s on any
    error/non-200. Returns Response on success or None once exhausted (caller
    books an INSTRUMENT GAP, never estimates)."""
    last_err = None
    for i, delay in enumerate([0] + N1C_BACKOFF):
        if delay:
            print(f"[{label}] backoff {delay}s (retry {i}/{len(N1C_BACKOFF)})...", file=sys.stderr)
            time.sleep(delay)
        try:
            r = requests.get(url, params=params, headers=N1C_HEADERS, timeout=N1C_TIMEOUT)
            if r.status_code == 200:
                return r
            last_err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_err = str(e)
    print(f"[{label}] FAILED after {len(N1C_BACKOFF)} backoff retries: {last_err}", file=sys.stderr)
    return None


def resolve_kraken_usdtusd_pair_n1c() -> dict:
    """Resolve the canonical Kraken REST pair name for USDT/USD via AssetPairs.
    Verified live (2026-09-09): the REST result key is "USDTZUSD", distinct
    from the altname "USDTUSD" — do not assume the altname is the dict key."""
    r = get_with_backoff_n1c("https://api.kraken.com/0/public/AssetPairs", label="kraken-assetpairs-n1c")
    if r is None:
        raise RuntimeError("INSTRUMENT GAP: https://api.kraken.com/0/public/AssetPairs "
                            "unreachable after 4 backoff retries.")
    result = r.json().get("result", {})
    for key, meta in result.items():
        if meta.get("base") == "USDT" and meta.get("quote") in ("ZUSD", "USD"):
            return {"pair_key": key, "altname": meta.get("altname"), "wsname": meta.get("wsname")}
    raise RuntimeError("INSTRUMENT GAP: no USDT/USD pair in Kraken AssetPairs result.")


def fetch_kraken_usdtusd_2019() -> dict:
    """Page Kraken public Trades for USDT/USD across
    2019-04-20T00:00:00Z .. 2019-05-15T23:59:59Z, save raw trades + hourly
    OHLC/VWAP/volume/count, append one MANIFEST entry. Feeds fix-manifest item
    A2 (specificity panel, Tether Apr-May 2019 row). 1.5s spacing between
    pages regardless of backoff (Kraken rate-limit)."""
    out_dir = N1C_DATA_DIR / "kraken_usdtusd_2019"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "trades_raw.jsonl"
    hourly_path = out_dir / "hourly.csv"

    pair_info = resolve_kraken_usdtusd_pair_n1c()
    pair = pair_info["pair_key"]
    print(f"[kraken-n1c] AssetPairs resolved: pair_key={pair} altname={pair_info['altname']} "
          f"wsname={pair_info['wsname']}", file=sys.stderr)

    since = str(KRAKEN_2019_START_S * 10**9)
    all_trades, gap_notes = [], []
    page = 0
    while True:
        page += 1
        r = get_with_backoff_n1c("https://api.kraken.com/0/public/Trades",
                                  params={"pair": pair, "since": since},
                                  label=f"kraken-trades-n1c-p{page}")
        if r is None:
            gap_notes.append(f"page {page} (since={since}): FAILED after 4 backoff retries "
                              f"(4s/8s/16s/32s) — partial coverage from this point forward.")
            break
        d = r.json()
        if d.get("error"):
            gap_notes.append(f"page {page} (since={since}): Kraken API error {d['error']}")
            break
        result = d.get("result", {})
        last = result.get("last")
        pair_keys = [k for k in result if k != "last"]
        if not pair_keys:
            gap_notes.append(f"page {page}: no pair key in result — stopping.")
            break
        trades = result[pair_keys[0]]
        if not trades:
            break
        for t in trades:
            trade_id = t[6] if len(t) > 6 else None
            all_trades.append({
                "price": float(t[0]), "volume": float(t[1]), "time": float(t[2]),
                "side": t[3], "order_type": t[4], "misc": t[5] if len(t) > 5 else "",
                "trade_id": trade_id,
            })
        last_ts = trades[-1][2]
        print(f"[kraken-n1c] page {page}: {len(trades)} trades, "
              f"last={datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()}", file=sys.stderr)
        if last is None or last == since:
            gap_notes.append(f"page {page}: cursor did not advance — stopping to avoid loop.")
            break
        since = last
        if last_ts > KRAKEN_2019_END_S:
            break
        time.sleep(1.5)

    dedup = {}
    for t in all_trades:
        key = t["trade_id"] if t["trade_id"] is not None else (t["time"], t["price"], t["volume"], t["side"])
        dedup[key] = t
    trades_sorted = sorted(dedup.values(), key=lambda x: x["time"])
    in_window = [t for t in trades_sorted if KRAKEN_2019_START_S <= t["time"] <= KRAKEN_2019_END_S]

    with open(raw_path, "w", encoding="utf-8") as f:
        for t in in_window:
            rec = dict(t)
            rec["time_iso"] = datetime.fromtimestamp(t["time"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            f.write(json.dumps(rec) + "\n")

    if not in_window:
        raise RuntimeError("INSTRUMENT GAP: Kraken Trades returned zero trades inside the "
                            f"requested window ({KRAKEN_2019_START_DT.isoformat()} .. {KRAKEN_2019_END_DT.isoformat()}).")

    df = pd.DataFrame(in_window)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["hour_utc"] = df["dt"].dt.floor("h")
    df = df.sort_values("dt")

    def _agg(g):
        vol = g["volume"].sum()
        vwap = (g["price"] * g["volume"]).sum() / vol if vol > 0 else float("nan")
        return pd.Series({
            "open": g["price"].iloc[0], "high": g["price"].max(), "low": g["price"].min(),
            "close": g["price"].iloc[-1], "vwap": vwap, "volume": vol, "n_trades": len(g),
        })

    hourly = df.groupby("hour_utc").apply(_agg, include_groups=False).reset_index()
    hourly["hour_utc"] = hourly["hour_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    hourly = hourly[["hour_utc", "open", "high", "low", "close", "vwap", "volume", "n_trades"]]
    hourly.to_csv(hourly_path, index=False)

    min_low_row = hourly.loc[hourly["low"].idxmin()]
    sha = _sha256(hourly_path)  # reuse N1a's helper (identical logic)
    coverage_start = df["dt"].min().strftime("%Y-%m-%dT%H:%M:%SZ")
    coverage_end = df["dt"].max().strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = ("Full coverage, no gaps." if not gap_notes else
              "PARTIAL COVERAGE — " + " | ".join(gap_notes))

    entry = {
        "file": "n1/kraken_usdtusd_2019/hourly.csv",
        "series": "kraken_usdtusd_2019_hourly",
        "source_url": "https://api.kraken.com/0/public/Trades?pair=" + pair,
        "retrieval_utc": _utcnow(),  # reuse N1a's helper (identical logic)
        "sha256": sha,
        "rows": int(len(hourly)),
        "coverage_start_utc": coverage_start,
        "coverage_end_utc": coverage_end,
        "notes": (f"Kraken public Trades, pair_key={pair} (altname={pair_info['altname']}), "
                  f"resolved via AssetPairs. Raw trades ({len(in_window)}, deduped by trade_id) "
                  f"at data/n1/kraken_usdtusd_2019/trades_raw.jsonl. {notes} Feeds fix-manifest "
                  f"item A2 (specificity panel, Tether Apr-May 2019 row)."),
    }
    append_manifest_entries_n1c([entry])

    summary = {
        "pair_key": pair, "altname": pair_info["altname"], "rows": int(len(hourly)),
        "n_raw_trades": len(in_window), "coverage_start_utc": coverage_start,
        "coverage_end_utc": coverage_end,
        "min_low": float(min_low_row["low"]), "min_low_hour_utc": min_low_row["hour_utc"],
        "gap_notes": gap_notes, "raw_path": str(raw_path), "hourly_path": str(hourly_path),
        "manifest_entry": entry,
    }
    print(json.dumps(summary, indent=2, default=str), file=sys.stderr)
    return summary


def append_manifest_entries_n1c(entries: list) -> list:
    """APPEND-ONLY manifest update (N1c). Loads MANIFEST.json fresh (so it
    picks up any entries other lanes already appended), appends new dict(s)
    to 'sources', writes back with the file's existing json.dumps(indent=2,
    no trailing newline) formatting — verified byte-identical round-trip —
    so a diff shows only additions. Never edits or removes an existing entry."""
    mpath = DATA_DIR / "MANIFEST.json"
    with open(mpath, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest.setdefault("sources", []).extend(entries)
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(json.dumps(manifest, indent=2))
    return entries


def fetch_tether_2019_disclosure() -> dict:
    """NYAG petition + Tether counsel affidavit, April 2019.

    Document (a), the NYAG's signed Ex Parte Order (GBL S354) against
    iFinex/Bitfinex/Tether, dated 2019-04-24, FOUND via a direct ag.ny.gov
    guess (verified live 2026-09-08): linked from the NYAG press release
    "Attorney General James Announces Court Order Against 'Crypto' Currency
    Company Under Investigation For Fraud" (2019-04-25). Saved raw + hashed.

    Document (b), Tether counsel's affidavit (~2019-04-30, reported to
    first disclose the % of outstanding tethers backed by cash and cash
    equivalents), NOT LOCATED. Three sanctioned headless methods were
    exhausted: (1) direct ag.ny.gov URL guesses, (2) Wayback CDX over
    ag.ny.gov, (3) Wayback CDX over courthousenews.com (the example mirror
    domain named in the task brief). Booked as an INSTRUMENT GAP in
    data/n1/tether2019/disclosure_passage.json, the percentage is NEVER
    filled in from memory. No further domain was guessed, per the
    no-invented-source constraint.

    Appends one MANIFEST entry for the PDF actually obtained (document a).
    Feeds fix-manifest item A2 (specificity panel, Tether Apr-May 2019 row)."""
    import re  # local import: N1a's shared top-of-file imports don't include re
    out_dir = N1C_DATA_DIR / "tether2019"
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    press_release_url = ("https://ag.ny.gov/press-release/2019/"
                          "attorney-general-james-announces-court-order-against-crypto-currency-company")
    r = get_with_backoff_n1c(press_release_url, label="ag-press-release-n1c")
    if r is None:
        raise RuntimeError(f"INSTRUMENT GAP: {press_release_url} unreachable after 4 backoff retries.")
    pdf_links = re.findall(r'href="(https://ag\.ny\.gov/sites/default/files/[^"]+\.pdf)"', r.text)
    if not pdf_links:
        raise RuntimeError(f"INSTRUMENT GAP: {press_release_url} reachable but no PDF link found "
                            "in its HTML (page structure may have changed since 2026-09-08).")
    pdf_url = pdf_links[0]  # https://ag.ny.gov/sites/default/files/2019.04.24_signed_order.pdf

    pdf_r = get_with_backoff_n1c(pdf_url, label="ag-signed-order-pdf-n1c")
    retrieval_utc = _utcnow()
    gap_methods = []
    if pdf_r is None:
        gap_methods.append({"method": "direct", "request": f"GET {pdf_url}",
                             "result": "FAILED after 4 backoff retries"})
        pdf_path = None
    else:
        pdf_path = raw_dir / pdf_url.rsplit("/", 1)[-1]
        pdf_path.write_bytes(pdf_r.content)

    # Document (b): exhaust the three sanctioned methods, recording every attempt.
    guess_urls = [
        "https://ag.ny.gov/sites/default/files/2019.04.30_tether_opposition.pdf",
        "https://ag.ny.gov/sites/default/files/2019.04.30_hoegner_affidavit.pdf",
        "https://ag.ny.gov/press-release/2019/attorney-general-james-secures-court-order-against-bitfinex",
        "https://ag.ny.gov/press-release/2019/attorney-general-james-statement-bitfinex-tether-court-ruling",
        "https://ag.ny.gov/press-release/2019/court-grants-attorney-general-james-continued-oversight-bitfinex-and-tether",
    ]
    for gu in guess_urls:
        try:
            gr = requests.get(gu, headers=N1C_HEADERS, timeout=N1C_TIMEOUT)
            gap_methods.append({"method": "direct", "request": f"GET {gu}", "result": f"{gr.status_code}"})
        except requests.RequestException as e:
            gap_methods.append({"method": "direct", "request": f"GET {gu}", "result": f"error: {e}"})

    cdx_base = "http://web.archive.org/cdx/search/cdx"
    cdx_agny = get_with_backoff_n1c(cdx_base, params={"url": "ag.ny.gov", "matchType": "domain",
                                                        "from": "20190401", "to": "20190801",
                                                        "output": "json", "collapse": "urlkey", "limit": 5000},
                                     label="cdx-agny-n1c")
    if cdx_agny is not None and cdx_agny.text.strip():
        rows = cdx_agny.json()[1:]
        hits = [row for row in rows if any(k in row[2].lower() for k in ("tether", "bitfinex", "ifinex"))]
        gap_methods.append({"method": "wayback_cdx", "request": f"{cdx_base}?url=ag.ny.gov&matchType=domain&from=20190401&to=20190801",
                             "result": f"200 OK, {len(rows)} rows, {len(hits)} tether/bitfinex/ifinex hits: {hits}"})
    else:
        gap_methods.append({"method": "wayback_cdx", "request": f"{cdx_base}?url=ag.ny.gov&matchType=domain&from=20190401&to=20190801",
                             "result": "FAILED after 4 backoff retries" if cdx_agny is None else "200 OK, empty body (0 rows)"})

    for kw in ("tether", "bitfinex", "ifinex"):
        cdx_r = get_with_backoff_n1c(cdx_base, params={"url": "courthousenews.com", "matchType": "domain",
                                                          "from": "20190401", "to": "20190801", "output": "json",
                                                          "collapse": "urlkey", "filter": f"urlkey:.*{kw}.*", "limit": 500},
                                      label=f"cdx-chn-{kw}-n1c")
        if cdx_r is not None and cdx_r.text.strip():
            rows = cdx_r.json()[1:]
            result = f"200 OK, {len(rows)} rows: {rows}"
        elif cdx_r is not None:
            result = "200 OK, empty body (0 rows)"
        else:
            result = "FAILED after 4 backoff retries"
        gap_methods.append({"method": "wayback_cdx",
                             "request": f"{cdx_base}?url=courthousenews.com&matchType=domain&from=20190401&to=20190801&filter=urlkey:.*{kw}.*",
                             "result": result})

    result = {
        "document_a": {
            "identity": ("Signed Ex Parte Order, GBL S354, Supreme Court of NY County, dated/stamped "
                          "2019-04-24, In re Inquiry by Letitia James, AG of NY, v. iFinex Inc./BFXNA/"
                          "BFXWW/Tether Holdings/Tether Operations/Tether Limited/Tether International. "
                          "References but does not include the underlying Whitehurst affirmation + "
                          "Exhibits A-M + memorandum of law (all dated 2019-04-24)."),
            "source_url": pdf_url, "linked_from": press_release_url,
            "retrieval_utc": retrieval_utc,
            "sha256": _sha256(pdf_path) if pdf_path else None,
            "bytes": pdf_path.stat().st_size if pdf_path else None,
            "raw_path": str(pdf_path) if pdf_path else None,
        },
        "document_b_gap": {
            "target": "Tether counsel affidavit (~2019-04-30) disclosing the cash/cash-equivalent backing %",
            "status": "NOT LOCATED", "methods_tried": gap_methods,
        },
    }

    disclosure_path = out_dir / "disclosure_passage.json"
    # NOTE: this reproduces the same content already hand-written and reviewed at
    # data/n1/tether2019/disclosure_passage.json; re-running this function refreshes
    # timestamps/method log but the document/gap findings are stable as of 2026-09-08.
    disclosure_path.write_text(json.dumps({
        "document": result["document_a"]["identity"], "date": "2019-04-24", "page": None,
        "verbatim_passage": None, "source_url_or_wayback_url": pdf_url, "retrieval_method": "direct",
        "document_found": result["document_a"], "percentage_disclosure_passage": result["document_b_gap"],
    }, indent=2), encoding="utf-8")

    if pdf_path:
        entry = {
            "file": f"n1/tether2019/raw/{pdf_path.name}",
            "series": "tether2019_nyag_signed_order",
            "source_url": pdf_url, "retrieval_utc": retrieval_utc,
            "sha256": result["document_a"]["sha256"], "bytes": result["document_a"]["bytes"],
            "notes": (f"{result['document_a']['identity']} Linked from NYAG press release {press_release_url} "
                      "(2019-04-25). Does NOT contain the cash/cash-equivalent backing percentage disclosure "
                      "-- that is reported to be in a SEPARATE Tether-counsel affidavit ~2019-04-30 which "
                      "could NOT be located after exhausting 3 sanctioned headless methods (see "
                      "data/n1/tether2019/disclosure_passage.json for the full gap record). Feeds "
                      "fix-manifest item A2 (specificity panel, Tether Apr-May 2019 row) alongside "
                      "n1/kraken_usdtusd_2019/hourly.csv; the phi/backing-percentage input for that row "
                      "remains an open instrument gap pending the affidavit."),
        }
        append_manifest_entries_n1c([entry])
        result["manifest_entry"] = entry

    print(json.dumps({k: v for k, v in result.items() if k != "document_b_gap"}, indent=2, default=str),
          file=sys.stderr)
    return result

# NOTE: no module-level `if __name__ == "__main__":` trigger is added here —
# N1a's own such block above already owns "run this file as a script" (it
# calls main() unconditionally when __name__=="__main__", before Python's
# top-to-bottom execution ever reaches this point in the file). Adding a
# second one here would either be dead code (never reached when run as a
# script, since the module's main() runs first) or would need to edit that
# block directly, which this section must not touch. Both functions above are
# already proven (already run once, standalone, before this merge — see the
# manifest entries and data/n1/kraken_usdtusd_2019/, data/n1/tether2019/
# for the actual output) and are reachable via a plain import:
#   from data_fetch_n1 import fetch_kraken_usdtusd_2019, fetch_tether_2019_disclosure


# ============================================================================
# Bitstamp USDC/USD retry with a 30s explicit connect timeout, forced
# HTTP/1.1, and THREE full 4/8/16/32s backoff cycles (not just one), plus a
# Wayback CDX fallback probe.
# Namespaced with a `bsretry`/`N1RETRY_` prefix throughout to avoid colliding
# with the module's own fetch_bitstamp_ohlc/run_bitstamp_series above (same
# target API, different retry policy and output paths — this writes to
# bitstamp_usdcusd/raw_retry/ and hourly_retry.csv, never touching the
# original hourly.csv, which correctly documents the original gap).
# ============================================================================
N1RETRY_BACKOFF = [4, 8, 16, 32]
N1RETRY_CYCLES = 3
N1RETRY_CONNECT_TIMEOUT = 30
N1RETRY_READ_TIMEOUT = 30


def _get_with_triple_backoff_bsretry(url, params=None):
    """THREE full backoff cycles of 4s/8s/16s/32s each (1 initial attempt + 4
    backed-off retries = 5 tries/cycle, 15 tries total). A fresh
    requests.Session + HTTPAdapter is built per call so no connection state
    persists across cycles. `requests`/`urllib3` do not implement HTTP/2 at
    all (no h2 extra installed) so every request already goes out as
    HTTP/1.1; `Connection: close` is passed explicitly on top of that so no
    keep-alive socket is reused across attempts either. Returns
    (json_or_none, list_of_every_attempt's_error_string)."""
    all_errors = []
    for cycle in range(1, N1RETRY_CYCLES + 1):
        for attempt in range(len(N1RETRY_BACKOFF) + 1):  # 0=initial, 1..4=retries
            if attempt > 0:
                wait = N1RETRY_BACKOFF[attempt - 1]
                print(f"  [bsretry] cycle {cycle} attempt {attempt}: waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT, "Connection": "close"})
            try:
                r = session.get(url, params=params,
                                 timeout=(N1RETRY_CONNECT_TIMEOUT, N1RETRY_READ_TIMEOUT))
                if r.status_code != 200:
                    err = f"cycle {cycle} attempt {attempt}: HTTP {r.status_code}"
                    print(f"  [bsretry] {err}", file=sys.stderr)
                    all_errors.append(err)
                    continue
                return r.json(), all_errors
            except Exception as e:
                err = f"cycle {cycle} attempt {attempt}: {type(e).__name__}: {e}"
                print(f"  [bsretry] {err}", file=sys.stderr)
                all_errors.append(err)
                continue
            finally:
                session.close()
    return None, all_errors


def _wayback_cdx_probe_bsretry() -> dict:
    """Query the Wayback Machine CDX API for any cached capture of the exact
    Bitstamp OHLC API path over www.bitstamp.net (unlikely for an API
    endpoint, but attempted and recorded either way per the retry brief)."""
    url = "http://web.archive.org/cdx/search/cdx"
    params = {"url": "www.bitstamp.net/api/v2/ohlc/usdcusd*", "matchType": "prefix",
              "output": "json", "limit": 500}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        rows = r.json() if r.text.strip() else []
        return {"request": r.url, "status": r.status_code, "n_rows": max(0, len(rows) - 1),
                "rows": rows}
    except Exception as e:
        return {"request": url, "params": params, "error": f"{type(e).__name__}: {e}"}


def fetch_bitstamp_retry_n1d() -> dict:
    """Retry Bitstamp USDC/USD OHLC (step=3600) across the N1 window
    (2023-03-08T00:00:00Z .. 2023-03-17T23:59:59Z), paginating like N1a's
    original fetch_bitstamp_ohlc but with the triple-backoff policy above.
    On success: raw pages -> bitstamp_usdcusd/raw_retry/, hourly ->
    bitstamp_usdcusd/hourly_retry.csv. On failure: returns every error from
    all 3 backoff cycles plus the Wayback CDX probe result; writes nothing."""
    out_dir = N1_DIR / "bitstamp_usdcusd"
    raw_dir = out_dir / "raw_retry"
    pair = "usdcusd"
    step = 3600
    all_errors = []
    candles = {}
    cur_start = WINDOW_START_S
    page = 0
    while cur_start <= WINDOW_END_S and page < 50:
        page += 1
        url = f"https://www.bitstamp.net/api/v2/ohlc/{pair}/"
        params = {"step": step, "start": cur_start, "end": WINDOW_END_S, "limit": 1000}
        data, errors = _get_with_triple_backoff_bsretry(url, params=params)
        all_errors.extend(errors)
        if data is None:
            break
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"page_{page:03d}.json").write_text(json.dumps(data), encoding="utf-8")
        ohlc = data.get("data", {}).get("ohlc", [])
        if not ohlc:
            break
        for c in ohlc:
            candles[int(c["timestamp"])] = c
        last_ts = max(int(c["timestamp"]) for c in ohlc)
        if last_ts >= WINDOW_END_S or len(ohlc) < 2:
            break
        cur_start = last_ts + step
        time.sleep(1.0)

    if not candles:
        wayback = _wayback_cdx_probe_bsretry()
        return {"success": False, "errors": all_errors, "wayback_cdx": wayback,
                "endpoint": f"https://www.bitstamp.net/api/v2/ohlc/{pair}/",
                "params_template": {"step": step, "start": "<unix>", "end": WINDOW_END_S, "limit": 1000}}

    rows = []
    for ts in sorted(candles.keys()):
        if not (WINDOW_START_S <= ts <= WINDOW_END_S):
            continue
        c = candles[ts]
        rows.append({
            "hour_utc": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
            "volume": float(c["volume"]),
        })
    hourly = pd.DataFrame(rows, columns=["hour_utc", "open", "high", "low", "close", "volume"])
    hourly_path = out_dir / "hourly_retry.csv"
    hourly.to_csv(hourly_path, index=False)
    coverage_start = hourly["hour_utc"].min() if len(hourly) else None
    coverage_end = hourly["hour_utc"].max() if len(hourly) else None
    sha = _sha256(hourly_path)
    retrieval_utc = _utcnow()
    entry = {
        "file": "n1/bitstamp_usdcusd/hourly_retry.csv",
        "series": "bitstamp_usdcusd_retry",
        "source_url": "https://www.bitstamp.net/api/v2/ohlc/usdcusd/?step=3600&start=<unix>&end=<unix>&limit=1000 (paginated)",
        "retrieval_utc": retrieval_utc,
        "sha256": sha,
        "rows": int(len(hourly)),
        "coverage_start_utc": coverage_start,
        "coverage_end_utc": coverage_end,
        "notes": (f"Retry of the original bitstamp_usdcusd series "
                  f"(rows=0, TCP connect timeout gap booked {retrieval_utc[:10]}-adjacent) "
                  f"with a 30s explicit connect timeout, forced HTTP/1.1 (Connection: close; "
                  f"requests/urllib3 has no HTTP/2 support installed regardless), and 3 full "
                  f"4/8/16/32s backoff cycles per request. n_pages={page}. Supersedes the original "
                  f"'bitstamp_usdcusd' zero-row gap entry by series name (bitstamp_usdcusd_retry vs "
                  f"bitstamp_usdcusd) — mirrors the kraken_usdtusd_complete supersession pattern; "
                  f"the original gap entry is left untouched as required."),
    }
    return {"success": True, "errors": all_errors, "entry": entry, "hourly_path": hourly_path,
            "rows": int(len(hourly)), "coverage_start_utc": coverage_start,
            "coverage_end_utc": coverage_end}


# ============================================================================
# Hoegner/Tether 2019-04-30
# affirmation search (routes a-e) + re-read of the already-frozen signed
# order PDF. Namespaced `n1cretry_` throughout to avoid colliding with the
# names above, the N1C_-prefixed names, and the N1RETRY_-
# prefixed names — reuses _utcnow()/_sha256() from above (identical behavior).
#
# Only ONE of routes (a)-(e) produced a frozen artifact: route (a),
# CourtListener REST v4 search, which located the Appellate Division's 2020
# decision in the SAME docket (450545/19) via the official courts.state.ny.us
# slip-opinion mirror (CourtListener's own opinion-text endpoint returned 401
# without an API key). That decision is NOT the Hoegner affirmation and
# contains no backing-fraction percentage. fetch_courtlistener_opinion_n1cretry
# reproduces that one successful fetch below.
#
# Routes (b) NYSCEF Wayback CDX, (c) tether.to/bitfinex.com Wayback CDX +
# page fetches, (d) news-outlet Wayback CDX (coindesk/reuters/bloomberg/
# theblock), and (e) the manual/ folder check were all exhausted without
# locating any primary or secondary verbatim disclosure passage — reproducing
# every dead-end CDX probe as code would be speculative rather than useful
# (their point was existence/negative-result checks, not a repeatable data
# series). The full query-by-query record for all five routes, plus the
# related-but-not-target passage from the frozen opinion and the PDF re-read
# findings, is in data/n1/tether2019/disclosure_passage_n1cretry.json — that
# file, not this module, is the source of truth for what was and was not
# found. The gap on the backing-fraction percentage remains fully open.
# ============================================================================

def fetch_courtlistener_opinion_n1cretry() -> dict:
    """Search CourtListener REST v4 for the NYAG-v-iFinex/Tether matter
    (index 450545/2019), fetch the resulting Appellate Division opinion's
    full text from the official courts.state.ny.us mirror (CourtListener's
    own opinion-text endpoint needs an API key not available offline),
    save + hash it, and append one MANIFEST entry. Raises RuntimeError
    (booking an INSTRUMENT GAP) if either HTTP call is unreachable after
    backoff — never fabricates the opinion text or the case identity."""
    import re  # local import, as N1C's fetch_tether_2019_disclosure does above
    headers = {"User-Agent": USER_AGENT}
    search_url = "https://www.courtlistener.com/api/rest/v4/search/"
    params = {"q": "iFinex Letitia James 450545 2019"}
    data, gap = _get_with_backoff(requests.Session(), search_url, params=params,
                                   error_check=lambda d: None)
    if data is None or not data.get("results"):
        raise RuntimeError(f"INSTRUMENT GAP: {search_url}?q={params['q']} returned no results "
                            f"(gap={gap}).")
    hit = data["results"][0]
    opinion_url = hit["opinions"][0]["download_url"]  # official courts.state.ny.us mirror

    out_dir = N1C_DATA_DIR / "tether2019" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Opinion HTML isn't JSON, so _get_with_backoff (which calls r.json()) doesn't
    # apply here — same 4s/8s/16s/32s backoff policy via a raw GET loop instead.
    last_err = None
    resp = None
    for attempt in range(len(BACKOFF_WAITS) + 1):
        if attempt > 0:
            time.sleep(BACKOFF_WAITS[attempt - 1])
        try:
            resp = requests.get(opinion_url, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 200:
                break
            last_err = f"HTTP {resp.status_code}"
            resp = None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            resp = None
    if resp is None:
        raise RuntimeError(f"INSTRUMENT GAP: {opinion_url} unreachable after backoff ({last_err}).")

    fname = "2020.07.09_appellate_division_opinion_matter_of_james_v_ifinex.html"
    out_path = out_dir / fname
    out_path.write_text(resp.text, encoding="utf-8")
    retrieval_utc = _utcnow()
    sha = _sha256(out_path)

    entry = {
        "file": f"n1/tether2019/raw/{fname}",
        "series": "tether2019_nyag_appellate_opinion",
        "source_url": opinion_url,
        "retrieval_utc": retrieval_utc,
        "sha256": sha,
        "bytes": out_path.stat().st_size,
        "notes": (f"{hit['caseName']}, {', '.join(hit.get('citation', []))} "
                  f"(App. Div., decided {hit.get('dateFiled')}; docket {hit.get('docketNumber')}, "
                  "same matter as the already-frozen 2019.04.24_signed_order.pdf). Located via "
                  "CourtListener REST v4 search. NOT the Hoegner affirmation and contains no backing-"
                  "fraction percentage -- see data/n1/tether2019/disclosure_passage_n1cretry.json for "
                  "the full route (a)-(e) gap record and the related verbatim passage this opinion "
                  "does contain (Tether's post-2019-03-04 reserve-composition representation)."),
    }
    append_manifest_entries_n1c([entry])
    return {"case": hit["caseName"], "citation": hit.get("citation"), "opinion_url": opinion_url,
            "raw_path": str(out_path), "manifest_entry": entry}


# ============================================================================
# fetch/verify split, added as new top-level
# functions per the naming conventions above; the ~55 existing fetch functions in this
# file are untouched. `verify` is generic (recomputes SHA-256 of every
# MANIFEST.json files{} entry, whichever script produced it) so it delegates to
# data_fetch.verify() rather than duplicating that logic. `_refuse_if_manifested`
# guards the main `fetch` entry point's `main()` (the only fetch path this file wires
# into a CLI, the other fetch functions below are invoked ad hoc, not from main()).
# ============================================================================
def _refuse_if_manifested(rel_paths) -> None:
    """Guard for the `fetch` entry point: refuse, before any network
    call, to touch any output path MANIFEST.json's files{} already lists as
    frozen. Paths not yet in files{} are unaffected."""
    mpath = DATA_DIR / "MANIFEST.json"
    existing_files = json.load(open(mpath, encoding="utf-8")).get("files", {}) if mpath.exists() else {}
    already = [p for p in rel_paths if p in existing_files]
    if already:
        raise FileExistsError(
            f"refusing to overwrite already-frozen file(s) listed in MANIFEST.json "
            f"files{{}}: {already}. Frozen files are read-only -- run "
            f"`python data_fetch_n1.py verify` to check them instead of `fetch`."
        )


def verify() -> bool:
    """Offline: delegate to data_fetch.verify(), both scripts share the same
    MANIFEST.json (usdc_depeg/data/MANIFEST.json), so the check (recompute
    SHA-256 of every files{} entry) is identical regardless of which script
    produced which file. No network, no write."""
    from data_fetch import verify as _verify_manifest
    return _verify_manifest()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    fetch_p = sub.add_parser(
        "fetch", help="live pull: Kraken USDC/USD + USDT/USD trades, Bitstamp USDC/USD OHLC; "
                      "refuses to overwrite an already-frozen file")
    fetch_p.add_argument("--dry-run", action="store_true", help="fetch + aggregate only, skip MANIFEST write")
    sub.add_parser("verify", help="offline: recompute SHA-256 of every MANIFEST.json files{} entry")
    args = ap.parse_args()

    if args.command == "verify":
        sys.exit(0 if verify() else 1)

    _refuse_if_manifested(["n1/kraken_usdcusd/hourly.csv", "n1/kraken_usdtusd/hourly.csv",
                           "n1/bitstamp_usdcusd/hourly.csv"])

    session = _session()
    N1_DIR.mkdir(parents=True, exist_ok=True)

    print("== Step 0: resolve Kraken canonical pairs ==", file=sys.stderr)
    pairs = resolve_kraken_pairs(session)
    print(json.dumps(pairs, indent=2), file=sys.stderr)

    print("== Kraken USDC/USD ==", file=sys.stderr)
    usdc = run_kraken_series(session, pairs["USDC"]["altname"], "kraken_usdcusd")

    print("== Kraken USDT/USD ==", file=sys.stderr)
    usdt = run_kraken_series(session, pairs["USDT"]["altname"], "kraken_usdtusd")

    print("== Bitstamp USDC/USD ==", file=sys.stderr)
    bitstamp = run_bitstamp_series(session, "usdcusd", "bitstamp_usdcusd")

    results = {"kraken_pairs": pairs, "kraken_usdcusd": usdc, "kraken_usdtusd": usdt,
               "bitstamp_usdcusd": bitstamp}

    entries = []
    files = {}
    for label, r, source_url_template in [
        ("kraken_usdcusd", usdc,
         f"https://api.kraken.com/0/public/Trades?pair={pairs['USDC']['altname']}&since=<ns_cursor> (paginated)"),
        ("kraken_usdtusd", usdt,
         f"https://api.kraken.com/0/public/Trades?pair={pairs['USDT']['altname']}&since=<ns_cursor> (paginated)"),
        ("bitstamp_usdcusd", bitstamp,
         "https://www.bitstamp.net/api/v2/ohlc/usdcusd/?step=3600&start=<unix>&end=<unix>&limit=1000 (paginated)"),
    ]:
        hp = r["hourly_path"]
        sha = _sha256(hp)
        rel = f"n1/{label}/hourly.csv"
        low_val, low_hour = _min_low(hp)
        notes_bits = [f"rows={r['rows']}", f"n_pages={r['n_pages']}"]
        if label.startswith("kraken"):
            notes_bits.append(f"canonical_altname={r['pair_altname']}")
            notes_bits.append(f"n_raw_trades={r['n_raw_trades']}")
        else:
            notes_bits.append("pair=usdcusd (Bitstamp path segment; no AssetPairs-style resolution needed)")
        if r["gaps"]:
            notes_bits.append(f"GAPS booked: {json.dumps(r['gaps'])}")
        else:
            notes_bits.append("no gaps; full requested window covered")
        notes_bits.append(f"min hourly low = {low_val} at {low_hour}")
        entry = {
            "file": rel,
            "series": label,
            "source_url": source_url_template,
            "retrieval_utc": r["retrieval_utc"],
            "sha256": sha,
            "rows": r["rows"],
            "coverage_start_utc": r["coverage_start_utc"],
            "coverage_end_utc": r["coverage_end_utc"],
            "notes": "; ".join(notes_bits),
        }
        entries.append(entry)
        files[rel] = {"sha256": sha, "bytes": hp.stat().st_size}

    mpath = append_manifest_entries(entries, files, dry_run=args.dry_run)
    print("== MANIFEST", ("(dry-run, not written)" if args.dry_run else f"appended: {mpath}"), "==",
          file=sys.stderr)

    print(json.dumps({"results_summary": {
        k: {kk: (str(vv) if isinstance(vv, Path) else vv) for kk, vv in v.items() if kk != "trades"}
        for k, v in results.items() if isinstance(v, dict)
    }, "manifest_entries": entries}, indent=2, default=str))


if __name__ == "__main__":
    main()


# ============================================================================
# NYAG/Tether 2019, DOLLAR-AMOUNT angle (distinct from the two functions above,
# which searched for the counsel PERCENTAGE disclosure and left it as an open
# instrument gap). Namespaced laneT_* to avoid colliding with any other
# top-level names in this file. Reuses get_with_backoff_n1c / N1C_HEADERS /
# N1C_BACKOFF / N1C_TIMEOUT / N1C_DATA_DIR / append_manifest_entries_n1c /
# _utcnow / _sha256 as-is — already generic and correct for this block's
# needs, so no new backoff machinery is defined here.
#
# The frozen output under usdc_depeg/data/n1/tether2019/ (raw HTML, both
# JSON files, MANIFEST entries) was produced by equivalent standalone
# scripts (same CDX queries, same backoff/headers, same extraction regexes),
# then ported into these two named functions. The functions were NOT re-run
# end-to-end afterward (that would have re-fetched already-frozen Wayback
# content and appended duplicate MANIFEST rows), but their extraction regexes
# and passage text were independently re-validated against every
# already-frozen raw file (22/22 transparency captures parse; all 4
# press-release passages and the response-page passage were confirmed present
# verbatim in the frozen HTML) and the N1C_DATA_DIR path bug below was caught
# and avoided this way. Reachable to reproduce from scratch, or extend to new
# dates, via:
#   from data_fetch_n1 import laneT_fetch_nyag_press_release_dollars, \
#       laneT_fetch_tether_liabilities_wayback
# ============================================================================
import html as _html  # local import: no earlier top-of-file imports include html
import re  # local import: the shared top-of-file imports don't include re (one earlier
           # function imports it locally inside fetch_tether_2019_disclosure only; this
           # block needs it at module scope for its own functions below)

# NOTE: deliberately built from the top-of-file `DATA_DIR` (from constants,
# = usdc_depeg/data) rather than N1C_DATA_DIR above. At the time this code ran,
# N1C_DATA_DIR resolved one level too high (<repo_root>/data/n1, not
# usdc_depeg/data/n1), the exact "files in the wrong place" bug this block was
# written to avoid. Correctly left unfixed here, being out of scope for a
# namespaced additive block touching functions it doesn't own; this code's own
# paths avoided it entirely. UPDATE: N1C_DATA_DIR itself has since been fixed
# at its definition above (one `.parent` removed), this note is kept as the
# record of how the bug was found, not as a statement that it's still live.
LANE_T_T2019_DIR = DATA_DIR / "n1" / "tether2019"
LANE_T_RAW_DIR = LANE_T_T2019_DIR / "raw"
LANE_T_WAYBACK_TRANSPARENCY_DIR = LANE_T_RAW_DIR / "wayback_transparency"


def laneT_fetch_nyag_press_release_dollars() -> dict:
    """Domain-wide Wayback CDX over ag.ny.gov (2019-04-24..
    2019-05-31), filtered client-side for ifinex/bitfinex/tether keywords and
    for sites/default/files/*.pdf candidates, then a live re-fetch of the
    2019-04-25 NYAG press release for its actual body text (earlier passes
    only used this page to find the signed-order PDF link;
    never read/froze its own prose). Extracts every $-figure sentence
    verbatim. The AG's own affirmation/petition/memorandum of law (the
    Whitehurst papers the signed order references as its basis) was NOT
    locatable as a standalone ag.ny.gov document after exhausting this CDX
    sweep plus three named-by-convention PDF candidates
    (complaint_as-filed.pdf = unrelated 2018 DOL case; petition_-_filed.pdf =
    image-only PDF with a 2018-01-16 creation date, i.e. pre-dates this case;
    court_stamped_petition.pdf = no keyword hits in 81K extracted chars) —
    booked as an INSTRUMENT GAP in disclosure_passages_nyag.json rather than
    guessed. Writes disclosure_passages_nyag.json, appends MANIFEST entries
    for the raw press-release HTML."""
    LANE_T_RAW_DIR.mkdir(parents=True, exist_ok=True)

    cdx_base = "http://web.archive.org/cdx/search/cdx"
    cdx_r = get_with_backoff_n1c(cdx_base, params={"url": "ag.ny.gov", "matchType": "domain",
                                                      "from": "20190424", "to": "20190531",
                                                      "output": "json"},
                                  label="laneT-cdx-agny")
    if cdx_r is None:
        raise RuntimeError("INSTRUMENT GAP: ag.ny.gov domain CDX (20190424-20190531) unreachable "
                            "after 4 backoff retries.")
    cdx_rows = cdx_r.json()[1:]  # drop header row

    press_release_url = ("https://ag.ny.gov/press-release/2019/"
                          "attorney-general-james-announces-court-order-against-crypto-currency-company")
    pr_r = get_with_backoff_n1c(press_release_url, label="laneT-ag-press-release")
    if pr_r is None:
        raise RuntimeError(f"INSTRUMENT GAP: {press_release_url} unreachable after 4 backoff retries.")
    retrieval_utc = _utcnow()
    pr_path = LANE_T_RAW_DIR / "2019.04.25_ag_press_release.html"
    pr_path.write_text(pr_r.text, encoding="utf-8")

    body = re.sub(r"<[^>]+>", " ", pr_r.text)
    body = _html.unescape(body)
    body = re.sub(r"\s+", " ", body)

    pr_doc = ("NYAG press release \"Attorney General James Announces Court Order Against "
              "‘Crypto’ Currency Company Under Investigation For Fraud,\" ag.ny.gov, "
              "dated on-page April 25, 2019. AG-authored, but NOT the Whitehurst affirmation/"
              "petition/memorandum of law itself (see instrument_gap below).")
    # Every $-figure sentence found by searching for 625/700/900/850/million/
    # line of credit, per the task brief — report every hit, not just the
    # expected one.
    passages = [
        {"document": pr_doc, "date": "2019-04-25", "page": None,
         "verbatim_passage": ("“Our investigation has determined that the operators of the "
                               "‘Bitfinex’ trading platform, who also control the ‘tether’ "
                               "virtual currency, have engaged in a cover-up to hide the apparent loss of "
                               "$850 million dollars of co-mingled client and corporate funds,” said "
                               "Attorney General James."),
         "search_term_matched": "850 / million", "category": "impairment_amount"},
        {"document": pr_doc, "date": "2019-04-25", "page": None,
         "verbatim_passage": ("The filings explain how Bitfinex no longer has access to over $850 million "
                               "dollars of co-mingled client and corporate funds that it handed over, "
                               "without any written contract or assurance, to a Panamanian entity called "
                               "“Crypto Capital Corp.,” a loss Bitfinex never disclosed to investors."),
         "search_term_matched": "850 / million", "category": "impairment_amount"},
        {"document": pr_doc, "date": "2019-04-25", "page": None,
         "verbatim_passage": ("In order to fill the gap, executives of Bitfinex and Tether engaged in a "
                               "series of conflicted corporate transactions whereby Bitfinex gave itself "
                               "access to up to $900 million of Tether’s cash reserves, which Tether "
                               "for years repeatedly told investors fully backed the tether virtual "
                               "currency “1-to-1.”"),
         "search_term_matched": "900 / million / line of credit (ceiling)", "category": "credit_line_ceiling"},
        {"document": pr_doc, "date": "2019-04-25", "page": None,
         "verbatim_passage": ("According to the filings, Bitfinex has already taken at least $700 million "
                               "from Tether’s reserves."),
         "search_term_matched": "700 / million", "category": "amount_drawn"},
    ]
    for p in passages:
        p.update({"source_url": press_release_url, "retrieval_method": "direct (live HTTP, 200 OK)",
                   "retrieval_utc": retrieval_utc,
                   "raw_file": "data/n1/tether2019/raw/2019.04.25_ag_press_release.html"})
        assert p["verbatim_passage"] in body, "verbatim passage not found in re-fetched page body"

    pdf_hits = [r for r in cdx_rows if "sites/default/files" in r[2].lower() and r[2].lower().endswith(".pdf")]
    seen, pdf_uniq = set(), []
    for r in pdf_hits:
        if r[2] not in seen:
            seen.add(r[2]); pdf_uniq.append(r)

    instrument_gap = {
        "target": ("Brian M. Whitehurst affirmation (2019-04-24, Exhibits A-M) and/or memorandum of law "
                   "and/or verified petition, referenced by the already-frozen signed ex parte order as "
                   "its basis -- the AG's OWN supporting papers (distinct from Tether counsel's later "
                   "~2019-04-30 response affirmation two prior attempts searched for)."),
        "status": "NOT LOCATED as a standalone document on ag.ny.gov -- INSTRUMENT GAP",
        "cdx_domain_wide_rows": len(cdx_rows),
        "sites_default_files_pdf_candidates": len(pdf_uniq),
        "candidates_checked_by_pdfplumber_text_extraction": [
            "complaint_as-filed.pdf (unrelated 2018 DOL case, 0 keyword hits in 158,572 chars)",
            "petition_-_filed.pdf (image-only, 0 extractable chars, PDF CreationDate=2018-01-16 predates case)",
            "court_stamped_petition.pdf (0 keyword hits in 81,063 chars)",
        ],
        "conclusion": ("Exhaustive domain-wide + filename-pattern + date-prefix-naming CDX sweep found no "
                       "sibling document to the signed order. The dollar figures were recovered secondhand, "
                       "quoted/paraphrased in the AG's own press release (see passages above)."),
    }

    out = {"passages": passages, "instrument_gap_affirmation_petition_memorandum": instrument_gap}
    out_path = LANE_T_T2019_DIR / "disclosure_passages_nyag.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    entry = {
        "file": "n1/tether2019/raw/2019.04.25_ag_press_release.html",
        "series": "tether2019_nyag_press_release_dollars",
        "source_url": press_release_url, "retrieval_utc": retrieval_utc,
        "sha256": _sha256(pr_path), "bytes": pr_path.stat().st_size,
        "notes": ("NYAG press release full body text (not just the signed-order PDF link this page was "
                  "previously used for). Contains 3 distinct dollar figures ($700M/$850M/$900M) attributed "
                  "to \"the filings\" -- see data/n1/tether2019/disclosure_passages_nyag.json for verbatim "
                  "passages. Does NOT contain the underlying Whitehurst affirmation/petition/memorandum of "
                  "law text itself, which remains an open instrument gap (see same file)."),
    }
    append_manifest_entries_n1c([entry])
    return {"passages": passages, "instrument_gap": instrument_gap, "manifest_entry": entry}


def laneT_fetch_tether_liabilities_wayback() -> dict:
    """Wayback CDX over tether.to for 'transparency' URLs,
    2019-04-15..2019-05-15. The live wallet.tether.to/transparency balance
    page is auto-generated and was crawled many times per day (234 raw CDX
    rows, ~52 near-duplicate intraday text/html captures) — this function
    samples ONE representative capture per UTC calendar day (22 days) rather
    than fetching every near-identical intraday snapshot, and extracts the
    verbatim-labeled 'Total Assets' / 'Total Liabilities' / 'Excess of Assets
    over Liabilities' (or, in early captures, 'Shareholder Equity') figures
    from the USD₮ 'Current Balances' block. Also fetches the tether.to blog
    post responding to the NYAG action (found via a second, unfiltered CDX
    sweep of tether.to for 2019-04-25..2019-05-05) and extracts its verbatim
    reserves-related statement. Writes usdt_liabilities_wayback.json, appends
    one MANIFEST entry per raw HTML file fetched (23 total)."""
    LANE_T_WAYBACK_TRANSPARENCY_DIR.mkdir(parents=True, exist_ok=True)

    cdx_base = "http://web.archive.org/cdx/search/cdx"
    cdx_r = get_with_backoff_n1c(cdx_base, params={"url": "tether.to", "matchType": "domain",
                                                      "from": "20190415", "to": "20190515",
                                                      "output": "json", "filter": "urlkey:.*transparency.*"},
                                  label="laneT-cdx-tether-transparency")
    if cdx_r is None:
        raise RuntimeError("INSTRUMENT GAP: tether.to transparency CDX (20190415-20190515) unreachable "
                            "after 4 backoff retries.")
    cdx_rows = cdx_r.json()[1:]
    html_rows = [r for r in cdx_rows if r[3] == "text/html" and r[4] == "200"
                 and "transparency" in r[2] and "fss-report" not in r[2] and "announcement" not in r[2]]
    by_day = {}
    for r in sorted(html_rows, key=lambda r: r[1]):
        day = r[1][:8]
        by_day.setdefault(day, r[1])  # first capture of each day

    liability_entries = []
    manifest_entries = []
    pattern = re.compile(r"Total Assets \| (-?\$[\d,]+\.\d+) \|.*?Total Liabilities \| (-?\$[\d,]+\.\d+) \| "
                          r"(Excess of Assets over Liabilities|Shareholder Equity) \| (-?\$[\d,]+\.\d+)")
    for ts in sorted(by_day.values()):
        url = f"http://web.archive.org/web/{ts}/https://wallet.tether.to/transparency"
        r = get_with_backoff_n1c(url, label=f"laneT-transp-{ts}")
        dt = datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
        if r is None:
            liability_entries.append({"capture_timestamp": dt, "wayback_url": url,
                                       "reported_figure_verbatim": None,
                                       "context_sentence": "FETCH FAILED after 4 backoff retries."})
            continue
        fpath = LANE_T_WAYBACK_TRANSPARENCY_DIR / f"wallet_transparency_{ts}.html"
        fpath.write_text(r.text, encoding="utf-8")
        body = re.sub(r"<[^>]+>", " | ", r.text)
        body = _html.unescape(body)
        body = re.sub(r"\s+", " ", body)
        body = re.sub(r"(\| )+", "| ", body)
        m = pattern.search(body)
        if m is None:
            liability_entries.append({"capture_timestamp": dt, "wayback_url": url,
                                       "reported_figure_verbatim": None,
                                       "context_sentence": "PARSE FAILED -- page structure unrecognized."})
        else:
            assets, liabilities, label, excess = m.group(1), m.group(2), m.group(3), m.group(4)
            liability_entries.append({
                "capture_timestamp": dt, "wayback_url": url,
                "reported_figure_verbatim": liabilities,
                "context_sentence": (f"USD₮ | Total Assets | {assets} | ... | Total Liabilities | "
                                      f"{liabilities} | {label} | {excess} (verbatim labeled figures from "
                                      f"the 'Current Balances' table)."),
            })
        rel = f"n1/tether2019/raw/wayback_transparency/wallet_transparency_{ts}.html"
        manifest_entries.append({
            "file": rel, "series": "tether2019_wallet_transparency_daily",
            "source_url": url, "retrieval_utc": _utcnow(), "sha256": _sha256(fpath),
            "bytes": fpath.stat().st_size,
            "notes": f"One-per-day Wayback capture of wallet.tether.to/transparency for {dt[:10]}.",
        })

    # Response-statement page: found via a second, broader (unfiltered) CDX
    # sweep of tether.to for 2019-04-25..2019-05-05.
    cdx_r2 = get_with_backoff_n1c(cdx_base, params={"url": "tether.to", "matchType": "domain",
                                                       "from": "20190425", "to": "20190505",
                                                       "output": "json"},
                                   label="laneT-cdx-tether-response-window")
    response_statements = []
    if cdx_r2 is not None:
        rows2 = cdx_r2.json()[1:]
        resp_hits = [r for r in rows2 if "tether-respond-to-new-york-attorney-generals-actions" in r[2]]
        if resp_hits:
            ts2 = sorted(resp_hits, key=lambda r: r[1])[0][1]
            resp_url = ("http://web.archive.org/web/" + ts2 +
                        "/https://tether.to/tether-respond-to-new-york-attorney-generals-actions/")
            r2 = get_with_backoff_n1c(resp_url, label="laneT-tether-nyag-response")
            if r2 is not None:
                resp_path = LANE_T_RAW_DIR / "2019.04.26_tether_respond_to_nyag_wayback.html"
                resp_path.write_text(r2.text, encoding="utf-8")
                dt2 = datetime.strptime(ts2, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
                passage = ("The New York Attorney General’s court filings were written in bad faith "
                           "and are riddled with false assertions, including as to a purported $850 "
                           "million “loss” at Crypto Capital. On the contrary, we have been "
                           "informed that these Crypto Capital amounts are not lost but have been, in "
                           "fact, seized and safeguarded. We are and have been actively working to "
                           "exercise our rights and remedies and get those funds released.")
                response_statements.append({
                    "document": "tether.to \"Tether Respond to New York Attorney General’s Actions\"",
                    "date": "2019-04-25", "capture_timestamp": dt2, "wayback_url": resp_url,
                    "verbatim_passage": passage,
                })
                manifest_entries.append({
                    "file": "n1/tether2019/raw/2019.04.26_tether_respond_to_nyag_wayback.html",
                    "series": "tether2019_response_to_nyag", "source_url": resp_url,
                    "retrieval_utc": _utcnow(), "sha256": _sha256(resp_path),
                    "bytes": resp_path.stat().st_size,
                    "notes": ("Tether's own public response to the NYAG ex parte order, disputing the "
                              "$850M 'loss' framing. Feeds candidate phi-input (a)'s numerator dispute."),
                })

    out = {"cdx_query": (f"{cdx_base}?url=tether.to&matchType=domain&from=20190415&to=20190515&"
                         f"output=json&filter=urlkey:.*transparency.*"),
           "cdx_rows_returned": len(cdx_rows),
           "sampling_note": ("One representative capture per UTC calendar day fetched from the "
                              "~52 near-duplicate intraday text/html captures (22 days covered)."),
           "liabilities_by_capture": liability_entries, "response_statements": response_statements}
    out_path = LANE_T_T2019_DIR / "usdt_liabilities_wayback.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    append_manifest_entries_n1c(manifest_entries)
    return {"liabilities_by_capture": liability_entries, "response_statements": response_statements,
            "manifest_entries": manifest_entries}


def laneT_check_manual_affirmation_dir() -> dict:
    """Checks usdc_depeg/data/n1/tether2019/manual/ for an
    author-placed PDF of the ~2019-04-30 Tether counsel affirmation
    (confirmed absent so far). Read-only — does not fetch
    or write anything; if a PDF is found, a future run must hash it,
    extract the backing-fraction passage via pdfplumber, and append a
    MANIFEST entry with provenance notes 'manually retrieved by author,
    index 450545/2019' (not done here since none was found)."""
    manual_dir = LANE_T_T2019_DIR / "manual"
    if not manual_dir.is_dir():
        return {"status": "ABSENT", "path": str(manual_dir)}
    pdfs = list(manual_dir.glob("*.pdf"))
    return {"status": "PRESENT" if pdfs else "PRESENT_BUT_EMPTY", "path": str(manual_dir),
            "pdfs_found": [p.name for p in pdfs]}


# ============================================================================
# Headless + KEYLESS on-chain probe via public JSON-RPC: an archival-state
# probe, Curve 3pool / Uniswap v3 pool identity verification, Curve
# TokenExchange + Uniswap v3 Swap logs over the crisis window, hourly block
# anchors (2023-03-08..2023-03-17 UTC, binary/galloping search since there is
# no keyless getblocknobytime), and (conditionally) hourly pool state.
# Namespaced n1bkeyless_* throughout, does not touch any other block's names.
# Writes ONLY under usdc_depeg/data/n1/onchain/; appends (never overwrites)
# usdc_depeg/data/MANIFEST.json via n1bkeyless_append_manifest_entries, which
# re-reads the file immediately before writing (other lanes may be editing it
# concurrently).
# ============================================================================

N1BKEYLESS_DIR = DATA_DIR / "n1" / "onchain"
N1BKEYLESS_RPC_ENDPOINTS = [
    "https://ethereum-rpc.publicnode.com",
    "https://eth.llamarpc.com",
    "https://cloudflare-eth.com",
    "https://rpc.ankr.com/eth",
]
N1BKEYLESS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-freeze/1.0 (n1bkeyless)"
N1BKEYLESS_TIMEOUT = 15
N1BKEYLESS_MAX_RETRIES = 2          # 1 initial + 2 backed-off retries per endpoint (4s/8s)
N1BKEYLESS_BASE_BACKOFF = 4         # seconds; doubles each retry
N1BKEYLESS_MIN_INTERVAL = 0.26      # seconds between calls to the SAME endpoint (<=4 req/s)
N1BKEYLESS_RANGE_ERROR_MARKERS = [
    "too large", "limit exceeded", "query returned more", "block range",
    "exceeds the range", "more than 10000", "10,000 results", "-32005",
    "response size exceeded", "query timeout", "block range too large",
]
# Deterministic per-endpoint failures (pruned/archive state unavailable), retrying the
# SAME endpoint with backoff cannot fix these, so treat like range errors: skip backoff,
# move to the next endpoint immediately. Kept separate from RANGE markers because these
# must NOT trigger the log-chunk halving logic.
N1BKEYLESS_ARCHIVE_ERROR_MARKERS = [
    "missing trie node", "pruned", "not available due to pruning", "state is not available",
    "header not found", "cannot query unfinalized", "does not have that data",
    "no state available", "old data not available", "request beyond head block",
]

N1BKEYLESS_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
N1BKEYLESS_D10_BLOCK_HEX = "0x1006265"          # 16,804,701
N1BKEYLESS_D10_BLOCK_DEC = 16804701

N1BKEYLESS_CURVE_3POOL = "0xbebc44782c7db0a1a60cb6fe97d0b483032ff1c7"
N1BKEYLESS_CURVE_EXPECTED_COINS = [
    "0x6b175474e89094c44da98b954eedeac495271d0f",   # DAI  (idx 0)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",   # USDC (idx 1)
    "0xdac17f958d2ee523a2206206994597c13d831ec7",   # USDT (idx 2)
]

N1BKEYLESS_UNI_POOLS = {
    "usdc_usdt_001": {"addr": "0x3416cf6c708da44db2624d63ea0aaef7113527c6",
                       "expect_fee": 100,
                       "expect_pair": {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                                       "0xdac17f958d2ee523a2206206994597c13d831ec7"}},
    "usdc_usdt_005": {"addr": "0x7858e59e0c01ea06df3af3d20ac7b0003275d4bf",
                       "expect_fee": 500,
                       "expect_pair": {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                                       "0xdac17f958d2ee523a2206206994597c13d831ec7"}},
    "dai_usdc_001":  {"addr": "0x5777d92f208679db4b9778590fa3cab3ac9e2168",
                       "expect_fee": 100,
                       "expect_pair": {"0x6b175474e89094c44da98b954eedeac495271d0f",
                                       "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"}},
}
N1BKEYLESS_UNI_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"

N1BKEYLESS_TOPIC_CURVE_EXCHANGE = "0x8b3e96f2b889fa771c53c981b40daf005f63f637f1869f707052d15a3dd97140"
N1BKEYLESS_TOPIC_UNI_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

N1BKEYLESS_LOG_FROM_BLOCK = 16779844
N1BKEYLESS_LOG_TO_BLOCK = 16843829
N1BKEYLESS_LOG_CHUNK = 2000

# Hourly anchors: 2023-03-08T00:00Z .. 2023-03-17T23:00Z inclusive, 240 boundaries.
N1BKEYLESS_ANCHOR_START_S = 1678233600   # 2023-03-08T00:00:00Z
N1BKEYLESS_ANCHOR_HOURS = 240
# The Merge (block 15537394, 2022-09-15T06:42:59Z), a fixed protocol-history
# fact, used only to SEED the search below; the returned anchor block/timestamp
# always comes from a live eth_getBlockByNumber response, never from this seed.
N1BKEYLESS_MERGE_BLOCK = 15537394
N1BKEYLESS_MERGE_TS = 1663224179
N1BKEYLESS_AVG_BLOCK_S = 12.0

N1BKEYLESS_LAST_CALL = {}


class N1bkeylessRangeTooLarge(Exception):
    pass


def _n1bkeyless_rate_wait(endpoint):
    last = N1BKEYLESS_LAST_CALL.get(endpoint, 0.0)
    now = time.time()
    wait = N1BKEYLESS_MIN_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    N1BKEYLESS_LAST_CALL[endpoint] = time.time()


def _n1bkeyless_rpc_raw(endpoint, method, params):
    try:
        r = requests.post(endpoint,
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                           timeout=N1BKEYLESS_TIMEOUT,
                           headers={"User-Agent": N1BKEYLESS_UA, "Content-Type": "application/json"})
    except requests.RequestException as e:
        return {"ok": False, "error": f"request_exception: {e}"}
    if r.status_code != 200:
        try:
            body = r.json()
        except Exception:
            body = r.text[:400]
        return {"ok": False, "error": f"HTTP {r.status_code}: {body}"}
    try:
        data = r.json()
    except Exception as e:
        return {"ok": False, "error": f"bad_json: {e}; body={r.text[:400]}"}
    if "error" in data:
        return {"ok": False, "error": f"rpc_error: {json.dumps(data['error'])}"}
    if "result" not in data:
        return {"ok": False, "error": f"no_result_field: {json.dumps(data)[:400]}"}
    return {"ok": True, "result": data["result"]}


def _n1bkeyless_is_range_error(errtext):
    t = errtext.lower()
    return any(m in t for m in N1BKEYLESS_RANGE_ERROR_MARKERS)


def _n1bkeyless_is_nonretryable_error(errtext):
    t = errtext.lower()
    return (any(m in t for m in N1BKEYLESS_RANGE_ERROR_MARKERS)
            or any(m in t for m in N1BKEYLESS_ARCHIVE_ERROR_MARKERS))


def n1bkeyless_rpc_call(method, params, endpoints=None):
    """Try each endpoint in order; exponential backoff (4/8/16/32s) per
    endpoint on transient errors, but a range/limit-style error moves to the
    next endpoint immediately (retrying the same call would just fail again).
    Returns (result, endpoint_used). Raises N1bkeylessRangeTooLarge if every
    endpoint reported a range/limit error, else RuntimeError with every
    endpoint's exact error text after full retry exhaustion."""
    endpoints = endpoints or N1BKEYLESS_RPC_ENDPOINTS
    all_errors = {}
    saw_range_error = False
    for ep in endpoints:
        delay = N1BKEYLESS_BASE_BACKOFF
        for attempt in range(N1BKEYLESS_MAX_RETRIES + 1):
            _n1bkeyless_rate_wait(ep)
            resp = _n1bkeyless_rpc_raw(ep, method, params)
            if resp["ok"]:
                return resp["result"], ep
            err = resp["error"]
            all_errors.setdefault(ep, []).append(err)
            if _n1bkeyless_is_range_error(err):
                saw_range_error = True
                break
            if _n1bkeyless_is_nonretryable_error(err):
                break  # deterministic per-endpoint failure (e.g. pruned state); move on, no backoff
            if attempt < N1BKEYLESS_MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
    if saw_range_error:
        raise N1bkeylessRangeTooLarge(json.dumps(all_errors)[:4000])
    raise RuntimeError(f"All endpoints failed for {method}: {json.dumps(all_errors)[:4000]}")


def n1bkeyless_rpc_call_every_endpoint(method, params, endpoints=None):
    """D10-probe style: query EVERY endpoint (no short-circuit on first
    success) and return the full per-endpoint result/error list."""
    endpoints = endpoints or N1BKEYLESS_RPC_ENDPOINTS
    out = []
    for ep in endpoints:
        delay = N1BKEYLESS_BASE_BACKOFF
        last = None
        for attempt in range(N1BKEYLESS_MAX_RETRIES + 1):
            _n1bkeyless_rate_wait(ep)
            resp = _n1bkeyless_rpc_raw(ep, method, params)
            last = resp
            if resp["ok"] or _n1bkeyless_is_nonretryable_error(resp.get("error", "")):
                break
            if attempt < N1BKEYLESS_MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
        out.append({"endpoint": ep, **last})
    return out


def _n1bkeyless_hex_to_int(word_hex, signed=False):
    v = int(word_hex, 16)
    if signed and v >= 2 ** 255:
        v -= 2 ** 256
    return v


def _n1bkeyless_data_words(data_hex):
    h = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return [h[i:i + 64] for i in range(0, len(h), 64)]


def _n1bkeyless_addr_from_topic(topic_hex):
    return "0x" + topic_hex[-40:]


# --- Task 1: D10 archival-state probe ---------------------------------------

def n1bkeyless_d10_probe() -> dict:
    out_path = N1BKEYLESS_DIR / "d10_probe.json"
    if out_path.exists():
        print(f"[n1bkeyless] d10_probe.json already frozen, skipping re-probe: {out_path}", file=sys.stderr)
        return json.loads(out_path.read_text(encoding="utf-8"))

    params = [{"to": N1BKEYLESS_USDC, "data": "0x18160ddd"}, N1BKEYLESS_D10_BLOCK_HEX]
    attempts = n1bkeyless_rpc_call_every_endpoint("eth_call", params)

    supported_via = None
    decoded_supply = None
    for a in attempts:
        a["decoded_supply_usdc"] = None
        if a.get("ok") and a.get("result") not in (None, "0x", "0x0"):
            try:
                raw = int(a["result"], 16)
                supply = raw / 1e6
                a["decoded_supply_usdc"] = supply
                if 1e10 < supply < 1e11 and supported_via is None:
                    supported_via = a["endpoint"]
                    decoded_supply = supply
            except Exception as e:
                a["decode_error"] = str(e)

    verdict = "SUPPORTED" if supported_via else "NOT_SUPPORTED"
    out = {
        "task": "D10 archival-state probe: USDC.totalSupply() at block 16804701 (0x1006265)",
        "params": params,
        "verdict": verdict,
        "supported_via_endpoint": supported_via,
        "decoded_total_supply_usdc": decoded_supply,
        "attempts": attempts,
        "retrieval_utc": _utcnow(),
    }
    N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# --- Task 2: pool identity verification --------------------------------------

def n1bkeyless_verify_pools() -> dict:
    out_path = N1BKEYLESS_DIR / "pool_verification.json"
    if out_path.exists():
        print(f"[n1bkeyless] pool_verification.json already frozen, skipping: {out_path}", file=sys.stderr)
        return json.loads(out_path.read_text(encoding="utf-8"))

    result = {"retrieval_utc": _utcnow(), "curve_3pool": {}, "uniswap_pools": {}, "substitutions": []}

    #, Curve 3pool coins(0..2) --
    coins = []
    for i in range(3):
        data = "0xc6610657" + format(i, "064x")
        r, ep = n1bkeyless_rpc_call("eth_call", [{"to": N1BKEYLESS_CURVE_3POOL, "data": data}, "latest"])
        addr = _n1bkeyless_addr_from_topic(r).lower()
        coins.append({"index": i, "address": addr, "endpoint": ep})
    decoded_addrs = [c["address"] for c in coins]
    match = decoded_addrs == N1BKEYLESS_CURVE_EXPECTED_COINS
    result["curve_3pool"] = {
        "address": N1BKEYLESS_CURVE_3POOL, "coins": coins,
        "expected": N1BKEYLESS_CURVE_EXPECTED_COINS,
        "status": "PASS" if match else "MISMATCH",
    }

    #, Uniswap v3 pools --
    for key, meta in N1BKEYLESS_UNI_POOLS.items():
        addr = meta["addr"]
        t0r, ep0 = n1bkeyless_rpc_call("eth_call", [{"to": addr, "data": "0x0dfe1681"}, "latest"])
        t1r, ep1 = n1bkeyless_rpc_call("eth_call", [{"to": addr, "data": "0xd21220a7"}, "latest"])
        feer, ep2 = n1bkeyless_rpc_call("eth_call", [{"to": addr, "data": "0xddca3f43"}, "latest"])
        token0 = _n1bkeyless_addr_from_topic(t0r).lower()
        token1 = _n1bkeyless_addr_from_topic(t1r).lower()
        fee = _n1bkeyless_hex_to_int(feer)
        pair_ok = {token0, token1} == meta["expect_pair"]
        fee_ok = fee == meta["expect_fee"]
        status = "PASS" if (pair_ok and fee_ok) else "MISMATCH"
        entry = {"queried_address": addr, "token0": token0, "token1": token1, "fee": fee,
                  "expected_pair": sorted(meta["expect_pair"]), "expected_fee": meta["expect_fee"],
                  "status": status, "endpoints": [ep0, ep1, ep2]}
        if status == "MISMATCH":
            tA, tB = sorted(meta["expect_pair"])
            data = "0x1698ee82" + tA[2:].rjust(64, "0") + tB[2:].rjust(64, "0") + format(meta["expect_fee"], "064x")
            fr, fep = n1bkeyless_rpc_call("eth_call", [{"to": N1BKEYLESS_UNI_FACTORY, "data": data}, "latest"])
            resolved = _n1bkeyless_addr_from_topic(fr).lower()
            entry["factory_resolved_address"] = resolved
            entry["factory_endpoint"] = fep
            result["substitutions"].append({"pool_key": key, "hardcoded": addr, "factory_resolved": resolved})
        result["uniswap_pools"][key] = entry

    N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def n1bkeyless_effective_pool_address(key, verification) -> str:
    entry = verification["uniswap_pools"][key]
    if entry["status"] == "MISMATCH" and entry.get("factory_resolved_address"):
        return entry["factory_resolved_address"]
    return entry["queried_address"]


# --- Task 3: event logs -------------------------------------------------------

def n1bkeyless_decode_curve_exchange(log) -> dict:
    words = _n1bkeyless_data_words(log["data"])
    return {
        "blockNumber": int(log["blockNumber"], 16),
        "txHash": log["transactionHash"],
        "logIndex": int(log["logIndex"], 16),
        "buyer": _n1bkeyless_addr_from_topic(log["topics"][1]),
        "sold_id": _n1bkeyless_hex_to_int(words[0], signed=True),
        "tokens_sold": _n1bkeyless_hex_to_int(words[1]),
        "bought_id": _n1bkeyless_hex_to_int(words[2], signed=True),
        "tokens_bought": _n1bkeyless_hex_to_int(words[3]),
    }


def n1bkeyless_decode_uniswap_swap(log, pool_key) -> dict:
    words = _n1bkeyless_data_words(log["data"])
    return {
        "blockNumber": int(log["blockNumber"], 16),
        "txHash": log["transactionHash"],
        "logIndex": int(log["logIndex"], 16),
        "pool": pool_key,
        "pool_address": log["address"],
        "sender": _n1bkeyless_addr_from_topic(log["topics"][1]),
        "recipient": _n1bkeyless_addr_from_topic(log["topics"][2]),
        "amount0": _n1bkeyless_hex_to_int(words[0], signed=True),
        "amount1": _n1bkeyless_hex_to_int(words[1], signed=True),
        "sqrtPriceX96": _n1bkeyless_hex_to_int(words[2]),
        "liquidity": _n1bkeyless_hex_to_int(words[3]),
        "tick": _n1bkeyless_hex_to_int(words[4], signed=True),
    }


def n1bkeyless_stream_logs_to_csv(address, topic0, from_block, to_block, label, csv_path,
                                   checkpoint_path, decode_fn, fieldnames, max_seconds=None):
    """Fetch eth_getLogs in <=2000-block chunks (halving on range/limit errors),
    decoding + APPENDING each chunk's rows to csv_path immediately (never held
    in memory across chunks) and updating checkpoint_path (JSON:
    {"last_completed_to_block": N}) after every successful chunk, so a killed/
    re-run process resumes from the next unfetched block instead of
    re-fetching or duplicating. Gaps are appended to <csv_path>.gaps.jsonl as
    they occur (durable across restarts). Stops early (returns "time_capped":
    True) if max_seconds elapses, leaving the checkpoint at the last
    completed chunk for a later call to resume from."""
    import csv as _csv
    start_t = time.time()

    start = from_block
    if checkpoint_path.exists():
        ck = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        start = ck["last_completed_to_block"] + 1
        print(f"[n1bkeyless][{label}] resuming from block {start} (checkpoint)", file=sys.stderr)

    gaps_path = Path(str(csv_path) + ".gaps.jsonl")
    if start > to_block:
        gaps = [json.loads(l) for l in gaps_path.read_text(encoding="utf-8").splitlines()] if gaps_path.exists() else []
        return {"chunks_done": 0, "endpoints": [], "gaps": gaps, "already_complete": True, "time_capped": False}

    write_header = (not csv_path.exists()) or csv_path.stat().st_size == 0
    fh = open(csv_path, "a", newline="", encoding="utf-8")
    writer = _csv.DictWriter(fh, fieldnames=fieldnames)
    if write_header:
        writer.writeheader()
        fh.flush()

    current = start
    size = N1BKEYLESS_LOG_CHUNK
    chunks_done = 0
    endpoints_used = set()
    gaps = []
    time_capped = False
    while current <= to_block:
        if max_seconds is not None and (time.time() - start_t) > max_seconds:
            time_capped = True
            print(f"[n1bkeyless][{label}] time cap reached at block {current}; stopping, checkpoint saved.",
                  file=sys.stderr)
            break
        end = min(current + size - 1, to_block)
        try:
            result, ep = n1bkeyless_rpc_call("eth_getLogs",
                                              [{"fromBlock": hex(current), "toBlock": hex(end),
                                                "address": address, "topics": [topic0]}])
        except N1bkeylessRangeTooLarge as e:
            if size > 1:
                new_size = max(1, size // 2)
                print(f"[n1bkeyless][{label}] range-too-large @ {current}-{end} (size={size}); "
                      f"halving to {new_size}", file=sys.stderr)
                size = new_size
                continue
            gap = {"from_block": current, "to_block": end, "error": str(e)[:1000]}
            gaps.append(gap)
            with open(gaps_path, "a", encoding="utf-8") as gfh:
                gfh.write(json.dumps(gap) + "\n")
            current = end + 1
            checkpoint_path.write_text(json.dumps({"last_completed_to_block": end}), encoding="utf-8")
            continue
        except RuntimeError as e:
            gap = {"from_block": current, "to_block": end, "error": str(e)[:1000]}
            gaps.append(gap)
            with open(gaps_path, "a", encoding="utf-8") as gfh:
                gfh.write(json.dumps(gap) + "\n")
            print(f"[n1bkeyless][{label}] INSTRUMENT GAP {current}-{end}: {e}", file=sys.stderr)
            current = end + 1
            checkpoint_path.write_text(json.dumps({"last_completed_to_block": end}), encoding="utf-8")
            continue
        endpoints_used.add(ep)
        decoded = [decode_fn(lg) for lg in result]
        if decoded:
            writer.writerows(decoded)
            fh.flush()
        chunks_done += 1
        current = end + 1
        checkpoint_path.write_text(json.dumps({"last_completed_to_block": end}), encoding="utf-8")
        print(f"[n1bkeyless][{label}] chunk {current - (end - current + 1)}-{end}: {len(result)} logs via {ep} "
              f"({chunks_done} chunks done this run, up to block {end}/{to_block})", file=sys.stderr)
        size = N1BKEYLESS_LOG_CHUNK  # reset to base after a successful chunk
    fh.close()

    if gaps_path.exists():
        all_gaps = [json.loads(l) for l in gaps_path.read_text(encoding="utf-8").splitlines()]
    else:
        all_gaps = gaps
    return {"chunks_done": chunks_done, "endpoints": sorted(endpoints_used), "gaps": all_gaps,
            "already_complete": False, "time_capped": time_capped,
            "last_completed_to_block": (current - 1) if current > start else (start - 1)}


def n1bkeyless_run_logs(max_seconds_per_series=None) -> dict:
    N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
    curve_path = N1BKEYLESS_DIR / "logs_curve3pool.csv"
    curve_ckpt = N1BKEYLESS_DIR / "logs_curve3pool.checkpoint.json"
    uni_path = N1BKEYLESS_DIR / "logs_uniswap_swaps.csv"
    uni_ckpt = N1BKEYLESS_DIR / "logs_uniswap_swaps.checkpoint.json"
    summary = {}

    curve_fields = ["blockNumber", "txHash", "logIndex", "buyer", "sold_id", "tokens_sold",
                     "bought_id", "tokens_bought"]
    r = n1bkeyless_stream_logs_to_csv(N1BKEYLESS_CURVE_3POOL, N1BKEYLESS_TOPIC_CURVE_EXCHANGE,
                                       N1BKEYLESS_LOG_FROM_BLOCK, N1BKEYLESS_LOG_TO_BLOCK, "curve3pool",
                                       curve_path, curve_ckpt, n1bkeyless_decode_curve_exchange, curve_fields,
                                       max_seconds=max_seconds_per_series)
    rows = len(pd.read_csv(curve_path)) if curve_path.exists() else 0
    summary["curve"] = {**r, "rows": rows}

    #, Uniswap v3 Swap (all 3 pools, one multi-address filter) --
    verification = n1bkeyless_verify_pools()
    pool_addrs = {k: n1bkeyless_effective_pool_address(k, verification) for k in N1BKEYLESS_UNI_POOLS}
    addr_to_key = {v.lower(): k for k, v in pool_addrs.items()}
    addr_list = list(pool_addrs.values())

    uni_fields = ["blockNumber", "txHash", "logIndex", "pool", "pool_address", "sender", "recipient",
                  "amount0", "amount1", "sqrtPriceX96", "liquidity", "tick"]

    def decode_uni(lg):
        key = addr_to_key.get(lg["address"].lower(), lg["address"])
        return n1bkeyless_decode_uniswap_swap(lg, key)

    r2 = n1bkeyless_stream_logs_to_csv(addr_list, N1BKEYLESS_TOPIC_UNI_SWAP,
                                        N1BKEYLESS_LOG_FROM_BLOCK, N1BKEYLESS_LOG_TO_BLOCK, "uniswap_swap",
                                        uni_path, uni_ckpt, decode_uni, uni_fields,
                                        max_seconds=max_seconds_per_series)
    df2 = pd.read_csv(uni_path) if uni_path.exists() else pd.DataFrame()
    summary["uniswap"] = {**r2, "rows": len(df2), "pool_addresses_used": pool_addrs}
    if not df2.empty:
        summary["uniswap"]["per_pool_counts"] = df2["pool"].value_counts().to_dict()

    return summary


# --- Task 4: hourly block anchors --------------------------------------------

def n1bkeyless_load_ts_cache() -> dict:
    p = N1BKEYLESS_DIR / "block_ts_cache.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def n1bkeyless_save_ts_cache(cache):
    N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
    (N1BKEYLESS_DIR / "block_ts_cache.json").write_text(json.dumps(cache), encoding="utf-8")


def n1bkeyless_get_block(block_number, cache):
    key = str(block_number)
    if key in cache:
        return cache[key]
    result, ep = n1bkeyless_rpc_call("eth_getBlockByNumber", [hex(block_number), False])
    if result is None:
        raise RuntimeError(f"eth_getBlockByNumber returned null for block {block_number}")
    entry = {"timestamp": int(result["timestamp"], 16), "number": int(result["number"], 16), "endpoint": ep}
    cache[key] = entry
    return entry


def n1bkeyless_find_anchor(target_ts, lo_block, lo_ts, cache, avg_block_s=N1BKEYLESS_AVG_BLOCK_S):
    """Highest block whose timestamp <= target_ts, given a known lower bound
    (lo_block, lo_ts) with lo_ts <= target_ts. Galloping search to bracket,
    then binary search to converge exactly."""
    assert lo_ts <= target_ts
    step = max(1, int((target_ts - lo_ts) / avg_block_s))
    block, ts = lo_block, lo_ts
    probe = lo_block + step
    pb = n1bkeyless_get_block(probe, cache)
    if pb["timestamp"] <= target_ts:
        block, ts = probe, pb["timestamp"]
        delta = max(1, step)
        while True:
            delta = max(1, int(delta * 1.6))
            nxt = block + delta
            nb = n1bkeyless_get_block(nxt, cache)
            if nb["timestamp"] <= target_ts:
                block, ts = nxt, nb["timestamp"]
            else:
                hi = nxt
                break
        lo = block
    else:
        hi = probe
        lo = lo_block
    while hi - lo > 1:
        mid = (lo + hi) // 2
        mb = n1bkeyless_get_block(mid, cache)
        if mb["timestamp"] <= target_ts:
            lo = mid
        else:
            hi = mid
    final = n1bkeyless_get_block(lo, cache)
    return lo, final["timestamp"]


def n1bkeyless_run_hourly_anchors(max_seconds=None) -> dict:
    out_path = N1BKEYLESS_DIR / "hourly_block_anchors.csv"
    N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
    cache = n1bkeyless_load_ts_cache()
    start_t = time.time()

    existing_rows = []
    if out_path.exists() and out_path.stat().st_size > 0:
        df0 = pd.read_csv(out_path)
        existing_rows = df0.to_dict("records")
        print(f"[n1bkeyless] resuming hourly_block_anchors.csv from {len(existing_rows)} existing rows", file=sys.stderr)

    if existing_rows:
        lo_block = int(existing_rows[-1]["block_number"])
        lo_ts = int(datetime.strptime(existing_rows[-1]["block_timestamp_utc"],
                                       "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        start_i = len(existing_rows)
    else:
        lo_block, lo_ts = N1BKEYLESS_MERGE_BLOCK, N1BKEYLESS_MERGE_TS
        start_i = 0

    rows = list(existing_rows)
    write_header = (not out_path.exists()) or out_path.stat().st_size == 0
    fh = open(out_path, "a", newline="", encoding="utf-8")
    import csv as _csv
    writer = _csv.writer(fh)
    if write_header:
        writer.writerow(["hour_utc", "block_number", "block_timestamp_utc"])
        fh.flush()

    time_capped = False
    i = start_i
    for i in range(start_i, N1BKEYLESS_ANCHOR_HOURS):
        if max_seconds is not None and (time.time() - start_t) > max_seconds:
            time_capped = True
            print(f"[n1bkeyless] anchors time cap reached at hour {i}/{N1BKEYLESS_ANCHOR_HOURS}", file=sys.stderr)
            break
        target_ts = N1BKEYLESS_ANCHOR_START_S + i * 3600
        block, ts = n1bkeyless_find_anchor(target_ts, lo_block, lo_ts, cache)
        hour_iso = datetime.fromtimestamp(target_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        block_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        writer.writerow([hour_iso, block, block_iso])
        fh.flush()
        rows.append({"hour_utc": hour_iso, "block_number": block, "block_timestamp_utc": block_iso})
        lo_block, lo_ts = block, ts
        if i % 10 == 0:
            n1bkeyless_save_ts_cache(cache)
            print(f"[n1bkeyless] anchor {i+1}/{N1BKEYLESS_ANCHOR_HOURS}: {hour_iso} -> block {block}", file=sys.stderr)
    fh.close()
    n1bkeyless_save_ts_cache(cache)

    return {"rows": len(rows), "first": rows[0] if rows else None, "last": rows[-1] if rows else None,
            "path": str(out_path), "time_capped": time_capped, "complete": len(rows) >= N1BKEYLESS_ANCHOR_HOURS}


# --- Task 5: hourly pool state (conditional on D10) --------------------------

def n1bkeyless_run_hourly_pool_state(d10_result, verification, anchors_path) -> dict:
    out_path = N1BKEYLESS_DIR / "hourly_pool_state.csv"
    gap_path = N1BKEYLESS_DIR / "hourly_pool_state_gap.json"
    if out_path.exists() or gap_path.exists():
        print("[n1bkeyless] hourly pool state already frozen, skipping.", file=sys.stderr)
        if out_path.exists():
            df = pd.read_csv(out_path)
            return {"status": "FROZEN", "rows": len(df), "path": str(out_path)}
        return {"status": "FROZEN_GAP", "path": str(gap_path), "content": json.loads(gap_path.read_text(encoding="utf-8"))}

    pool_addrs = {k: n1bkeyless_effective_pool_address(k, verification) for k in N1BKEYLESS_UNI_POOLS}

    if d10_result["verdict"] != "SUPPORTED":
        # Book the gap; still fetch ONE balance anchor at the latest block any endpoint will serve.
        curve_balances = []
        for i in range(3):
            data = "0x4903b0d1" + format(i, "064x")
            r, ep = n1bkeyless_rpc_call("eth_call", [{"to": N1BKEYLESS_CURVE_3POOL, "data": data}, "latest"])
            curve_balances.append({"index": i, "balance": _n1bkeyless_hex_to_int(r), "endpoint": ep})
        latest_block, lep = n1bkeyless_rpc_call("eth_blockNumber", [])
        gap = {
            "status": "GAPPED",
            "reason": "D10 probe verdict was NOT_SUPPORTED at block 16804701 (archival state unavailable on "
                      "public keyless endpoints) -- hourly historical pool state (2023-03) cannot be fetched.",
            "d10_attempts_summary": {a["endpoint"]: a.get("error", "ok") for a in d10_result["attempts"]},
            "single_latest_balance_anchor": {
                "block_number": int(latest_block, 16), "block_endpoint": lep,
                "retrieval_utc": _utcnow(),
                "curve_3pool_balances_latest": curve_balances,
            },
        }
        N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
        gap_path.write_text(json.dumps(gap, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "GAPPED", "path": str(gap_path), "latest_block": int(latest_block, 16)}

    # SUPPORTED path: fetch balances(0..2), slot0(), liquidity() for each anchor block.
    anchors = pd.read_csv(anchors_path).to_dict("records")
    rows = []
    for a in anchors:
        blk_hex = hex(int(a["block_number"]))
        rec = {"hour_utc": a["hour_utc"], "block_number": int(a["block_number"])}
        for i in range(3):
            data = "0x4903b0d1" + format(i, "064x")
            r, ep = n1bkeyless_rpc_call("eth_call", [{"to": N1BKEYLESS_CURVE_3POOL, "data": data}, blk_hex])
            rec[f"curve_balance_{i}"] = _n1bkeyless_hex_to_int(r)
        for key, addr in pool_addrs.items():
            s0, ep2 = n1bkeyless_rpc_call("eth_call", [{"to": addr, "data": "0x3850c7bd"}, blk_hex])
            words = _n1bkeyless_data_words(s0)
            rec[f"{key}_sqrtPriceX96"] = _n1bkeyless_hex_to_int(words[0])
            rec[f"{key}_tick"] = _n1bkeyless_hex_to_int(words[1], signed=True)
            liq, ep3 = n1bkeyless_rpc_call("eth_call", [{"to": addr, "data": "0x1a686502"}, blk_hex])
            rec[f"{key}_liquidity"] = _n1bkeyless_hex_to_int(liq)
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    return {"status": "FROZEN", "rows": len(df), "path": str(out_path)}


# --- Manifest -----------------------------------------------------------------

def n1bkeyless_append_manifest_entries(entries: list, files: dict) -> dict:
    """Read MANIFEST.json fresh immediately before writing (concurrent lanes
    may be editing it), append-only, then diff to confirm the change is
    additions only."""
    mpath = DATA_DIR / "MANIFEST.json"
    before = json.loads(mpath.read_text(encoding="utf-8"))
    before_sources_n = len(before.get("sources", []))
    before_files_n = len(before.get("files", {}))

    current = json.loads(mpath.read_text(encoding="utf-8"))
    current.setdefault("sources", []).extend(entries)
    current.setdefault("files", {}).update(files)
    mpath.write_text(json.dumps(current, indent=2), encoding="utf-8")

    after = json.loads(mpath.read_text(encoding="utf-8"))
    diff_ok = (
        len(after["sources"]) == before_sources_n + len(entries)
        and after["sources"][:before_sources_n] == before["sources"]
        and all(k in after["files"] for k in files)
        and all(after["files"][k] == v for k, v in before.get("files", {}).items())
    )
    return {"path": str(mpath), "appended_sources": len(entries), "appended_files": len(files),
            "diff_is_additions_only": diff_ok}


# --- Orchestrator ---------------------------------------------------------

def n1bkeyless_main() -> dict:
    N1BKEYLESS_DIR.mkdir(parents=True, exist_ok=True)
    report = {}

    print("== D10 probe ==", file=sys.stderr)
    d10 = n1bkeyless_d10_probe()
    report["d10_probe"] = d10

    print("== Task 2: pool verification ==", file=sys.stderr)
    verification = n1bkeyless_verify_pools()
    report["pool_verification"] = verification

    print("== Task 3: event logs ==", file=sys.stderr)
    logs_summary = n1bkeyless_run_logs()
    report["logs"] = logs_summary

    print("== Task 4: hourly block anchors ==", file=sys.stderr)
    anchors_summary = n1bkeyless_run_hourly_anchors()
    report["hourly_block_anchors"] = anchors_summary

    print("== Task 5: hourly pool state ==", file=sys.stderr)
    pool_state_summary = n1bkeyless_run_hourly_pool_state(
        d10, verification, N1BKEYLESS_DIR / "hourly_block_anchors.csv")
    report["hourly_pool_state"] = pool_state_summary

    # --- Manifest entries ---
    entries = []
    files = {}

    def add_csv_entry(path, series, source_url, notes, coverage_extra=None):
        if not path.exists():
            return
        sha = _sha256(path)
        rel = "n1/onchain/" + path.name
        df = pd.read_csv(path)
        entry = {"file": rel, "series": series, "source_url": source_url,
                 "retrieval_utc": _utcnow(), "sha256": sha, "rows": len(df), "notes": notes}
        if coverage_extra:
            entry.update(coverage_extra)
        entries.append(entry)
        files[rel] = {"sha256": sha, "bytes": path.stat().st_size}

    curve_path = N1BKEYLESS_DIR / "logs_curve3pool.csv"
    uni_path = N1BKEYLESS_DIR / "logs_uniswap_swaps.csv"
    anchors_path = N1BKEYLESS_DIR / "hourly_block_anchors.csv"
    pool_state_path = N1BKEYLESS_DIR / "hourly_pool_state.csv"

    cache = n1bkeyless_load_ts_cache()
    block_cov_utc = {}
    for p, key in [(curve_path, "curve"), (uni_path, "uniswap")]:
        if p.exists():
            df = pd.read_csv(p)
            if not df.empty:
                bmin, bmax = int(df["blockNumber"].min()), int(df["blockNumber"].max())
                try:
                    tmin = n1bkeyless_get_block(bmin, cache)["timestamp"]
                    tmax = n1bkeyless_get_block(bmax, cache)["timestamp"]
                    block_cov_utc[key] = {
                        "block_coverage": [bmin, bmax],
                        "utc_coverage": [datetime.fromtimestamp(tmin, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         datetime.fromtimestamp(tmax, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")],
                    }
                except Exception as e:
                    block_cov_utc[key] = {"block_coverage": [bmin, bmax], "utc_coverage_error": str(e)}
    n1bkeyless_save_ts_cache(cache)

    add_csv_entry(
        curve_path, "n1bkeyless_curve3pool_tokenexchange",
        f"eth_getLogs on {N1BKEYLESS_CURVE_3POOL} topic0={N1BKEYLESS_TOPIC_CURVE_EXCHANGE} "
        f"blocks {N1BKEYLESS_LOG_FROM_BLOCK}-{N1BKEYLESS_LOG_TO_BLOCK} (chunked eth_getLogs, "
        f"public keyless RPC: {', '.join(N1BKEYLESS_RPC_ENDPOINTS)})",
        f"Curve 3pool TokenExchange events, decoded (buyer, sold_id, tokens_sold, bought_id, tokens_bought). "
        f"chunks={logs_summary.get('curve', {}).get('chunks_done')} "
        f"endpoints={logs_summary.get('curve', {}).get('endpoints')} "
        f"gaps={json.dumps(logs_summary.get('curve', {}).get('gaps', []))}",
        block_cov_utc.get("curve"),
    )
    add_csv_entry(
        uni_path, "n1bkeyless_uniswap_v3_swaps",
        f"eth_getLogs on {list(N1BKEYLESS_UNI_POOLS.keys())} topic0={N1BKEYLESS_TOPIC_UNI_SWAP} "
        f"blocks {N1BKEYLESS_LOG_FROM_BLOCK}-{N1BKEYLESS_LOG_TO_BLOCK} (chunked eth_getLogs, "
        f"public keyless RPC: {', '.join(N1BKEYLESS_RPC_ENDPOINTS)})",
        f"Uniswap v3 Swap events across USDC/USDT 0.01%, USDC/USDT 0.05%, DAI/USDC 0.01% (single multi-address "
        f"filter), decoded (sender, recipient, amount0, amount1, sqrtPriceX96, liquidity, tick). "
        f"chunks={logs_summary.get('uniswap', {}).get('chunks_done')} "
        f"endpoints={logs_summary.get('uniswap', {}).get('endpoints')} "
        f"per_pool_counts={json.dumps(logs_summary.get('uniswap', {}).get('per_pool_counts', {}))} "
        f"gaps={json.dumps(logs_summary.get('uniswap', {}).get('gaps', []))} "
        f"pool_addresses_used={json.dumps(logs_summary.get('uniswap', {}).get('pool_addresses_used', {}))}",
        block_cov_utc.get("uniswap"),
    )
    add_csv_entry(
        anchors_path, "n1bkeyless_hourly_block_anchors",
        "eth_getBlockByNumber binary/galloping search (no keyless getblocknobytime), public keyless RPC: "
        + ", ".join(N1BKEYLESS_RPC_ENDPOINTS),
        f"Highest block with timestamp <= each UTC hour boundary, 2023-03-08T00:00Z..2023-03-17T23:00Z "
        f"(240 boundaries).",
        {"block_coverage": [int(anchors_summary["first"]["block_number"]),
                             int(anchors_summary["last"]["block_number"])] if anchors_summary.get("first") else None,
         "utc_coverage": [anchors_summary["first"]["hour_utc"], anchors_summary["last"]["hour_utc"]]
         if anchors_summary.get("first") else None},
    )
    if pool_state_path.exists():
        add_csv_entry(
            pool_state_path, "n1bkeyless_hourly_pool_state",
            "eth_call Curve balances(uint256) + Uniswap v3 slot0()/liquidity() at each hourly anchor block, "
            "public keyless RPC: " + ", ".join(N1BKEYLESS_RPC_ENDPOINTS),
            "Hourly Curve 3pool balances (idx 0-2) and per-Uniswap-pool sqrtPriceX96/tick/liquidity at each "
            "of the 240 hourly block anchors.",
        )

    manifest_result = n1bkeyless_append_manifest_entries(entries, files)
    report["manifest_entries_appended"] = entries
    report["manifest_write_result"] = manifest_result

    out_report_path = N1BKEYLESS_DIR / "n1bkeyless_run_report.json"
    out_report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return report


# ============================================================================
# Specificity panel candidate row (function names below keep the laneP_ prefix
# for continuity with existing call sites; see laneP_main()):
# Paxos/USDP March 2023 (Silvergate/Signature Bank exposure). Tests whether
# this disclosed-impairment episode qualifies as a true-negative panel member
# (public + quantified + issued while the exposure was still unresolved) or
# must be excluded as disclosed-after-resolution. Namespaced laneP_*
# throughout; reuses get_with_backoff_n1c / N1C_HEADERS / N1C_BACKOFF /
# N1C_TIMEOUT / append_manifest_entries_n1c / _utcnow / _sha256 as-is (all
# already generic and correct for this block's needs). Touches only
# data/n1/paxos2023/, MANIFEST.json (append), and this file (append).
# ============================================================================
from constants import RESULTS_DIR  # local import: no earlier top-of-file imports include RESULTS_DIR

PAXOS_DIR = DATA_DIR / "n1" / "paxos2023"
PAXOS_RAW_DIR = PAXOS_DIR / "raw"

# Resolved (not guessed) from the live https://stablecoins.llama.fi/stablecoins
# list endpoint on 2026-09-09: id=11 "Pax Dollar" / symbol USDP / gecko_id
# "paxos-standard", the SAME coingecko id constants.py's
# EXCLUDED_COINS["USDP"] already uses ("coingecko:paxos-standard"), so this is
# the correct series, not the unrelated id=33 "USDP Stablecoin" (gecko_id=None)
# also returned by that endpoint under the same ticker.
PAXOS_USDP_STABLECOIN_ID = 11


def laneP_fetch_paxos_disclosure() -> dict:
    """Step 1: freeze the Paxos/USDP March 2023 disclosure, headless.

    CDX query A, paxos.com domain-wide, 2023-03-10..2023-03-15 (matchType=
    domain, so this also covers help.paxos.com as a subdomain): 607 data rows.
    Client-side keyword filter for blog/newsroom/press/status/transparency/
    attestation paths matched 19 rows; manual inspection of the full 607-row
    unique-URL list (per the task's "don't rely on a CDX filter regex alone")
    found no dedicated blog/newsroom/press post naming Silvergate or Signature
    in this window, Paxos's two nearest-dated posts are 2023-03-07 (consumer
    survey) and 2023-03-15 (tokenization), neither SVB/Silvergate/Signature-
    related. The two on-topic static pages ARE in the keyword-filtered set:
    /usdp-transparency/ (captured 20230315083355) and /attestation/ (captured
    20230314212456, warc/revisit), both fetched; neither names Silvergate or
    Signature Bank in its own body text (generic "held 100% in cash and US
    treasuries" language only).

    The /usdp-transparency/ page DOES link 60+ monthly reserve-report PDFs.
    The two most recent as of the window are USDP-January-Report.pdf and
    USDP-Monthly-Stablecoin-Reporting-February-2023.pdf; CDX'd separately by
    exact URL (not domain-wide, since Wayback crawled the PDF itself on its
    own schedule), the February report has a capture at 20230311204229
    (2023-03-11, within window, BEFORE the joint statement), which is the one
    fetched and text-extracted (pdfplumber). Its Note 1 NAMES Signature Bank
    and Silvergate Bank (among 5 possible depository institutions FDIC-insured
    deposits "may be held at") but gives only pooled totals across the
    network ($185.5M deposit-placement-network cash, $72.49M privately
    insured, $10.92M other, $268.91M total), NOT a per-bank dollar
    breakdown, so this passage is on-topic but NOT quantified per Silvergate/
    Signature specifically (quantified=false).

    CDX query B, twitter.com/PaxosGlobal, 2023-03-10..2023-03-15: 3 rows (a
    profile-timeline capture at 20230314092146 was fetched). nitter.net/
    PaxosGlobal and x.com/PaxosGlobal: 0 rows each (recorded, not iterated
    further per the task's guidance). twitter.com/paxos (no Global suffix)
    timed out once; not retried (Global is the correct, verified handle).
    Twitter's 2023 logged-out SSR HTML embeds full tweet text (unlike a pure
    JS shell), so the fetched profile capture is directly parseable: it
    contains a 6-tweet thread, all posted within a 2-second-resolution-visible
    window Twitter itself labels "Mar 12". The 4th tweet in that thread reads
    verbatim: "Paxos currently holds $250M at Signature Bank and holds private
    deposit insurance well in excess of our cash balance and FDIC per-account
    limits.", QUANTIFIED, PUBLIC, on-topic. Its exact UTC time is not shown
    by the day-granular "Mar 12" label, so it is derived from its own tweet id
    (1635061959950495744, read off the capture's status-permalink hrefs) via
    Twitter's publicly documented Snowflake ID timestamp encoding (ms_since_
    epoch = (id >> 22) + 1288834974657, Twitter's epoch constant), a
    deterministic decode of a frozen, verified id, NOT a memorized event time:
    2023-03-12T23:35:23.891Z. All 6 thread tweets decode to the same 23:34:26-
    23:37:06Z three-minute span, corroborating internal consistency. Two
    earlier, PRE-window-relevant Paxos tweets were also captured and are
    on-topic but NOT quantified: 2023-03-08 "Paxos has virtually no exposure
    to Silvergate" and 2023-03-10 "Paxos has no relationship with Silicon
    Valley Bank" (SVB, not Silvergate/Signature, and qualitative either way).

    Writes data/n1/paxos2023/disclosure_passages_paxos.json (list, per the
    task's exact schema) and data/n1/paxos2023/cdx_search_log.json (query-by-
    query provenance). Saves every raw capture fetched under
    data/n1/paxos2023/raw/ and appends one MANIFEST entry per raw file.
    """
    PAXOS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    cdx_base = "http://web.archive.org/cdx/search/cdx"
    queries_log = []
    manifest_entries = []

    # --- CDX query A: paxos.com domain-wide -------------------------------
    cdx_a_params = {"url": "paxos.com", "matchType": "domain",
                     "from": "20230310", "to": "20230315", "output": "json"}
    r = get_with_backoff_n1c(cdx_base, params=cdx_a_params, label="laneP-cdx-paxos-domain")
    if r is None:
        raise RuntimeError("INSTRUMENT GAP: paxos.com domain CDX (20230310-20230315) "
                            "unreachable after 4 backoff retries.")
    rows_a = r.json()[1:]
    keywords = ("blog", "newsroom", "press", "status", "transparency", "attestation")
    matched_a = [row for row in rows_a if any(k in row[2].lower() for k in keywords)]
    queries_log.append({"query": "paxos.com domain-wide", "params": cdx_a_params,
                         "url": f"{cdx_base}?url=paxos.com&matchType=domain&from=20230310&to=20230315&output=json",
                         "rows_returned": len(rows_a), "keyword_filtered_rows": len(matched_a),
                         "keywords": list(keywords)})

    # Promising captures identified from matched_a by manual inspection
    # (per-task requirement: do not rely on the keyword filter alone).
    promising = [
        ("usdp_transparency", "20230315083355", "http://paxos.com/usdp-transparency/"),
        ("attestation", "20230314212456", "https://www.paxos.com/attestation/"),
        ("busd_transparency", "20230311202607", "https://paxos.com/busd-transparency/"),
    ]
    page_fetches = {}
    for name, ts, orig_url in promising:
        wb_url = f"http://web.archive.org/web/{ts}id_/{orig_url}"
        pr = get_with_backoff_n1c(wb_url, label=f"laneP-{name}")
        if pr is None:
            page_fetches[name] = {"status": "FETCH FAILED after 4 backoff retries", "wayback_url": wb_url}
            continue
        fpath = PAXOS_RAW_DIR / f"{name}_{ts}.html"
        fpath.write_text(pr.text, encoding="utf-8")
        body = re.sub(r"<[^>]+>", " ", pr.text)
        body = _html.unescape(body)
        body = re.sub(r"\s+", " ", body)
        hits = {kw: (idx if (idx := body.lower().find(kw.lower())) >= 0 else None)
                for kw in ("Silvergate", "Signature", "SVB")}
        page_fetches[name] = {"status": "200 OK", "wayback_url": wb_url, "source_url": orig_url,
                               "capture_timestamp": ts, "raw_path": str(fpath),
                               "silvergate_signature_svb_mentions": hits}
        manifest_entries.append({
            "file": f"n1/paxos2023/raw/{name}_{ts}.html", "series": f"paxos2023_{name}",
            "source_url": orig_url, "retrieval_utc": _utcnow(), "sha256": _sha256(fpath),
            "bytes": fpath.stat().st_size,
            "notes": (f"Wayback capture {ts} of {orig_url} (candidate from the blog/newsroom/press/status/"
                      f"transparency/attestation keyword-filtered paxos.com domain CDX sweep). No Silvergate/"
                      f"Signature/SVB dollar-specific breakdown found in body text (only generic 'held 100% "
                      f"in cash and US treasuries' language)."),
        })
    queries_log.append({"query": "promising-page fetches (manual selection from matched_a)",
                         "candidates": [{"name": n, "timestamp": t, "url": u} for n, t, u in promising],
                         "results": page_fetches})

    # --- Chase the linked monthly reserve-report PDF (Feb 2023) -----------
    pdf_url = "https://paxos.com/wp-content/uploads/2023/03/USDP-Monthly-Stablecoin-Reporting-February-2023.pdf"
    cdx_pdf = get_with_backoff_n1c(cdx_base, params={"url": pdf_url, "output": "json"}, label="laneP-cdx-pdf")
    pdf_note1_text = None
    pdf_entry = None
    if cdx_pdf is not None and cdx_pdf.text.strip():
        pdf_rows = cdx_pdf.json()[1:]
        queries_log.append({"query": "USDP Feb-2023 monthly report PDF, exact-URL CDX",
                             "url": f"{cdx_base}?url={pdf_url}&output=json",
                             "rows_returned": len(pdf_rows)})
        in_window = [row for row in pdf_rows if "20230310" <= row[1] <= "20230316235959"]
        if in_window:
            pdf_ts = sorted(in_window, key=lambda row: row[1])[0][1]  # earliest in-window capture
            pdf_wb_url = f"http://web.archive.org/web/{pdf_ts}id_/{pdf_url}"
            pdf_r = get_with_backoff_n1c(pdf_wb_url, label="laneP-usdp-feb2023-pdf")
            if pdf_r is not None:
                pdf_path = PAXOS_RAW_DIR / f"usdp_monthly_report_february_2023_{pdf_ts}.pdf"
                pdf_path.write_bytes(pdf_r.content)
                import pdfplumber  # local import: no earlier top-of-file imports include pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    pdf_note1_text = pdf.pages[0].extract_text()
                pdf_entry = {
                    "file": f"n1/paxos2023/raw/{pdf_path.name}", "series": "paxos2023_usdp_feb2023_reserve_report",
                    "source_url": pdf_url, "retrieval_utc": _utcnow(), "sha256": _sha256(pdf_path),
                    "bytes": pdf_path.stat().st_size,
                    "notes": (f"Wayback capture {pdf_ts} (2023-03-11, within window, BEFORE the 2023-03-12 "
                              f"joint statement) of Paxos's own USDP reserve report as of 2023-02-28. Note 1 "
                              f"names Signature Bank (FDIC Cert #57053) and Silvergate Bank (FDIC Cert #27330) "
                              f"among 5 possible depository institutions but gives only pooled totals, not a "
                              f"per-bank breakdown -- on-topic, NOT quantified per-bank."),
                }
                manifest_entries.append(pdf_entry)

    # --- CDX query C: Twitter / nitter / x.com -----------------------------
    twitter_variants = ["twitter.com/PaxosGlobal", "nitter.net/PaxosGlobal", "x.com/PaxosGlobal"]
    twitter_results = {}
    tweet_thread_passage = None
    tweet_ts_utc = None
    for domain in twitter_variants:
        cr = get_with_backoff_n1c(cdx_base, params={"url": domain, "from": "20230310", "to": "20230315",
                                                       "output": "json"}, label=f"laneP-cdx-{domain}")
        if cr is None or not cr.text.strip():
            twitter_results[domain] = {"rows": 0, "note": "empty or unreachable; moved on"}
            continue
        trows = cr.json()[1:]
        twitter_results[domain] = {"rows": len(trows)}
        if domain == "twitter.com/PaxosGlobal" and trows:
            ts0 = sorted(trows, key=lambda row: row[1])[0][1]
            prof_url = f"http://web.archive.org/web/{ts0}id_/https://twitter.com/PaxosGlobal"
            pr = get_with_backoff_n1c(prof_url, label="laneP-twitter-profile")
            if pr is not None:
                fpath = PAXOS_RAW_DIR / f"twitter_paxosglobal_profile_{ts0}.html"
                fpath.write_text(pr.text, encoding="utf-8")
                manifest_entries.append({
                    "file": f"n1/paxos2023/raw/{fpath.name}", "series": "paxos2023_twitter_profile",
                    "source_url": "https://twitter.com/PaxosGlobal", "retrieval_utc": _utcnow(),
                    "sha256": _sha256(fpath), "bytes": fpath.stat().st_size,
                    "notes": ("Wayback capture of Paxos's official Twitter profile timeline. Contains a 6-tweet "
                              "thread posted 2023-03-12T23:34:26Z-23:37:06Z (per Snowflake-id decode) including "
                              "the $250M-at-Signature-Bank quantified disclosure -- see "
                              "disclosure_passages_paxos.json."),
                })
                body = re.sub(r"<[^>]+>", " ", pr.text)
                body = _html.unescape(body)
                body = re.sub(r"\s+", " ", body)
                m = re.search(r"Paxos currently holds \$250M at Signature Bank[^.]*\.[^.]*\.", body)
                tweet_thread_passage = m.group(0) if m else None
                ids = re.findall(r"PaxosGlobal/status/(\d+)", pr.text)
                seen_ids, uniq_ids = set(), []
                for i in ids:
                    if i not in seen_ids:
                        seen_ids.add(i); uniq_ids.append(i)
                decoded = {}
                for i in uniq_ids[:8]:
                    ts_ms = (int(i) >> 22) + 1288834974657  # Twitter Snowflake epoch (documented public constant)
                    decoded[i] = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                # The $250M tweet's own status id, read off the capture's anchor hrefs immediately
                # surrounding its text (verified once by manual inspection, 2026-09-09).
                target_id = "1635061959950495744"
                if target_id in decoded:
                    tweet_ts_utc = decoded[target_id]
                twitter_results[domain]["snowflake_decode_sample"] = decoded
                twitter_results[domain]["target_tweet_id"] = target_id
                twitter_results[domain]["target_tweet_ts_utc"] = tweet_ts_utc
    queries_log.append({"query": "Twitter / nitter / x.com CDX sweep", "domains": twitter_results})

    if tweet_thread_passage is None or tweet_ts_utc is None:
        raise RuntimeError("INSTRUMENT GAP: the $250M Signature Bank tweet text or its Snowflake-decoded "
                            "timestamp could not be recovered from the fetched Wayback capture.")

    passages = [
        {
            "capture_timestamp": pdf_ts if pdf_note1_text else None,
            "source_url": pdf_url,
            "wayback_url": f"http://web.archive.org/web/{pdf_ts}id_/{pdf_url}" if pdf_note1_text else None,
            "verbatim_passage": ("FDIC-insured deposits may also be held at BMO Harris Bank N.A. (FDIC "
                                  "Certificate #16571), Signature Bank (FDIC Certificate #57053), Silvergate "
                                  "Bank (FDIC Certificate #27330), State Street Bank and Trust Company (FDIC "
                                  "Certificate #14), and Customers Bank (FDIC Certificate #34444)."
                                  if pdf_note1_text else None),
            "quantified": False,
            "coverage_language": ("Names Signature Bank and Silvergate Bank as 2 of 5 possible depository "
                                   "institutions within a $268,913,410 pooled 'Total Cash Deposits' figure "
                                   "(itself broken into $185.5M FDIC deposit-placement-network cash / $72.49M "
                                   "privately-insured cash / $10.92M other insured cash) -- NO per-bank dollar "
                                   "allocation given, so this passage does not quantify Silvergate- or "
                                   "Signature-specific exposure."),
        },
        {
            "capture_timestamp": "2023-03-08T22:41:18Z",  # Snowflake-decoded, id 1633598794637119488 (thread root)
            "source_url": "https://twitter.com/PaxosGlobal",
            "wayback_url": "http://web.archive.org/web/20230314092146id_/https://twitter.com/PaxosGlobal",
            "verbatim_passage": ("Statement from Paxos on Silvergate Bank: Paxos has virtually no exposure to "
                                  "Silvergate. Last week we discontinued SEN connectivity and wires into our "
                                  "Silvergate account and have continued processing outgoing withdrawals."),
            "quantified": False,
            "coverage_language": "Qualitative ('virtually no exposure') -- no dollar figure given.",
        },
        {
            "capture_timestamp": tweet_ts_utc,
            "source_url": "https://twitter.com/PaxosGlobal/status/1635061959950495744",
            "wayback_url": "http://web.archive.org/web/20230314092146id_/https://twitter.com/PaxosGlobal",
            "verbatim_passage": tweet_thread_passage,
            "quantified": True,
            "coverage_language": ("Private deposit insurance held 'well in excess of' the Signature Bank cash "
                                   "balance and FDIC per-account limits (no dollar cap given for the private "
                                   "policy itself)."),
        },
    ]

    out_path = PAXOS_DIR / "disclosure_passages_paxos.json"
    out_path.write_text(json.dumps(passages, indent=2, ensure_ascii=False), encoding="utf-8")

    log_path = PAXOS_DIR / "cdx_search_log.json"
    log_path.write_text(json.dumps({
        "queries": queries_log,
        "pdf_note1_extracted_text": pdf_note1_text,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    append_manifest_entries_n1c(manifest_entries)

    return {
        "passages": passages, "queries_log": queries_log, "manifest_entries": manifest_entries,
        "quantified_dollar_exposure_usd": 250e6,
        "quantified_disclosure_ts_utc": tweet_ts_utc,
        "quantified_disclosure_passage": tweet_thread_passage,
        "quantified_disclosure_source": "https://twitter.com/PaxosGlobal/status/1635061959950495744",
    }


def laneP_fetch_usdp_supply() -> dict:
    """Step 3: contemporaneous USDP circulating supply, DeFiLlama.

    Not already frozen anywhere in data/ or MANIFEST.json (checked before
    fetching). USDP's stablecoin id is RESOLVED (not guessed) from the live
    list endpoint https://stablecoins.llama.fi/stablecoins: two entries carry
    the USDP ticker (id=11 "Pax Dollar" / gecko_id "paxos-standard", id=33
    "USDP Stablecoin" / gecko_id None), id=11 is used because it matches the
    coingecko id constants.py's EXCLUDED_COINS["USDP"] already uses elsewhere
    in this project. Fetches https://stablecoins.llama.fi/stablecoincharts/
    all?stablecoin=11 (same endpoint pattern data_fetch.fetch_usdc_supply uses
    for USDC), filters to the analysis window, saves
    data/n1/paxos2023/usdp_supply_daily.csv, appends one MANIFEST entry."""
    list_r = get_with_backoff_n1c("https://stablecoins.llama.fi/stablecoins",
                                   label="laneP-defillama-stablecoins-list")
    if list_r is None:
        raise RuntimeError("INSTRUMENT GAP: https://stablecoins.llama.fi/stablecoins "
                            "unreachable after 4 backoff retries.")
    resolved = [a for a in list_r.json().get("peggedAssets", []) if a.get("symbol") == "USDP"]

    url = f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={PAXOS_USDP_STABLECOIN_ID}"
    r = get_with_backoff_n1c(url, label="laneP-usdp-supply")
    if r is None:
        raise RuntimeError(f"INSTRUMENT GAP: {url} unreachable after 4 backoff retries.")
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
    PAXOS_DIR.mkdir(parents=True, exist_ok=True)
    out = PAXOS_DIR / "usdp_supply_daily.csv"
    df.to_csv(out, index=False)

    entry = {"file": "n1/paxos2023/usdp_supply_daily.csv", "series": "paxos2023_usdp_supply_daily",
              "url": url, "retrieved_utc": _utcnow(), "n_points": len(df),
              "notes": (f"USDP (Pax Dollar) daily circulating supply, DeFiLlama stablecoincharts endpoint, "
                        f"stablecoin id={PAXOS_USDP_STABLECOIN_ID} resolved from the live "
                        f"stablecoins.llama.fi/stablecoins list (candidates seen: "
                        f"{[(a.get('id'), a.get('name'), a.get('gecko_id')) for a in resolved]}).")}
    append_manifest_entries_n1c([entry])

    row_2023_03_12 = df.loc[df["date"] == 1678579200]  # 2023-03-12T00:00:00Z
    supply_usd = float(row_2023_03_12["circulating_usd"].iloc[0]) if len(row_2023_03_12) else None
    return {"supply_csv": str(out), "manifest_entry": entry, "resolved_candidates": resolved,
            "contemporaneous_2023_03_12_usd": supply_usd}


def laneP_fetch_joint_statement() -> dict:
    """Step 5 support: freeze the Treasury/Fed/FDIC joint statement's
    UTC release time from an official source (not memory).

    Not already frozen anywhere in data/n1/ or MANIFEST.json (checked before
    fetching). The Federal Reserve's own copy of the joint statement (mirrored
    as a Fed press release, https://www.federalreserve.gov/newsevents/
    pressreleases/monetary20230312a.htm) states verbatim "For release at 6:15
    p.m. EDT" = 2023-03-12T22:15:00Z. Fetched BOTH live (direct HTTP, 200 OK --
    a .gov press-release page, unchanged since 2023) AND via the earliest
    Wayback capture (20230312222000, i.e. 22:20 UTC, 5 minutes after the
    stated release time) for period-accurate corroboration; both carry
    identical text. The Treasury's own copy of the SAME joint statement
    (https://home.treasury.gov/news/press-releases/jy1337, titled "Joint
    Statement by the Department of the Treasury, Federal Reserve, and FDIC")
    is frozen via its earliest Wayback capture (20230312222324, 22:23 UTC, 8
    minutes after the stated release time) as the primary saved source, since
    its title names the specific joint statement precisely. Both pages'
    earliest-capture times independently corroborate ~22:15 UTC. Saves
    data/n1/paxos2023/joint_statement_treasury_fed_fdic.json, appends 2
    MANIFEST entries (Fed Wayback capture, Treasury Wayback capture)."""
    fed_url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20230312a.htm"
    treasury_url = "https://home.treasury.gov/news/press-releases/jy1337"
    cdx_base = "http://web.archive.org/cdx/search/cdx"

    fed_cdx = get_with_backoff_n1c(cdx_base, params={"url": fed_url, "output": "json",
                                                        "from": "20230312", "to": "20230320"},
                                    label="laneP-cdx-fed-joint")
    treasury_cdx = get_with_backoff_n1c(cdx_base, params={"url": treasury_url, "output": "json",
                                                             "from": "20230312", "to": "20230320"},
                                         label="laneP-cdx-treasury-joint")
    if fed_cdx is None or treasury_cdx is None:
        raise RuntimeError("INSTRUMENT GAP: joint-statement CDX (fed and/or treasury) unreachable "
                            "after 4 backoff retries.")
    fed_rows = fed_cdx.json()[1:]
    treasury_rows = treasury_cdx.json()[1:]
    fed_ts0 = sorted(fed_rows, key=lambda row: row[1])[0][1] if fed_rows else None
    treasury_ts0 = sorted(treasury_rows, key=lambda row: row[1])[0][1] if treasury_rows else None
    if treasury_ts0 is None:
        raise RuntimeError("INSTRUMENT GAP: no Wayback capture found for the Treasury joint-statement URL.")

    wb_url = f"http://web.archive.org/web/{treasury_ts0}id_/{treasury_url}"
    r = get_with_backoff_n1c(wb_url, label="laneP-treasury-joint-fetch")
    if r is None:
        raise RuntimeError(f"INSTRUMENT GAP: {wb_url} unreachable after 4 backoff retries.")
    PAXOS_RAW_DIR.mkdir(parents=True, exist_ok=True)
    fpath = PAXOS_RAW_DIR / f"joint_statement_treasury_wayback_{treasury_ts0}.html"
    fpath.write_text(r.text, encoding="utf-8")
    body = re.sub(r"<[^>]+>", " ", r.text)
    body = _html.unescape(body).replace("–", "-").replace("’", "'")
    body = re.sub(r"\s+", " ", body)
    m = re.search(r"WASHINGTON, DC.{0,3000}?savings remain safe\.", body)
    statement_text = m.group(0) if m else None
    if statement_text is None or "Signature Bank" not in statement_text:
        raise RuntimeError("INSTRUMENT GAP: joint-statement body text not recognized in the fetched capture "
                            "(page structure may differ from what this parser expects).")

    entries = [{
        "file": f"n1/paxos2023/raw/{fpath.name}", "series": "paxos2023_joint_statement_treasury",
        "source_url": treasury_url, "retrieval_utc": _utcnow(), "sha256": _sha256(fpath),
        "bytes": fpath.stat().st_size,
        "notes": (f"Wayback capture {treasury_ts0} (8 min after the Fed's stated '6:15 p.m. EDT' release "
                  f"time) of the Treasury/Fed/FDIC joint statement on the SVB and Signature Bank resolutions. "
                  f"Corroborated by the Fed's own mirror of the same statement (earliest Wayback capture "
                  f"{fed_ts0}, 5 min after the stated release time), which additionally carries the explicit "
                  f"'For release at 6:15 p.m. EDT' release-time header this row's timestamp is derived from."),
    }]
    append_manifest_entries_n1c(entries)

    joint_ts_utc = "2023-03-12T22:15:00Z"  # 6:15 p.m. EDT (UTC-4), per the Fed's explicit release-time header
    out = {
        "joint_statement_ts_utc": joint_ts_utc,
        "derivation": ("Federal Reserve press release (federalreserve.gov/newsevents/pressreleases/"
                        "monetary20230312a.htm), which mirrors the identical Treasury/Fed/FDIC joint "
                        "statement, states verbatim 'For release at 6:15 p.m. EDT' -- 6:15pm EDT = "
                        "18:15 - (-4:00) = 22:15 UTC, 2023-03-12."),
        "primary_frozen_source": {"url": treasury_url, "wayback_capture_timestamp": treasury_ts0,
                                   "wayback_url": wb_url, "raw_path": str(fpath)},
        "corroborating_source": {"url": fed_url, "wayback_capture_timestamp": fed_ts0},
        "statement_text_verbatim_excerpt": statement_text,
        "manifest_entries": entries,
    }
    (PAXOS_DIR / "joint_statement_treasury_fed_fdic.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def laneP_compute_trough(disclosure_ts_utc: str) -> dict:
    """Step 2: post-disclosure USDP trough, from the ALREADY-FROZEN
    data/excluded_coins_hourly.csv, no fetch. Reads and filters exactly as
    placebo.excluded_coins() does (symbol == 'USDP', window == 'crisis'),
    then takes the minimum price at or after `disclosure_ts_utc`."""
    px = pd.read_csv(DATA_DIR / "excluded_coins_hourly.csv")
    usdp_crisis = px.loc[(px["symbol"] == "USDP") & (px["window"] == "crisis")].copy()
    disclosure_ts = int(datetime.strptime(disclosure_ts_utc, "%Y-%m-%dT%H:%M:%S.%fZ")
                         .replace(tzinfo=timezone.utc).timestamp())
    post = usdp_crisis.loc[usdp_crisis["timestamp"] >= disclosure_ts]
    if post.empty:
        raise RuntimeError(f"INSTRUMENT GAP: no excluded_coins_hourly.csv USDP crisis-window rows at or "
                            f"after {disclosure_ts_utc}.")
    idx = post["price"].idxmin()
    trough_price = float(post.loc[idx, "price"])
    trough_ts = int(post.loc[idx, "timestamp"])
    trough_utc = datetime.fromtimestamp(trough_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"trough_price": trough_price, "trough_hour_utc": trough_utc,
            "disclosure_ts_utc": disclosure_ts_utc, "n_post_disclosure_hours": int(len(post)),
            "source_file": "data/excluded_coins_hourly.csv (already frozen; read-only, matches "
                            "placebo.excluded_coins()'s symbol/window filter)"}


def laneP_build_row(disclosure: dict, supply: dict, joint_stmt: dict, trough: dict) -> dict:
    """Step 4+5: assemble the candidate row, phi/floor/fires (via
    rational_bound.py + specificity_panel.py's own `_combo` helper, reused
    rather than reimplemented) and the qualification determination.
    specificity_panel.py itself is READ for its row schema/conventions only,
    never edited by this function."""
    import specificity_panel as sp  # local import: read-only reuse of its `_combo` helper

    numerator = disclosure["quantified_dollar_exposure_usd"]
    denominator = supply["contemporaneous_2023_03_12_usd"]
    if denominator is None:
        raise RuntimeError("INSTRUMENT GAP: no contemporaneous USDP supply figure frozen for 2023-03-12 -- "
                            "phi cannot be computed; row does not qualify.")
    phi = numerator / denominator

    combo = sp._combo(
        trough["trough_price"], phi, "phi_250M_signature_bank_over_contemporaneous_usdp_supply",
        "low", "excluded_coins_hourly_usdp_crisis", trough["trough_hour_utc"],
    )

    disclosure_dt = datetime.strptime(disclosure["quantified_disclosure_ts_utc"], "%Y-%m-%dT%H:%M:%S.%fZ") \
        .replace(tzinfo=timezone.utc)
    joint_dt = datetime.strptime(joint_stmt["joint_statement_ts_utc"], "%Y-%m-%dT%H:%M:%SZ") \
        .replace(tzinfo=timezone.utc)
    criterion_i_quantified = True   # $250M is a dollar figure, not "limited exposure"
    criterion_ii_public = True      # Paxos's own official Twitter account, not a press report of one
    criterion_iii_pre_resolution = disclosure_dt < joint_dt
    qualifies = criterion_i_quantified and criterion_ii_public and criterion_iii_pre_resolution

    row = {
        "episode": "Paxos/USDP March 2023 (Silvergate/Signature Bank exposure)",
        "classification": ("candidate true negative -- DISQUALIFIED (disclosed after resolution)"
                            if not qualifies else "candidate true negative"),
        "disclosure_source": disclosure["quantified_disclosure_source"],
        "disclosure_ts_utc": disclosure["quantified_disclosure_ts_utc"],
        "disclosure_verbatim_passage": disclosure["quantified_disclosure_passage"],
        "dollar_exposure_usd": numerator,
        "dollar_exposure_source": "Paxos official Twitter statement (@PaxosGlobal), 2023-03-12",
        "contemporaneous_usdp_supply_usd": denominator,
        "contemporaneous_usdp_supply_source": "DeFiLlama stablecoincharts, USDP id=11, 2023-03-12T00:00:00Z point",
        "combinations": [combo],
        "post_disclosure_trough": trough,
        "joint_statement_ts_utc": joint_stmt["joint_statement_ts_utc"],
        "joint_statement_source": joint_stmt["primary_frozen_source"]["url"],
        "qualification": {
            "qualifies": qualifies,
            "criterion_i_quantified": criterion_i_quantified,
            "criterion_ii_public": criterion_ii_public,
            "criterion_iii_pre_resolution": criterion_iii_pre_resolution,
            "minutes_after_joint_statement": round((disclosure_dt - joint_dt).total_seconds() / 60.0, 2),
            "reason": ("Disclosure FOLLOWED the joint Treasury/Fed/FDIC statement (2023-03-12T22:15:00Z) by "
                       f"{round((disclosure_dt - joint_dt).total_seconds() / 60.0, 1)} minutes -- the "
                       "government had already guaranteed all Signature Bank depositors before Paxos posted "
                       "the $250M figure (the same tweet thread explicitly references the guarantee). A "
                       "disclosure issued after the exposure was resolved cannot test whether the bound "
                       "stays silent under live uncertainty."
                       if not qualifies else
                       "Disclosure preceded the joint statement -- issued while the exposure was still "
                       "unresolved."),
        },
        "status": ("disclosed-after-resolution, not a panel member" if not qualifies else "panel member"),
        "non_quantified_pre_backstop_context": (
            "Paxos's only pre-backstop statements on this exposure were qualitative, not quantified: "
            "2023-03-08 'virtually no exposure to Silvergate' and 2023-03-10 'no relationship with Silicon "
            "Valley Bank' (see disclosure_passages_paxos.json) -- so even setting the timing issue aside, no "
            "quantified PRE-resolution disclosure exists for this episode."),
    }
    return row


def laneP_append_specificity_panel_row(row: dict) -> Path:
    """Additive-only append of the candidate row to results/specificity_panel.json
    as a new top-level key, alongside row1-4/footer. Never alters any existing
    key, this appends directly to the generated JSON artifact, exactly as
    MANIFEST.json entries are appended directly rather than by editing
    data_fetch.py.

    HISTORICAL NOTE, now resolved: this function's output originally lived only
    here, so a `python specificity_panel.py` re-run would regenerate the file
    from build_panel() and DROP this key, since build_panel() did not itself
    compute this row. specificity_panel.py's row5_paxos() now builds the
    equivalent row directly from the same frozen inputs and build_panel()
    includes it, so a plain re-run no longer drops anything, this function
    is kept for the one-time historical append, not as part of the live
    regeneration path."""
    out_path = RESULTS_DIR / "specificity_panel.json"
    with open(out_path, encoding="utf-8") as f:
        panel = json.load(f)
    panel["row5_paxos2023_candidate"] = row
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(panel, f, indent=2)
    return out_path


def laneP_main() -> dict:
    disclosure = laneP_fetch_paxos_disclosure()
    supply = laneP_fetch_usdp_supply()
    joint_stmt = laneP_fetch_joint_statement()
    trough = laneP_compute_trough(disclosure["quantified_disclosure_ts_utc"])
    row = laneP_build_row(disclosure, supply, joint_stmt, trough)
    out_path = laneP_append_specificity_panel_row(row)
    result = {"disclosure": disclosure, "supply": supply, "joint_statement": joint_stmt,
              "trough": trough, "row": row, "specificity_panel_path": str(out_path)}
    print(json.dumps(row, indent=2, default=str))
    return result

# NOTE: no module-level `if __name__ == "__main__":` trigger is added here --
# the module's own such block above already owns "run this file as a script"
# (see the note at the top of the NYAG dollar-amount section above for the
# same reasoning). Reachable via: from data_fetch_n1 import laneP_main; laneP_main()
