"""Proposition 1 — the rational expected-loss bound and the implied backstop-failure
probability. Sharpens Number 1 from an accounting identity into a near-impossibility
result: under risk-neutral expected-loss pricing of the DISCLOSED reserve impairment,
no admissible failure probability rationalizes the observed trough, so the share of the
de-peg lying below the worst-case fundamental floor is necessarily non-fundamental.

Assumptions (stated so they can be stressed in the paper):
  A1  reserve-only fundamental: a coin's fundamental value is its expected reserve
      backing per unit given DISCLOSED reserves; the only impairment is the SVB
      exposure phi.
  A2  par redemption when solvent: conditional on a rescue, redemption is at $1.
  A3  bounded recovery: recovery on impaired reserves rho in [0,1] (fully reserved, no
      leverage), so the worst-case fundamental value is 1 - phi.

Under A1-A3 a risk-neutral expected-loss price is
      P_fund(q, rho) = 1 - q * phi * (1 - rho),   q = P(backstop fails) in [0, 1],
so P_fund lies in [1 - phi, 1]. Any observed price below 1 - phi cannot be rationalized:
the failure probability it would require (at worst-case rho = 0) is q = (1-P)/phi > 1.
"""
from constants import PHI_SVB
import data_io


def floor_worstcase(phi: float) -> float:
    """Worst-case fundamental floor 1 - phi (rho = 0): the lowest price any risk-neutral
    expected-loss story can produce under A1-A3."""
    return 1.0 - phi


def implied_failure_prob(p_obs: float, phi: float, rho_fail: float = 0.0) -> float:
    """Backstop-failure probability q that would rationalize p_obs as pure expected loss:
    p_obs = 1 - q*phi*(1 - rho_fail)  =>  q = (1 - p_obs) / (phi*(1 - rho_fail)).
    q > 1 means NO admissible probability rationalizes the price."""
    denom = phi * (1.0 - rho_fail)
    if denom <= 0:
        raise ValueError("phi*(1 - rho_fail) must be > 0.")
    return (1.0 - p_obs) / denom


def below_floor_share(p_obs: float, phi: float) -> float:
    """Share of the observed de-peg lying BELOW the worst-case fundamental floor, the
    part no expected-loss pricing (q <= 1, rho >= 0) can explain. Equals Number 1's
    magnitude-excess lower bound, re-derived as a rational-pricing impossibility."""
    floor = floor_worstcase(phi)
    delta_obs = 1.0 - p_obs
    if delta_obs <= 0:
        raise ValueError("p_obs must be < 1.")
    return max(0.0, (floor - p_obs) / delta_obs)


def report(p_obs: float = None, phi: float = PHI_SVB) -> dict:
    if p_obs is None:
        p_obs = data_io.observed_trough("USDC")
    q = implied_failure_prob(p_obs, phi, 0.0)
    return {
        "p_obs": p_obs,
        "phi": phi,
        "floor_worstcase": floor_worstcase(phi),
        "implied_failure_prob_rho0": q,
        "rationalizable_by_expected_loss": q <= 1.0,
        "below_floor_nonfundamental_share": below_floor_share(p_obs, phi),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(report(), indent=2))
