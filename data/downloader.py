"""
data/downloader.py — Yahoo Finance batch downloader
=====================================================
Downloads daily OHLCV data via yfinance in batches.

Two public functions:

    download_ohlcv(symbols, start_date, end_date)
        → dict[str, pd.DataFrame]

    download_ohlcv_incremental(symbols_with_last_date, end_date)
        → dict[str, pd.DataFrame]

Both return a dict mapping symbol → DataFrame with columns:
    date, open, high, low, close, volume

The incremental version accepts a dict {symbol: last_date_fetched}
so each ticker only fetches the days it's missing.
"""

import logging
import random
import sys
import time
from datetime import date,timedelta
import pandas as pd
import yfinance as yf

from data.constants import HISTORY_YEARS, BATCH_SIZE, MIN_ROWS

logger = logging.getLogger(__name__)

def _normalize_single(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize a single-ticker DataFrame into clean columns:
    date, open, high, low, close, volume.

    Expects a DataFrame with OHLCV columns (any casing) and a
    Date/Datetime index or column.
    """
    df = df.copy()

    # Flatten MultiIndex columns if present (shouldn't be for single-ticker)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[-1] if isinstance(col, tuple) else col for col in df.columns]

    df.columns = [str(c).lower().strip() for c in df.columns]

    # Ensure we have the columns we need
    required = {"open", "high", "low", "close", "volume"}
    available = set(df.columns)
    if not required.issubset(available):
        return pd.DataFrame()

    df = df[["open", "high", "low", "close", "volume"]].copy()

    # Force numeric
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop all-NaN price rows
    df = df.dropna(subset=["open", "high", "low", "close"], how="all")

    if df.empty:
        return df

    # Index → column
    df = df.reset_index()

    # Find the date column
    date_col = [c for c in df.columns if c.lower() in ("date", "datetime")]
    if date_col:
        df = df.rename(columns={date_col[0]: "date"})

    df["date"] = pd.to_datetime(df["date"]).dt.date

    return df[["date", "open", "high", "low", "close", "volume"]]


def _download_batch(
    tickers: list[str], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """
    Download OHLCV for a batch of tickers.

    Uses group_by='ticker' so multi-ticker results come back with
    top-level ticker columns: data[ticker] gives that ticker's OHLCV.
    Returns dict of symbol → normalized DataFrame.
    """
    try:
        data = yf.download(
            tickers=tickers,
            start=start,
            end=end,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error(f"    Batch download error: {e}")
        return {}

    if data.empty:
        return {}

    results = {}

    if len(tickers) == 1:
        # Single ticker: yfinance returns flat columns, not grouped
        ticker = tickers[0]
        df = _normalize_single(data)
        if not df.empty and len(df) > MIN_ROWS:
            results[ticker] = df
    else:
        # Multi-ticker: data[ticker] extracts that ticker's sub-DataFrame
        for ticker in tickers:
            try:
                ticker_df = data[ticker].dropna(how="all")
                if ticker_df.empty or len(ticker_df) <= MIN_ROWS:
                    continue
                df = _normalize_single(ticker_df)
                if not df.empty and len(df) > MIN_ROWS:
                    results[ticker] = df
            except (KeyError, Exception):
                pass

    return results


def download_ohlcv(
    symbols: list[str],
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Download daily OHLCV for a list of symbols from Yahoo Finance.

    Downloads in batches with random delays (2-6s) to avoid rate
    limits.  Failed batches are retried once with longer delays.

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to download.
    start_date : date or None
        First date to fetch.  Defaults to HISTORY_YEARS ago.
    end_date : date or None
        Last date to fetch.  Defaults to today.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping of symbol → DataFrame with columns:
        date, open, high, low, close, volume.
        Symbols that fail or return no data are omitted.
    """
    if not symbols:
        return {}

    if start_date is None:
        start_date = date.today() - timedelta(days=HISTORY_YEARS * 365)
    if end_date is None:
        end_date = date.today()

    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    batches = [symbols[i : i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    all_data: dict[str, pd.DataFrame] = {}
    failed_batches: list[list[str]] = []

    logger.info(
        f"Downloading OHLCV for {len(symbols)} symbols "
        f"in {len(batches)} batches ({start_str} → {end_str})"
    )

    for idx, batch in enumerate(batches):
        pct = (idx + 1) / len(batches) * 100
        sys.stdout.write(
            f"\r  Batch {idx+1}/{len(batches)} ({pct:.0f}%) — "
            f"{len(all_data)} tickers loaded so far"
        )
        sys.stdout.flush()

        batch_data = _download_batch(batch, start_str, end_str)

        if not batch_data:
            failed_batches.append(batch)
        else:
            all_data.update(batch_data)

        if idx < len(batches) - 1:
            delay = random.uniform(2.0, 6.0)
            time.sleep(delay)

    # ── Retry failed batches with longer delays ──
    if failed_batches:
        logger.info(
            f"\n  Retrying {len(failed_batches)} failed batch(es) with longer delays..."
        )
        for idx, batch in enumerate(failed_batches):
            delay = random.uniform(8.0, 15.0)
            time.sleep(delay)
            sys.stdout.write(f"\r  Retry {idx+1}/{len(failed_batches)}...")
            sys.stdout.flush()

            batch_data = _download_batch(batch, start_str, end_str)
            if batch_data:
                all_data.update(batch_data)

    print()  # newline after progress output
    logger.info(f"Downloaded data for {len(all_data)}/{len(symbols)} symbols")
    return all_data


def download_ohlcv_incremental(
    symbols_with_last_date: dict[str, date | None],
    end_date: date | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Download OHLCV incrementally — only fetch days each symbol is missing.

    Parameters
    ----------
    symbols_with_last_date : dict[str, date | None]
        Mapping of symbol → last_date_fetched.
        If None, does a full backfill for that symbol.
    end_date : date or None
        Last date to fetch.  Defaults to today.

    Returns
    -------
    dict[str, pd.DataFrame]
        Same format as download_ohlcv().
    """
    if end_date is None:
        end_date = date.today()

    default_start = date.today() - timedelta(days=HISTORY_YEARS * 365)

    # Group symbols by their start date so we can batch efficiently
    by_start: dict[date, list[str]] = {}
    for sym, last_date in symbols_with_last_date.items():
        if last_date is None:
            start = default_start
        else:
            # Start the day after the last fetched date
            start = last_date + timedelta(days=1)

        if start > end_date:
            # Already up to date
            continue

        by_start.setdefault(start, []).append(sym)

    if not by_start:
        logger.info("All symbols are up to date — nothing to download")
        return {}

    logger.info(
        f"Incremental download: {sum(len(s) for s in by_start.values())} symbols "
        f"across {len(by_start)} start-date groups"
    )

    all_results: dict[str, pd.DataFrame] = {}
    for start, syms in by_start.items():
        results = download_ohlcv(syms, start_date=start, end_date=end_date)
        all_results.update(results)

    return all_results
