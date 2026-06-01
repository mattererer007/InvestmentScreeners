"""
stock_screener.py — NYSE + NASDAQ Stock Screener
==================================================
Scans all common stocks listed on NYSE and NASDAQ for quarterly
oversold + crossover signals.

Same indicator logic and thresholds as etf_screener.py, just a
different universe.

Dependencies:
    pip install yfinance pandas requests

Data sources (all free, no API key needed):
    1. Alpha Vantage LISTING_STATUS → full list of US-traded stocks
    2. yfinance → daily OHLCV price history (resampled to quarterly)

FILTER CONDITIONS (at least one must be true on quarterly view):
    1. Stochastic Momentum Index (SMI) has dropped below -40
    2. Stochastic Slow average (%K + %D)/2 is below 35
    3. Relative Strength Index (RSI) average is below 30

SIGNAL CONDITION (at least one must be true):
    - Stochastic Slow %K has crossed above %D
    - RSI has crossed above its signal (SMA) line

Usage:
    python stock_screener.py
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
import requests
import io
import time
import random
import sys
from datetime import datetime, timedelta

from indicators import resample_ohlcv, compute_all
from constants import (
    RSI_PERIOD, RSI_SIGNAL_PERIOD,
    STOCH_K_PERIOD, STOCH_SLOW_K, STOCH_SLOW_D,
    SMI_PERIOD, SMI_SMOOTH1, SMI_SMOOTH2,
    SMI_THRESHOLD, STOCH_AVG_THRESHOLD, RSI_AVG_THRESHOLD,
    HISTORY_YEARS, MIN_PRICE, MIN_AVG_VOLUME, BATCH_SIZE,
)


# ══════════════════════════════════════════════════════════════
#  STEP 1: Choose exchange + get stock list
# ══════════════════════════════════════════════════════════════

def prompt_exchange() -> list[str]:
    """Prompt user to pick which exchange(s) to scan."""
    print("  Which exchange(s) do you want to screen?")
    print("    1) NYSE only")
    print("    2) NASDAQ only")
    print("    3) Both NYSE + NASDAQ")
    print()

    while True:
        choice = input("  Enter 1, 2, or 3: ").strip()
        if choice == "1":
            return ["NYSE"]
        elif choice == "2":
            return ["NASDAQ"]
        elif choice == "3":
            return ["NYSE", "NASDAQ"]
        else:
            print("  Invalid choice. Please enter 1, 2, or 3.")


def get_stock_tickers_alphavantage(exchanges: list[str]) -> list[str]:
    """
    Download the full US listing from Alpha Vantage (free, no key needed)
    and filter to common stocks on the selected exchange(s).
    """
    url = "https://www.alphavantage.co/query?function=LISTING_STATUS&apikey=demo"
    label = " + ".join(exchanges)
    print(f"Fetching {label} stock listing from Alpha Vantage...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))

        # Filter to stocks only (exclude ETFs, warrants, etc.)
        stocks = df[df["assetType"] == "Stock"].copy()

        # Filter to selected exchange(s)
        stocks = stocks[stocks["exchange"].isin(exchanges)]

        # Filter to active listings
        if "status" in stocks.columns:
            stocks = stocks[stocks["status"] == "Active"]

        tickers = stocks["symbol"].tolist()

        # Remove tickers with special characters (warrants, units, etc.)
        tickers = [t for t in tickers
                   if isinstance(t, str)
                   and len(t) <= 5
                   and t.isalpha()]

        print(f"  Found {len(tickers)} common stocks on {label}")

        # Show exchange breakdown
        exchange_counts = stocks["exchange"].value_counts()
        for exch, count in exchange_counts.items():
            print(f"    {exch}: {count}")

        return tickers

    except Exception as e:
        print(f"  Alpha Vantage failed: {e}")
        return []


def get_stock_tickers_fallback() -> list[str]:
    """Hardcoded fallback list of ~100 major stocks for testing."""
    return [
        # Tech
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
        "AVGO", "ORCL", "CRM", "AMD", "ADBE", "NFLX", "INTC",
        "CSCO", "IBM", "QCOM", "TXN", "AMAT", "MU", "NOW", "UBER",
        "SHOP", "SQ", "SNAP", "PINS", "PLTR", "COIN", "HOOD",
        # Finance
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW",
        "AXP", "V", "MA", "PYPL", "COF", "USB",
        # Healthcare
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "TMO",
        "ABT", "BMY", "AMGN", "GILD", "ISRG", "MDT",
        # Consumer
        "WMT", "PG", "KO", "PEP", "COST", "MCD", "NKE",
        "SBUX", "TGT", "HD", "LOW", "TJX",
        # Industrial
        "CAT", "DE", "BA", "HON", "UPS", "GE", "RTX", "LMT",
        "MMM", "UNP", "FDX",
        # Energy
        "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC",
        "PSX", "VLO", "HAL",
        # Other
        "DIS", "CMCSA", "T", "VZ", "TMUS",
        "NEE", "DUK", "SO", "D",
        "AMT", "PLD", "CCI", "SPG",
    ]


def get_stock_tickers(exchanges: list[str]) -> list[str]:
    tickers = get_stock_tickers_alphavantage(exchanges)
    if len(tickers) < 50:
        print("  Falling back to hardcoded stock list")
        tickers = get_stock_tickers_fallback()
    return tickers


# ══════════════════════════════════════════════════════════════
#  STEP 2: Download daily OHLCV data in batches
# ══════════════════════════════════════════════════════════════

def download_batch(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Download OHLCV for a batch of tickers."""
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
        print(f"    Batch download error: {e}")
        return {}

    results = {}
    if len(tickers) == 1:
        ticker = tickers[0]
        if not data.empty and len(data) > 50:
            results[ticker] = data
    else:
        for ticker in tickers:
            try:
                df = data[ticker].dropna(how="all")
                if not df.empty and len(df) > 50:
                    results[ticker] = df
            except (KeyError, Exception):
                pass

    return results


def download_all_data(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Download data for all tickers in batches with random delays to avoid rate limits."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=HISTORY_YEARS * 365)).strftime("%Y-%m-%d")

    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    all_data = {}
    failed_batches = []

    print(f"\nDownloading {len(tickers)} stocks in {len(batches)} batches "
          f"({HISTORY_YEARS} years of daily data each)...")
    print(f"  Using random delays (2-6s) between batches to avoid rate limits.\n")

    for idx, batch in enumerate(batches):
        pct = (idx + 1) / len(batches) * 100
        sys.stdout.write(f"\r  Batch {idx+1}/{len(batches)} ({pct:.0f}%) — "
                         f"{len(all_data)} tickers loaded so far")
        sys.stdout.flush()

        batch_data = download_batch(batch, start_date, end_date)

        if not batch_data:
            failed_batches.append(batch)
        else:
            all_data.update(batch_data)

        if idx < len(batches) - 1:
            delay = random.uniform(2.0, 6.0)
            time.sleep(delay)

    # ── Retry failed batches with longer delays ──
    if failed_batches:
        print(f"\n\n  Retrying {len(failed_batches)} failed batch(es) with longer delays...")
        for idx, batch in enumerate(failed_batches):
            delay = random.uniform(8.0, 15.0)
            time.sleep(delay)
            sys.stdout.write(f"\r  Retry {idx+1}/{len(failed_batches)}...")
            sys.stdout.flush()

            batch_data = download_batch(batch, start_date, end_date)
            if batch_data:
                all_data.update(batch_data)

    print(f"\n  Successfully downloaded {len(all_data)} stocks with sufficient data")
    return all_data


# ══════════════════════════════════════════════════════════════
#  STEP 3: Screen one stock
# ══════════════════════════════════════════════════════════════

def analyze_stock(ticker: str, df: pd.DataFrame) -> dict | None:
    """
    1. Prep daily data
    2. Check price/volume on daily bars
    3. Resample to quarterly bars
    4. Compute RSI, Stoch, SMI on quarterly bars
    5. Apply filter + crossover signal logic
    """

    # ── Flatten & standardize columns ──
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)
    df.columns = [c.lower().strip() for c in df.columns]

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(df.columns)):
        return None

    df = df.dropna(subset=["close"]).copy()

    # ── Price & volume check on daily data ──
    latest_price = df["close"].iloc[-1]
    avg_volume = df["volume"].tail(20).mean()
    if latest_price < MIN_PRICE or avg_volume < MIN_AVG_VOLUME:
        return None

    # ── Resample daily → quarterly ──
    qdf = resample_ohlcv(df, 'QE')

    min_quarters_needed = RSI_PERIOD + RSI_SIGNAL_PERIOD + 2
    if len(qdf) < min_quarters_needed:
        return None

    # ── Compute indicators on QUARTERLY bars ──
    qdf = compute_all(
        qdf,
        rsi_period=RSI_PERIOD, rsi_signal_period=RSI_SIGNAL_PERIOD,
        stoch_k=STOCH_K_PERIOD, stoch_slow_k=STOCH_SLOW_K,
        stoch_slow_d=STOCH_SLOW_D,
        smi_period=SMI_PERIOD, smi_smooth1=SMI_SMOOTH1,
        smi_smooth2=SMI_SMOOTH2,
    )

    # ── Validate latest values are not NaN ──
    for col in ['RSI', 'RSI_signal', 'K', 'D', 'SMI']:
        if pd.isna(qdf[col].iloc[-1]) or pd.isna(qdf[col].iloc[-2]):
            return None

    # ── Current quarterly values ──
    current_smi       = qdf['SMI'].iloc[-1]
    current_k         = qdf['K'].iloc[-1]
    current_d         = qdf['D'].iloc[-1]
    current_stoch_avg = (current_k + current_d) / 2.0
    current_rsi       = qdf['RSI'].iloc[-1]
    current_rsi_sig   = qdf['RSI_signal'].iloc[-1]

    # ── Previous quarter values (for crossover detection) ──
    prev_k       = qdf['K'].iloc[-2]
    prev_d       = qdf['D'].iloc[-2]
    prev_rsi     = qdf['RSI'].iloc[-2]
    prev_rsi_sig = qdf['RSI_signal'].iloc[-2]

    latest_quarter_end = qdf.index[-1].strftime("%Y-%m-%d")

    # ══════════════════════════════════════════
    #  FILTER: at least one of three must be true
    # ══════════════════════════════════════════
    smi_below   = current_smi < SMI_THRESHOLD
    stoch_below = current_stoch_avg < STOCH_AVG_THRESHOLD
    rsi_below   = current_rsi < RSI_AVG_THRESHOLD

    if not (smi_below or stoch_below or rsi_below):
        return None

    # ══════════════════════════════════════════
    #  SIGNAL: at least one crossover
    # ══════════════════════════════════════════
    stoch_cross = (prev_k <= prev_d and current_k > current_d)
    rsi_cross   = (prev_rsi <= prev_rsi_sig and current_rsi > current_rsi_sig)

    if not (stoch_cross or rsi_cross):
        return None

    # ── Build result ──
    signals = []
    if stoch_cross:
        signals.append("Stoch %K > %D")
    if rsi_cross:
        signals.append("RSI > Signal")

    return {
        "Ticker":     ticker,
        "Price":      round(latest_price, 2),
        "Quarter":    latest_quarter_end,
        "Qtrs":       len(qdf),
        "SMI":        round(current_smi, 2),
        "Stoch %K":   round(current_k, 2),
        "Stoch %D":   round(current_d, 2),
        "Stoch Avg":  round(current_stoch_avg, 2),
        "RSI":        round(current_rsi, 2),
        "RSI Sig":    round(current_rsi_sig, 2),
        "Crossover":  ", ".join(signals),
    }


# ══════════════════════════════════════════════════════════════
#  STEP 4: Run the full screen
# ══════════════════════════════════════════════════════════════

def run_screen():
    print("=" * 70)
    print("  STOCK MOMENTUM SCREENER — QUARTERLY INDICATORS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    # ── Choose exchange(s) ──
    exchanges = prompt_exchange()
    label = " + ".join(exchanges)

    print()
    print("  How it works:")
    print(f"    1. Download daily OHLCV data for all {label} stocks")
    print("    2. Resample into quarterly candles (QE)")
    print("    3. Compute RSI, Stochastic Slow, SMI on quarterly bars")
    print("    4. Apply filters + crossover signals")
    print()
    print("  Filters (on quarterly bars):")
    print(f"    SMI < {SMI_THRESHOLD}")
    print(f"    Stochastic Slow avg < {STOCH_AVG_THRESHOLD}")
    print(f"    RSI < {RSI_AVG_THRESHOLD}")
    print(f"    + Crossover: Stoch %K > %D  or  RSI > RSI Signal")
    print()

    # ── Get ticker list ──
    tickers = get_stock_tickers(exchanges)

    # ── Download daily data ──
    all_data = download_all_data(tickers)

    # ── Screen each stock ──
    print(f"\nResampling to quarterly & screening {len(all_data)} stocks...")
    results = []

    for i, (ticker, df) in enumerate(all_data.items()):
        if (i + 1) % 100 == 0:
            sys.stdout.write(f"\r  Analyzed {i+1}/{len(all_data)}...")
            sys.stdout.flush()

        result = analyze_stock(ticker, df)
        if result is not None:
            results.append(result)

    print(f"\r  Analyzed {len(all_data)}/{len(all_data)} — done!")

    # ── Display results ──
    print()
    print("=" * 70)
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values("SMI", ascending=True)

        print(f"  {len(results)} stock(s) on {label} PASSED all criteria "
              f"(quarterly indicators):")
        print("=" * 70)
        print()
        print(results_df.to_string(index=False))
        print()

        # Save to CSV
        exch_tag = "_".join(e.lower() for e in exchanges)
        out_file = f"stock_screen_{exch_tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        results_df.to_csv(out_file, index=False)
        print(f"  Results saved to: {out_file}")
    else:
        print("  No stocks met all filter + signal conditions this quarter.")
        print("=" * 70)

    print()
    return results


if __name__ == "__main__":
    run_screen()