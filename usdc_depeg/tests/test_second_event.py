"""Second-event control — Terra/LUNA (May 2022): phi=0 -> floor 1.0 -> ~100% non-fundamental."""
import pytest

import second_event as se


def test_terra_control_phi_zero_floor_one():
    r = se.analyze()
    # R1 fix: a reproducer missing this frozen input must see the suite go RED,
    # not silently skip past the Terra control to a green exit.
    assert r.get("status") != "PENDING_DATA", (
        f"required input missing -- {r.get('reason')}")
    assert r["phi_terra_exposure"] == 0.0
    assert r["floor_worstcase"] == pytest.approx(1.0)
    for s in ("USDT", "USDC"):
        assert r[s]["trough"] < 1.0                                            # there was a dip
        assert r[s]["below_floor_nonfundamental_share"] == pytest.approx(1.0)  # phi=0 -> 100%
        assert r[s]["implied_failure_prob"] is None                            # q undefined at phi=0


# --- frozen-snapshot gating (S6): pin tab:terra's troughs -----------------------
def test_frozen_terra_troughs_pinned():
    import pytest
    r = se.analyze()
    assert r["USDT"]["trough"] == pytest.approx(0.99264, abs=1e-4)
    assert r["USDC"]["trough"] == pytest.approx(0.99099, abs=1e-4)
