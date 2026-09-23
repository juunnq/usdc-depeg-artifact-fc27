"""USDT-premium volume robustness, the premium held across material volume (not a thin print)."""
import json

import pytest

import usdt_premium
from constants import RESULTS_DIR


def test_premium_volume_present_and_positive():
    r = usdt_premium.robustness()
    if r.get("status") != "OK":
        # fallback path: the Binance cross-rate volume must at least be present + positive
        assert r["binance_usdcusdt_total_volume_usdc"] > 0
        return
    assert r["premium_volume_usdt"] > 0                          # figure present + positive
    assert r["total_volume_usdt"] >= r["premium_volume_usdt"] > 1e8  # material volume
    assert r["max_premium_bps"] > 0
    assert r["broke_par"] is False                              # never broke par on the fiat venue


def test_premium_magnitudes_pinned_to_frozen_snapshot():
    """Pin the abstract-level figures (+266 bps on $1.24B of $1.52B, min $0.999)
    against the frozen snapshot so they cannot silently drift.

    R1 fix (N7 C6): this used to silently `return` on any non-OK status, so a
    reproducer missing data/usdt_usd_hourly.csv got a green suite while the
    manuscript's tab:usdt figures went unpinned. The FALLBACK_BINANCE path is a
    legitimate degraded mode (see test_premium_volume_present_and_positive), but
    it cannot be used to pin these specific numbers, so this test must fail,
    not pass silently, when that file is missing."""
    r = usdt_premium.robustness()
    assert r.get("status") == "OK", (
        "data/usdt_usd_hourly.csv missing/not frozen -- run `python usdt_premium.py` "
        "(keyless) to produce it before this test can pin tab:usdt's figures.")
    assert r["max_premium_bps"] == pytest.approx(265.9, abs=0.5)
    assert r["min_price"] == pytest.approx(0.999, abs=1e-3)
    assert r["premium_volume_usdt"] == pytest.approx(1.2400227e9, rel=1e-4)
    assert r["total_volume_usdt"] == pytest.approx(1.5225630e9, rel=1e-4)


def test_results_artifact_persisted_and_matches():
    """report.py must persist results/usdt_premium.json (the paper's tab:usdt source);
    the artifact must agree with a fresh recomputation."""
    path = RESULTS_DIR / "usdt_premium.json"
    assert path.exists(), "run report.py: results/usdt_premium.json is a required artifact"
    frozen = json.load(open(path))
    r = usdt_premium.robustness()
    for key in ("status", "max_premium_bps", "min_price", "broke_par",
                "total_volume_usdt", "premium_volume_usdt"):
        assert frozen[key] == pytest.approx(r[key]) if isinstance(r[key], float) \
            else frozen[key] == r[key]
