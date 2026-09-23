"""Model validation — threshold + concentration comparative static (Deriv 2 + 3).
Seeded; these MUST pass."""
import numpy as np
import pytest

import model
from constants import SEED


@pytest.mark.parametrize("g_b,eta,c", [(0.1, 0.02, 0.005), (0.2, 0.05, 0.01), (0.5, 0.1, 0.02)])
def test_closed_form_matches_rootfind(g_b, eta, c):
    assert model.theta_star(g_b, eta, c) == pytest.approx(
        model.theta_star_rootfind(g_b, eta, c), abs=1e-9)


def test_indifference_zero_at_theta_star():
    th = model.theta_star(0.2, 0.05, 0.01)
    assert model.indifference(th, 0.2, 0.05, 0.01) == pytest.approx(0.0, abs=1e-12)


def test_mc_indifference_near_zero():
    mean, _th, se = model.mc_indifference_at_theta_star(0.2, 0.05, 0.01,
                                                        n_draws=400_000, seed=SEED)
    assert abs(mean) < 4 * se  # within 4 SE of zero (seeded -> deterministic)


def test_concentration_static_positive_and_matches_finite_diff():
    eta, c, g_b0 = 0.05, 0.01, 0.1
    g_b = lambda k: g_b0 * (1.0 + k)       # increasing in kappa; g_b'(k) = g_b0
    ks = np.linspace(0.0, 2.0, 50)
    thetas = np.array([model.theta_star(g_b(k), eta, c) for k in ks])
    assert np.all(np.diff(thetas) > 0)     # theta* strictly increasing in kappa

    k0, h = 1.0, 1e-6
    analytic = model.dtheta_dkappa(g_b(k0), g_b0, eta, c)
    fd = (model.theta_star(g_b(k0 + h), eta, c)
          - model.theta_star(g_b(k0 - h), eta, c)) / (2 * h)
    assert analytic > 0
    assert analytic == pytest.approx(fd, rel=1e-5)


def test_zero_friction_kills_the_channel():
    # static_step[8] necessity note: at eta + c = 0, theta* -> 1 and d theta*/d kappa -> 0.
    assert model.theta_star(0.2, 0.0, 0.0) == pytest.approx(1.0)
    assert model.dtheta_dkappa(0.2, 0.1, 0.0, 0.0) == pytest.approx(0.0)
