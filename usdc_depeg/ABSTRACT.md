# Abstract

**Non-Fundamental by Impossibility: A Public-Data Bound on Panic in the March 2023 USDC De-Peg**

How much of a stablecoin de-peg is rational reserve-loss repricing versus non-fundamental
— panic plus microstructure friction? We give a transparent, public-data method that bounds
the split, applied to the March 2023 USDC de-peg (Silicon Valley Bank trapped ~8% of
reserves; USDC fell to $0.877; a federal backstop reversed it). The identifying result uses
no structural model: under risk-neutral expected-loss pricing of the disclosed impairment,
the fundamental price cannot fall below 1 − φ = $0.92 for any failure probability or
recovery rate, yet the trough lies below it — rationalizing it would require an implied
failure probability of 1.54, which is impossible. The de-peg is thus non-fundamental by
impossibility, not assumption: at least 35% at the disclosed 8% impairment (robust for any
impairment below 12.3%), and near 100% once the full re-peg confirms no permanent loss — an
upper bound on pure panic. Three checks corroborate it. A hand-derivable global-games model
— which motivates the bound but never enters the estimator (uniqueness cited, not
reproduced) — predicts a fragility ordering by reserve exposure the episode realizes
exactly: USDC, DAI, and USDT trough at $0.877, $0.886, and $0.992, the last a +266 bps
flight-to-safety premium sustained on $1.24B of $1.52B in fiat volume — an upward divergence
no market-wide repricing can produce. Redemption concentration is low and custodial-routed
(burn-attributed Herfindahl ≤ 0.26, consistent with Ma–Zeng–Zhang 2025), and applied to
USDT in the May 2022 Terra collapse (no exposure, φ = 0) the method attributes its dip
almost entirely to non-fundamentals by construction — inventing no fundamental story where
none exists. With stablecoins entering their first U.S. federal framework, a reproducible
bound on the non-fundamental share speaks directly to reserve- and redemption-rule design;
all inputs are public and the pipeline reproducible end to end.

---

*Notes for the full paper.* "Non-fundamental" denotes coordination panic plus
microstructure friction and is an upper bound on pure coordination panic; the model
cites, and does not reproduce, the Goldstein–Pauzner uniqueness step. Frozen inputs:
USDC trough P_obs = 0.87667 (DeFiLlama hourly USD), φ = 0.08 (the $3.3B / ~8% SVB
exposure); the impossibility (implied failure probability > 1) holds for any φ below
12.3%, and the below-floor share stays positive (~19%) even at φ = 0.10. Redemption
concentration (FIFO burn-attribution — every counted dollar a real burn): Herfindahl
0.2558 (normalized 0.2548; seeded 95% CI 0.052–0.418; top-10 88.1%; largest 40.9%; 776
redeemers; $7.32B = 90% coverage of $8.15B gross burns); with the one verified custodian
(Coinbase) removed it falls to 0.0671. The raw inflow-to-minter proxy gave 0.2466 at 111%
coverage — that excess was Circle's treasury activity (concurrent mints plus $3.41B of
non-redemption pass-through). DAI's de-peg is mostly-to-entirely mechanical USDC
pass-through (PSM-linked; panic band [0, 435] bps). The USDT premium held on a
volume-bearing fiat venue (Coinbase USDT-USD): +266 bps, never broke par, $1.24B of $1.52B
above par. Supply: USDC circulating fell $43.2B → $38.1B by Mar 15 (≈ −$5.1B). Limitations:
the burner set is derived from on-chain burns, not a proprietary tagged list; DAI's PSM
USDC was off-chain by March 2023, so its backing fraction is reporting-based; the Terra
control is degenerate-by-design (φ = 0); off-chain redemptions are unobserved.
