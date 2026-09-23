"""Second de-peg event (method generalization / control): the May 2022 Terra/LUNA
collapse. USDT had NO Terra reserve exposure (no UST/LUNA in reserves), so the
Terra-shock phi ~= 0, the worst-case fundamental floor is ~1.00, and Proposition 1
attributes essentially the ENTIRE dip to non-fundamental, a clean no-fundamental-shock
control: the method should NOT manufacture a fundamental story where none exists. USDC
(also no Terra exposure) is shown alongside as a second control.

Keyless. fetch(): DeFiLlama hourly USDT + USDC over the Terra window -> freeze
data/terra_price_hourly.csv + MANIFEST entry. analyze() (offline): apply the canonical
rational_bound with phi = 0.

Honesty note: DeFiLlama's aggregate USDT price troughs at ~0.993 here; venue-specific
troughs were deeper (~0.95 on some CEXs), but the floor = 1.00 conclusion is
depth-invariant (phi = 0 makes the whole dip non-fundamental regardless of depth).
"""
import hashlib
import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests

import rational_bound as rb
from constants import (DATA_DIR, DLL_COINS, TERRA_SPAN_HOURS, TERRA_USDT_PHI,
                       TERRA_WINDOW_START_S)

TERRA_FILE = DATA_DIR / "terra_price_hourly.csv"
TERRA_COINS = {"USDT": DLL_COINS["USDT"], "USDC": DLL_COINS["USDC"]}
TIMEOUT = 60


def fetch() -> pd.DataFrame:
    rows = []
    sources = []
    for sym, coin in TERRA_COINS.items():
        url = (f"https://coins.llama.fi/chart/{coin}"
               f"?start={TERRA_WINDOW_START_S}&span={TERRA_SPAN_HOURS}&period=1h")
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        prices = r.json()["coins"][coin]["prices"]
        for p in prices:
            rows.append({"symbol": sym, "timestamp": p["timestamp"], "price": p["price"]})
        sources.append((sym, url, len(prices)))
    df = pd.DataFrame(rows).sort_values(["symbol", "timestamp"])
    df.to_csv(TERRA_FILE, index=False)
    _freeze_manifest(sources)
    print(f"froze {len(df)} rows -> {TERRA_FILE.name}")
    return df


def _freeze_manifest(sources):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mpath = DATA_DIR / "MANIFEST.json"
    man = json.load(open(mpath)) if mpath.exists() else {"sources": [], "files": {}}
    man.setdefault("sources", [])
    man.setdefault("files", {})
    man["sources"] = [s for s in man["sources"] if s.get("file") != "terra_price_hourly.csv"]
    for sym, url, n in sources:
        man["sources"].append({"file": "terra_price_hourly.csv", "symbol": sym, "url": url,
                               "retrieved_utc": now, "n_points": n,
                               "window_utc": "2022-05-08 .. 2022-05-16"})
    man["files"]["terra_price_hourly.csv"] = {
        "sha256": hashlib.sha256(TERRA_FILE.read_bytes()).hexdigest(),
        "bytes": TERRA_FILE.stat().st_size,
    }
    json.dump(man, open(mpath, "w"), indent=2)


def analyze() -> dict:
    """rational_bound with phi=0 on the frozen Terra-window prices (offline)."""
    if not TERRA_FILE.exists():
        return {"status": "PENDING_DATA",
                "reason": "data/terra_price_hourly.csv missing -- run `python second_event.py`."}
    df = pd.read_csv(TERRA_FILE)
    out = {"status": "OK", "event": "Terra/LUNA collapse (May 2022)",
           "phi_terra_exposure": TERRA_USDT_PHI,
           "floor_worstcase": rb.floor_worstcase(TERRA_USDT_PHI)}
    for s in ("USDT", "USDC"):
        d = df.loc[df["symbol"] == s, "price"]
        trough, peak = float(d.min()), float(d.max())
        try:
            q = rb.implied_failure_prob(trough, TERRA_USDT_PHI, 0.0)
        except ValueError:
            q = None  # phi = 0: no impairment exists to assign a failure probability to
        bfs = rb.below_floor_share(trough, TERRA_USDT_PHI) if trough < 1.0 else 0.0
        out[s] = {
            "trough": trough, "peak": peak,
            "max_depeg_bps": round((1.0 - trough) * 1e4, 1),
            "max_premium_bps": round(max(0.0, peak - 1.0) * 1e4, 1),
            "implied_failure_prob": q,
            "below_floor_nonfundamental_share": bfs,
        }
    out["interpretation"] = (
        "Neither USDT nor USDC had Terra reserve exposure (phi~=0), so the worst-case floor is "
        "~1.00 and Proposition 1 attributes ~100% of each dip to non-fundamental -- the small, "
        "transient dips were panic/liquidity, not reserve repricing. The method does not invent a "
        "fundamental story where none exists. Contrast March 2023 USDC (phi=0.08, floor 0.92, "
        "35-100% non-fundamental). q is undefined here because phi=0 (no impairment to fail on)."
    )
    out["depth_caveat"] = ("DeFiLlama aggregate troughs are shallow (USDT ~0.993); venue-specific "
                           "CEX troughs were deeper (~0.95). The phi=0 -> 100% conclusion is "
                           "depth-invariant.")
    out["degenerate_caveat"] = ("phi=0 BY CONSTRUCTION, so the ~100% non-fundamental result is "
                                "degenerate-by-design: it shows the method does NOT invent "
                                "fundamentals (a falsification/honesty control), NOT that it "
                                "produces a non-trivial intermediate bound or a second quantitative "
                                "estimate.")
    return out


if __name__ == "__main__":
    fetch()
    print(json.dumps(analyze(), indent=2))
