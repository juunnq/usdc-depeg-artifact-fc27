"""Derivations 1 + 4 — the fundamentals floor and the non-fundamental-share band.

This module is the ESTIMATOR for reported Number 1. It is pure accounting:
``P_fund = 1 - phi*(1 - rho)``, then the two band restrictions. The global-games
run threshold theta* (see model.py) deliberately does NOT enter here — per the
build routing, no threshold gates the panic-share number.

The residual isolated below is the NON-FUNDAMENTAL deviation (coordination panic
PLUS mechanical liquidity / fire-sale friction). It is an UPPER BOUND on pure
coordination panic, and is never relabeled as pure panic.
"""


def fundamentals_floor(phi: float, rho: float) -> float:
    """Derivation 1 (floor_step[5]): ``P_fund = 1 - phi*(1 - rho)``.

    phi : fraction of reserves impaired (e.g. 0.08 for the SVB exposure).
    rho : expected recovery rate of the impaired assets, in [0, 1].
    Returns the rational, no-panic price floor = expected backing per coin.
    """
    return 1.0 - phi * (1.0 - rho)


def fundamental_deviation(phi: float, rho: float) -> float:
    """``Delta_fund = phi*(1 - rho) == 1 - P_fund`` (decomp_step[1])."""
    return phi * (1.0 - rho)


def nonfundamental_share(p_obs: float, phi: float, rho: float) -> float:
    """Derivation 4 (decomp_step[2]): ``(Delta_obs - Delta_fund) / Delta_obs``.

    The non-fundamental share of the observed maximum de-peg = the
    coordination-panic + friction residual as a fraction of the total deviation.
    """
    delta_obs = 1.0 - p_obs
    if delta_obs <= 0:
        raise ValueError("p_obs must be < 1 (a de-peg below par) to decompose.")
    delta_fund = fundamental_deviation(phi, rho)
    return (delta_obs - delta_fund) / delta_obs


def band(p_obs: float, phi: float) -> dict:
    """Reported band from the two INDEPENDENT restrictions (decomp_step[3-9]).

    low edge  (rho = 0): magnitude-excess restriction. ``phi`` is the maximum
        permanent fundamental loss mathematically possible (total wipeout of the
        impaired reserves); the deviation beyond it is the MINIMUM non-fundamental
        share.
    high edge (rho = 1): reversal restriction. The FDIC backstop produced a full
        re-peg, so NO permanent loss occurred and ~100% of the deviation was
        non-fundamental.

    Returns raw (unclamped) edges plus the clamped reportable band in [0, 1]. A
    negative raw low edge means phi alone can account for the whole de-peg
    (fundamentals-sufficient); it is clamped to 0 and the raw value is retained
    for honesty.
    """
    low = nonfundamental_share(p_obs, phi, rho=0.0)
    high = nonfundamental_share(p_obs, phi, rho=1.0)
    return {
        "low_raw": low,
        "high_raw": high,
        "low": max(0.0, min(1.0, low)),
        "high": max(0.0, min(1.0, high)),
        "p_obs": p_obs,
        "phi": phi,
        "delta_obs": 1.0 - p_obs,
    }
