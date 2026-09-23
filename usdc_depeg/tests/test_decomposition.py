"""Number 1 — fundamentals floor and the non-fundamental-share band (Deriv 1 + 4)."""
import pytest

import decomposition as dec


def test_floor_basic():
    assert dec.fundamentals_floor(0.08, 0.0) == pytest.approx(0.92)   # total loss
    assert dec.fundamentals_floor(0.08, 1.0) == pytest.approx(1.0)    # full recovery
    assert dec.fundamentals_floor(0.08, 0.5) == pytest.approx(0.96)


def test_nonfundamental_share_spec_anchor():
    # Spec anchor: P_obs=0.877, phi=0.08 -> low ~35%, high 100%.
    assert dec.nonfundamental_share(0.877, 0.08, 0.0) == pytest.approx(0.34959, abs=1e-3)
    assert dec.nonfundamental_share(0.877, 0.08, 1.0) == pytest.approx(1.0)


def test_band_edges_and_ordering():
    b = dec.band(0.877, 0.08)
    assert b["low"] < b["high"]
    assert b["high"] == pytest.approx(1.0)       # reversal edge is exactly 100%
    assert 0.34 < b["low"] < 0.36


def test_band_clamps_when_fundamentals_sufficient():
    # delta_obs=0.05 < phi=0.08: fundamentals alone could explain it -> low clamps to 0.
    b = dec.band(0.95, 0.08)
    assert b["low"] == 0.0
    assert b["low_raw"] < 0.0


def test_requires_a_depeg():
    with pytest.raises(ValueError):
        dec.nonfundamental_share(1.0, 0.08, 0.0)
