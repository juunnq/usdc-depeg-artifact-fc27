"""Derivations 2 + 3 — the global-games run threshold and the concentration
comparative static. THEORY + numerical validation only; NOT an estimator of the
reported band (per the build routing, theta* never touches Number 1).

GLOBAL-GAMES CAVEAT (thresh_step[5]): the existence and uniqueness of the
threshold equilibrium, and the marginal holder's uniform belief ``n ~ U[0,1]``
over the redemption mass, are TAKEN FROM the global-games selection result
(Morris-Shin 1998; Goldstein-Pauzner 2005). The Laplace-integral /
Goldstein-Pauzner uniqueness step is CITED, NOT REPRODUCED here.
"""
import numpy as np
from scipy.optimize import brentq

from constants import SEED


def theta_star(g_b: float, eta: float, c: float) -> float:
    """Derivation 2 closed form (thresh_step[10]): ``theta* = g_b / (g_b + eta + c)``.

    g_b : net gain of REDEEM over HOLD if the peg BREAKS (> 0).
    eta : forfeited holding benefit (yield / liquidity premium), >= 0.
    c   : redemption transaction cost, >= 0.
    HOLD is normalized to 0 in BOTH states (thresh_step[0]); g_b and g_n are
    therefore net gains of REDEEM over HOLD in each respective state.
    """
    denom = g_b + eta + c
    if denom <= 0:
        raise ValueError("g_b + eta + c must be > 0.")
    return g_b / denom


def indifference(theta: float, g_b: float, eta: float, c: float) -> float:
    """Marginal-holder indifference value (thresh_step[6]):
    ``(1 - theta)*g_b + theta*g_n`` with ``g_n = -(eta + c)``. Zero at theta*."""
    g_n = -(eta + c)
    return (1.0 - theta) * g_b + theta * g_n


def theta_star_rootfind(g_b: float, eta: float, c: float) -> float:
    """theta* as the numerical root of ``indifference`` in (0, 1). Used ONLY to
    validate the closed form. indifference(0)=g_b>0, indifference(1)=-(eta+c)<0,
    so the bracket has a sign change."""
    return brentq(lambda th: indifference(th, g_b, eta, c), 1e-12, 1.0 - 1e-12)


def dtheta_dkappa(g_b: float, g_b_prime: float, eta: float, c: float) -> float:
    """Derivation 3 closed form (static_step[7]):
    ``d theta* / d kappa = g_b'(kappa) * (eta + c) / (g_b + eta + c)**2``.
    Strictly > 0 when g_b' > 0 and eta + c > 0 (static_step[8])."""
    return g_b_prime * (eta + c) / (g_b + eta + c) ** 2


def mc_indifference_at_theta_star(g_b: float, eta: float, c: float,
                                  n_draws: int = 400_000, seed: int = SEED):
    """Monte-Carlo check that the marginal holder is indifferent at theta*.

    Draw redemption mass ``n ~ U[0,1]``; the peg breaks iff ``n >= theta*``
    (thresh_step[4]). REDEEM pays g_b on a break and ``g_n = -(eta + c)`` if the
    peg holds. The mean REDEEM-over-HOLD payoff converges to
    ``(1 - theta*) g_b + theta* g_n = 0``.

    Returns ``(mean_payoff, theta_star, stderr)``.
    """
    th = theta_star(g_b, eta, c)
    rng = np.random.default_rng(seed)
    n = rng.uniform(0.0, 1.0, n_draws)
    g_n = -(eta + c)
    payoff = np.where(n >= th, g_b, g_n)
    return float(payoff.mean()), th, float(payoff.std(ddof=1) / np.sqrt(n_draws))
