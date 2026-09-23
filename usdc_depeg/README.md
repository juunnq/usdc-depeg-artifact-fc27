# USDC March-2023 de-peg: a bounded panic share + redemption concentration

A small, reproducible codebase for a stablecoin de-peg analysis. It reports
**two numbers with bands**, computed from a frozen snapshot of public data:

1. **Non-fundamental share** of the de-peg (coordination panic + microstructure
   friction) — an honest **upper bound on pure coordination panic**, reported as a
   band from two independent restrictions, not a point estimate.
2. **Redemption Herfindahl** — the concentration of primary-market redemptions,
   the empirical analogue of the model's arbitrageur-concentration `kappa`.

## What the model is, and is not

The theory is a deliberately **simplified, fully hand-derivable global game** of a
stablecoin run (Derivations 2 + 3). It uses the global-games *selection result*
(Morris–Shin 1998; Goldstein–Pauzner 2005) to justify the threshold equilibrium
and the marginal holder's uniform belief over the redemption mass. It **does not
reproduce** the Laplace-integral / Goldstein–Pauzner uniqueness proof — that step
is **cited, not derived**, and the writeup says so.

Crucially, **the threshold `theta*` does not enter the two reported numbers.**
Number 1 is pure accounting (`P_fund = 1 − phi(1−rho)` plus two restrictions);
the threshold and the concentration comparative static are the *theory section*
and the *model-validation tests*.

**Scope — concentration vs. exposure (what this episode does and does not identify).**
The cross-stablecoin placebo (`placebo.py`) shows the realized fragility ordering by
trough — USDC (0.877) < DAI (0.886) < USDT (0.992) — exactly matches the *ex-ante
exposure* ranking (USDC direct > DAI contagion > USDT none). So **exposure drove the
ordering**. The model's concentration comparative static (`d theta*/d kappa > 0`,
Derivation 3) is a **separate, conditional prediction that this single episode does
NOT identify**: USDC was *low*-concentration (~521 arbitrageurs, dispersed) yet broke
hardest, because it was the *exposed* coin. We therefore do **not** claim concentration
predicted this episode; the static is reported as theory + validation only.

## Layout

| File | Role |
|---|---|
| `decomposition.py` | Derivations 1 + 4 — **estimator for Number 1** (floor + band) |
| `herfindahl.py`    | **estimator for Number 2** — raw/normalized HHI, top-k, bootstrap |
| `model.py`         | Derivations 2 + 3 — threshold + comparative static (theory + validation) |
| `data_fetch.py`    | fetch → freeze snapshot + `data/MANIFEST.json` (URL, UTC, SHA-256) |
| `data_io.py`       | deterministic loaders for the frozen snapshot |
| `report.py`        | compute both numbers + bands → `results/` |
| `tests/`           | seeded validation + estimator unit tests |

## Run

```bash
pip install -r requirements.txt

# Reproduce from the frozen snapshot (offline, keyless):
python data_fetch.py verify           # check data/*.csv against MANIFEST.json's hashes
python data_fetch_n1.py verify        # same check for the N1 cross-venue series
python redemptions.py --from-frozen   # re-validate the frozen FIFO attribution (not a
                                       # live re-attribution -- its raw input isn't frozen)
python -m pytest                      # seeded validation + unit tests
python report.py                      # write results/ and print the headline numbers

# Re-acquire from scratch (network; only to build a NEW snapshot, not to reproduce):
python data_fetch.py fetch        # keyless: price/supply/klines
python data_fetch_n1.py fetch     # keyless: Kraken + Bitstamp cross-venue series
python second_event.py            # keyless: Terra/LUNA control window
python usdt_premium.py            # keyless: USDT-USD premium volume
python redemptions.py             # needs ETHERSCAN_API_KEY: FIFO burn-attributed redemptions
python dai_contagion.py           # needs ETHERSCAN_API_KEY: DAI PSM on-chain backing
```

`fetch` refuses to overwrite any file `data/MANIFEST.json` already lists as frozen (use
`verify` to check an existing snapshot instead of re-pulling it).

`SEED = 20230311` seeds every stochastic routine (Monte-Carlo indifference check,
wallet bootstrap).

## Data

Window **2023-03-08 00:00 → 2023-03-17 00:00 UTC**. Sources, all public:

- **Price (hourly, USD):** DeFiLlama coins API for USDC/USDT/DAI (primary).
  CoinGecko's free tier no longer reaches March 2023 (365-day cap).
- **Price cross-check:** Binance `USDCUSDT` 1h klines (keyless) — a *contaminated*
  cross-rate (it embeds USDT's own deviation from $1), used only to confirm
  independently that a ~$0.88 de-peg occurred. The clean USD reference is
  DeFiLlama USDC/USD; the two troughs (0.8767 vs 0.882) differ by venue, timing,
  and liquidity, and the sign of that gap is not interpreted.
- **Supply (daily):** DeFiLlama stablecoins API (context only).
- **Redemptions (per wallet):** Etherscan `getLogs`, FIFO burn-attribution
  (`redemptions.py`). **Needs a free `ETHERSCAN_API_KEY`** (read from env only).

`phi = 0.08` (the $3.3B / ~8% SVB reserve exposure) is a documented model input,
not fetched.

### Number 2 — redemption concentration: a defensible bound

`redemptions.py` (run with `ETHERSCAN_API_KEY` in the environment — read from env
only, never written to disk) attributes redemptions by **FIFO burn-matching**:

1. Derive Circle's burner set **M** from the burn events themselves (USDC
   `Transfer → 0x0`); the free tier exposes no name-tags, so this is the
   reproducible ground truth. Over Mar 8–17 it is essentially one address
   (`0x55fe…44b8`, the Circle USDC minter = 99.8% of the **$8.15B** gross burned).
2. The minter is a busy **treasury, not a pure burn address**: over the window it
   took in $9.06B from external senders *and* $2.35B in fresh mints, burned $8.13B,
   and forwarded $3.41B back out (balance ~flat). Raw inflow therefore over-counts
   redemptions (that was the earlier 111% coverage). We instead **FIFO-match the
   actual burns to the external deposits that funded them**: every counted dollar is
   a real burn; mint-funded burns and non-redemption pass-through are excluded.
   Result: **776 redeemers, $7.32B attributed = 90% coverage** of gross burns
   (≤100% by construction).

**The result is a bound, not a point.** The two largest redeemers (40.9% + 28.6% =
69.5%) are single-source **conduits** that both feed from one **Coinbase** hot wallet
(`0xa9d1…3e43`) aggregating **14,111** distinct senders. Disaggregating a custodian
strictly lowers a Herfindahl, so the **burn-attributed HHI = 0.2558** (normalized
0.2548; seeded 95% CI 0.052–0.418; top-10 88.1%; largest 40.9%; effective redeemers
~4) is an **upper bound** on economic-redeemer concentration. Removing that one
verified custodian drops it to **0.0671** (effective redeemers ~15). So the
economic-redeemer Herfindahl is **≤ 0.26, and ~0.07 once Coinbase is unwound** — low
and custodial-routed. (The earlier raw-inflow proxy gave 0.2466; restricting to real
burns moves it only to 0.2558 — the conclusion is robust.) No external entity-label
set (MZZ replication / Arkham / Nansen) is in the repo, so this bound is complete.

**Supply reconciliation:** USDC circulating fell $43.2B → $38.1B by Mar 15
(≈ −$5.1B at the run trough), continuing to $36.6B by the Mar 17 window close. Gross
Ethereum burns over the window were $8.15B; the all-chain supply figure and the
Ethereum-only burn figure sit on different bases and are not equated.

**MZZ consistency:** consistent with Ma–Zeng–Zhang — USDC redemption is dispersed and
exchange-mediated (776 burn-attributed redeemers; Coinbase aggregates ~14k), matching
their ~521 USDC arbitrageurs vs USDT's ~6; the on-chain "concentration" is routing,
not arbitrage centralization.

**Limitations (disclosed):** the burner set is derived from burns, not a proprietary
tagged list; exchange/intermediary senders aggregate many ultimate redeemers; FIFO is
a conventional matching of fungible balances; off-chain redemptions are unobserved.
