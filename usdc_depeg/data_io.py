"""Deterministic loaders for the FROZEN data snapshot. Tests and the report read
only these CSVs — never the network."""
import pandas as pd

from constants import DATA_DIR


def load_price(symbol: str = "USDC") -> pd.DataFrame:
    """Hourly price series from the frozen DeFiLlama snapshot.
    Columns: symbol, timestamp (UTC seconds), price."""
    df = pd.read_csv(DATA_DIR / "price_hourly.csv")
    sub = df[df["symbol"] == symbol].sort_values("timestamp").reset_index(drop=True)
    if sub.empty:
        raise ValueError(f"No price rows for {symbol} in the snapshot.")
    return sub


def observed_trough(symbol: str = "USDC") -> float:
    """P_obs = minimum observed price over the window (the de-peg trough)."""
    return float(load_price(symbol)["price"].min())


def load_redemptions() -> pd.DataFrame:
    """Per-wallet redemption volumes from the frozen Etherscan snapshot.
    Columns: wallet, volume. Raises if the snapshot is absent (Number 2 pending)."""
    path = DATA_DIR / "redemptions_by_wallet.csv"
    if not path.exists():
        raise FileNotFoundError(
            "redemptions_by_wallet.csv not found. Number 2 needs the Etherscan "
            "snapshot -- run `python redemptions.py` with ETHERSCAN_API_KEY set "
            "(live FIFO attribution), or restore the frozen CSV and run "
            "`python redemptions.py --from-frozen` to re-validate it offline "
            "(data_fetch.py has no --etherscan flag; it never fetches redemptions)."
        )
    return pd.read_csv(path)
