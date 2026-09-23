"""Proposition 1 — rational expected-loss bound + implied backstop-failure probability."""
import pytest

import data_io
import rational_bound as rb
from constants import PHI_SVB


def test_worstcase_floor():
    assert rb.floor_worstcase(0.08) == pytest.approx(0.92)


def test_implied_failure_prob_exceeds_one():
    p_obs = data_io.observed_trough("USDC")
    q = rb.implied_failure_prob(p_obs, PHI_SVB, 0.0)
    assert q == pytest.approx(1.5416, abs=1e-3)
    assert q > 1.0  # no admissible probability rationalizes the trough


def test_below_floor_share_matches_number1_low_edge():
    p_obs = data_io.observed_trough("USDC")
    assert rb.below_floor_share(p_obs, PHI_SVB) == pytest.approx(0.3513, abs=1e-3)


def test_report_not_rationalizable():
    r = rb.report()
    assert r["floor_worstcase"] == pytest.approx(0.92)
    assert r["implied_failure_prob_rho0"] > 1.0
    assert r["rationalizable_by_expected_loss"] is False
    assert r["below_floor_nonfundamental_share"] == pytest.approx(0.3513, abs=1e-3)


def test_implied_failure_prob_raises_when_no_impairment():
    # phi*(1 - rho) <= 0 -> no expected-loss story exists -> raise.
    with pytest.raises(ValueError):
        rb.implied_failure_prob(0.95, 0.08, 1.0)   # rho_fail = 1 -> denom 0
    with pytest.raises(ValueError):
        rb.implied_failure_prob(0.95, 0.0, 0.0)    # phi = 0 -> denom 0
