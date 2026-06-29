"""
data/universe.py — Fetch ticker universes from external sources
================================================================
Two public functions:

    fetch_stock_tickers(exchanges)  → pd.DataFrame   (from SEC EDGAR)
    fetch_etf_tickers()             → pd.DataFrame   (from EODHD)

Both return DataFrames with columns that map directly to the
tickers table: symbol, asset_type, exchange, name, source.

SEC EDGAR requires no API key.
EODHD requires a free API key stored in .env as EODHD_API_KEY.
"""

import logging
import os

import pandas as pd
import requests
from dotenv import load_dotenv

from data.constants import EDGAR_TICKERS_URL, _SEC_HEADERS, EODHD_BASE_URL

load_dotenv()

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  STOCKS — SEC EDGAR
# ──────────────────────────────────────────────────────────────


def fetch_stock_tickers(
    exchanges: list[str] | None = None,
) -> pd.DataFrame:
    """
    Fetch all US stock tickers from SEC EDGAR's company_tickers_exchange.json.

    Parameters
    ----------
    exchanges : list[str] or None
        Filter to these exchanges (e.g. ["NYSE", "Nasdaq"]).
        If None, returns all exchanges.

    Returns
    -------
    pd.DataFrame
        Columns: symbol, asset_type, exchange, name, source
        Filtered to common stocks only (alphabetic tickers, ≤5 chars,
        no warrants/units/rights).
    """
    logger.info("Fetching stock tickers from SEC EDGAR...")

    resp = requests.get(EDGAR_TICKERS_URL, headers=_SEC_HEADERS, timeout=30)
    resp.raise_for_status()

    data = resp.json()

    # EDGAR returns {"fields": [...], "data": [[...], ...]}
    fields = data["fields"]
    rows = data["data"]
    df = pd.DataFrame(rows, columns=fields)

    # Standardise column names (EDGAR uses cik, name, ticker, exchange)
    df = df.rename(columns={"ticker": "symbol", "name": "name"})

    # Filter to requested exchanges
    if exchanges:
        # EDGAR uses mixed case: "NYSE", "Nasdaq", "CBOE", etc.
        exchange_upper = [e.upper() for e in exchanges]
        df = df[df["exchange"].str.upper().isin(exchange_upper)].copy()

    # Keep only clean common-stock tickers:
    #   - alphabetic only (drops things like "BRK-B", "GS.WS", warrants, units)
    #   - 1-5 characters (drops blank tickers and long codes)
    mask = (
        df["symbol"].str.match(r"^[A-Z]{1,5}$", na=False)
    )
    df = df[mask].copy()

    # Drop duplicates (same ticker can appear with different CIKs for
    # dual-class shares; keep first occurrence)
    df = df.drop_duplicates(subset="symbol", keep="first")

    # Shape output to match our tickers table
    df["asset_type"] = "stock"
    df["source"] = "edgar"

    result = df[["symbol", "asset_type", "exchange", "name", "source"]].copy()
    result = result.reset_index(drop=True)

    logger.info(f"EDGAR: {len(result)} stock tickers after filtering")
    return result


# ──────────────────────────────────────────────────────────────
#  ETFs — EODHD
# ──────────────────────────────────────────────────────────────


def fetch_etf_tickers() -> pd.DataFrame:
    """
    Fetch all US-listed ETFs from EODHD's exchange symbol list.

    Requires EODHD_API_KEY in .env (free tier is fine — 20 calls/day,
    and this only needs 1 call).

    Returns
    -------
    pd.DataFrame
        Columns: symbol, asset_type, exchange, name, source
    """
    api_key = os.getenv("EODHD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "EODHD_API_KEY not set in .env.  "
            "Get a free key at https://eodhd.com/register"
        )

    logger.info("Fetching ETF tickers from EODHD...")

    params = {
        "api_token": api_key,
        "type": "etf",
        "fmt": "json",
    }

    resp = requests.get(EODHD_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    df = pd.DataFrame(data)

    if df.empty:
        logger.warning("EODHD returned 0 ETFs — check your API key")
        return pd.DataFrame(columns=["symbol", "asset_type", "exchange", "name", "source"])

    # EODHD columns: Code, Name, Country, Exchange, Currency, Type, Isin
    df = df.rename(columns={"Code": "symbol", "Name": "name", "Exchange": "exchange"})

    # Keep only clean tickers (alphabetic, 1-5 chars)
    mask = df["symbol"].str.match(r"^[A-Z]{1,5}$", na=False)
    df = df[mask].copy()

    df = df.drop_duplicates(subset="symbol", keep="first")

    df["asset_type"] = "etf"
    df["source"] = "eodhd"

    result = df[["symbol", "asset_type", "exchange", "name", "source"]].copy()
    result = result.reset_index(drop=True)

    logger.info(f"EODHD: {len(result)} ETF tickers after filtering")
    return result
