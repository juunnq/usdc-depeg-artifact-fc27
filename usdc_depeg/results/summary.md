# USDC de-peg (March 2023): headline results

Frozen P_obs (USDC trough, DeFiLlama hourly): 0.87667
phi (SVB reserve impairment): 0.08
Delta_obs (observed max de-peg): 0.12333

## Number 1 - non-fundamental share
(coordination panic + microstructure friction; an UPPER BOUND on pure panic)

**Band: 35.1% - 100.0%**
- low edge (rho=0, magnitude-excess restriction): 35.1%
- high edge (rho=1, reversal restriction): 100.0%
  The reversal (full re-peg after the FDIC backstop) argues the high end.

## Proposition 1 - rational-pricing bound (sharpens Number 1)

Under risk-neutral expected-loss pricing of the disclosed phi=0.08 impairment,
the fundamental price cannot fall below 1 - phi = 0.92 (worst case rho=0).
The observed trough 0.8767 lies below it: rationalizing it would require a
backstop-failure probability q = 1.54 > 1 (impossible).
So the 35.1% of the de-peg below the floor is non-fundamental by IMPOSSIBILITY, not by the arithmetic choice of rho.

## Number 2 - redemption Herfindahl


## Number 3 - cross-stablecoin placebo (falsification test)

Prediction: shock-exposed coins break; the unexposed coin holds (else the
de-peg is a market-wide repricing, not a shock-driven run).
- USDC [direct (~8% of reserves at SVB)]: trough 0.8767  -> BROKE
- DAI [contagion (heavily USDC-collateralized)]: trough 0.8859  -> BROKE
- USDT [none (no SVB exposure)]: trough 0.9924, +270 bps premium  -> HELD
**Placebo: PASS** (exposed broke, unexposed held; break threshold < 0.99).
Realized fragility by trough: USDC < DAI < USDT; ex-ante exposure USDC > DAI > USDT -> MATCH (exposure, not concentration, drove the ordering -- see README scope note).

## Number 4 - DAI contagion pass-through

- NOT_SHIPPED: on-chain DAI contagion is not part of the short paper's artifact; dai_contagion.py is excluded from the export

### USDT-premium robustness

On a volume-bearing fiat venue (Coinbase USDT-USD (1h)), USDT reached +266 bps and never
broke par (min 0.9990); $1.24B of $1.52B traded above par
-> the premium held on MATERIAL volume, not a thin print.

## Second event (control) - Terra/LUNA collapse, May 2022

USDT and USDC had NO Terra reserve exposure (phi=0), so the worst-case
floor is 1.00 and Proposition 1 attributes ~100% of each dip to non-fundamental:
- USDT: trough 0.9926 (74 bps dip) -> below-floor non-fundamental 100%
- USDC: trough 0.9910 (90 bps dip, +260 bps premium) -> below-floor non-fundamental 100%
Clean no-fundamental-shock control: q is undefined (phi=0), the method does not invent
a fundamental story. NOTE: phi=0 BY CONSTRUCTION, so the ~100% result is DEGENERATE-BY-DESIGN
-- a falsification/honesty control, NOT a second quantitative estimate or a non-trivial bound.
Contrast Mar-2023 USDC (phi=0.08, floor 0.92, 35-100% non-fundamental).
(DeFiLlama aggregate troughs are shallow; the phi=0 -> 100% conclusion is depth-invariant.)

## Figures (regenerated to figs/ by figures.py)
- fig_11mar_panel.pdf  - hour-by-hour 11 March panel across every frozen feed (main-text Fig. 1)
- fig_depeg_paths.pdf  - USDC/DAI/USDT paths over the full window, floor(t), firing band, event markers
(fig3_phi_rho_sweep and fig4_model_validation are cut -- orphaned / removed by an earlier ruling)
