"""
backtest.py — Historical performance check
=============================================
Runs the screener as of a historical date, finds all stocks that
triggered the filter + signal, then measures how they actually
performed from that point to today.

Shows the top 10 best and bottom 10 worst performers.

Usage:
    python backtest.py --date "Q1 2015"
    python backtest.py --date "Q4 2008"
    python backtest.py --date "March 2020"
    python backtest.py --date "2015-06-30"
    python backtest.py --date "Q1 2015" --top 20 --bottom 20

Notes:
    - Survivorship bias warning: this only tests stocks that are STILL
      listed today. Stocks that were delisted/bankrupt after the signal
      date are missing, which skews results positively.
    - The screener evaluates the most recent COMPLETED quarter as of
      your chosen date.
    - Performance = total return from that quarter's closing price to
      the most recent closing price.

Dependencies:
    pip install yfinance pandas requests
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
import argparse
import re
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
#  DATE PARSING (reused from validate_stock.py)
# ══════════════════════════════════════════════════════════════

QUARTER_MAP = {
    'Q1': '03-31', 'Q2': '06-30', 'Q3': '09-30', 'Q4': '12-31',
}
MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9,
    'oct': 10, 'nov': 11, 'dec': 12,
}


def parse_date(raw: str) -> datetime:
    raw = raw.strip()
    if raw.lower() == 'today':
        return datetime.now()

    m = re.match(r'^(Q[1-4])\s+(\d{4})$', raw, re.IGNORECASE)
    if m:
        q, year = m.group(1).upper(), int(m.group(2))
        return datetime.strptime(f"{year}-{QUARTER_MAP[q]}", "%Y-%m-%d")

    m = re.match(r'^([A-Za-z]+)\s+(\d{4})$', raw)
    if m:
        month_str, year = m.group(1).lower(), int(m.group(2))
        if month_str in MONTH_MAP:
            month = MONTH_MAP[month_str]
            if month == 12:
                return datetime(year, 12, 31)
            else:
                return datetime(year, month + 1, 1) - timedelta(days=1)

    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue

    print(f"  Could not parse date '{raw}'.")
    print(f"  Examples: Q1 2015, Q4 2008, March 2020, 2015-06-30")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════
#  EXCHANGE PROMPT
# ══════════════════════════════════════════════════════════════

def prompt_exchange() -> list[str]:
    print("  Which exchange(s) do you want to backtest?")
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


# ══════════════════════════════════════════════════════════════
#  UNIVERSE
# ══════════════════════════════════════════════════════════════

def get_stock_tickers(exchanges: list[str]) -> list[str]:
    url = "https://www.alphavantage.co/query?function=LISTING_STATUS&apikey=demo"
    label = " + ".join(exchanges)
    print(f"  Fetching {label} stock listing from Alpha Vantage...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        stocks = df[df["assetType"] == "Stock"].copy()
        stocks = stocks[stocks["exchange"].isin(exchanges)]
        if "status" in stocks.columns:
            stocks = stocks[stocks["status"] == "Active"]
        tickers = [t for t in stocks["symbol"].tolist()
                   if isinstance(t, str) and len(t) <= 5 and t.isalpha()]
        print(f"  Found {len(tickers)} stocks on {label}")
        return tickers
    except Exception as e:
        print(f"  Alpha Vantage failed: {e}")
        # Small fallback for testing
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
            "JPM", "BAC", "WFC", "JNJ", "UNH", "PFE", "XOM", "CVX",
            "WMT", "PG", "KO", "DIS", "NFLX", "INTC", "AMD", "CRM",
            "BA", "CAT", "GE", "HD", "MCD", "NKE", "V", "MA",
        ]


# ══════════════════════════════════════════════════════════════
#  BATCH DOWNLOAD — full history (signal date through today)
# ══════════════════════════════════════════════════════════════

def download_batch(tickers, start, end):
    try:
        data = yf.download(
            tickers=tickers, start=start, end=end,
            interval="1d", group_by="ticker",
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception as e:
        print(f"    Batch error: {e}")
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


def download_all(tickers, signal_date):
    """
    Download from (signal_date - HISTORY_YEARS) through today.
    We need pre-signal history for indicator warmup, plus post-signal
    data to measure performance.
    """
    start = (signal_date - timedelta(days=HISTORY_YEARS * 365)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    all_data = {}
    failed = []

    print(f"\n  Downloading {len(tickers)} stocks in {len(batches)} batches...")
    print(f"  Date range: {start} → {end}")
    print(f"  Random delays (2-6s) between batches.\n")

    for idx, batch in enumerate(batches):
        pct = (idx + 1) / len(batches) * 100
        sys.stdout.write(f"\r  Batch {idx+1}/{len(batches)} ({pct:.0f}%) — "
                         f"{len(all_data)} loaded")
        sys.stdout.flush()

        batch_data = download_batch(batch, start, end)
        if not batch_data:
            failed.append(batch)
        else:
            all_data.update(batch_data)

        if idx < len(batches) - 1:
            time.sleep(random.uniform(2.0, 6.0))

    # Retry failed
    if failed:
        print(f"\n\n  Retrying {len(failed)} failed batch(es)...")
        for idx, batch in enumerate(failed):
            time.sleep(random.uniform(8.0, 15.0))
            sys.stdout.write(f"\r  Retry {idx+1}/{len(failed)}...")
            sys.stdout.flush()
            batch_data = download_batch(batch, start, end)
            if batch_data:
                all_data.update(batch_data)

    print(f"\n  Downloaded {len(all_data)} stocks with data\n")
    return all_data


# ══════════════════════════════════════════════════════════════
#  SCREEN AS OF A HISTORICAL DATE
# ══════════════════════════════════════════════════════════════

def screen_at_date(ticker: str, daily: pd.DataFrame,
                   signal_date: pd.Timestamp) -> dict | None:
    """
    Run the screener as of signal_date:
      1. Truncate daily data to only include bars <= signal_date
      2. Resample to quarterly, compute indicators
      3. Check filter + signal on the last completed quarter
      4. If it passes, also compute return from signal to today

    Returns a result dict or None.
    """
    # ── Standardize columns ──
    if isinstance(daily.columns, pd.MultiIndex):
        daily.columns = daily.columns.get_level_values(-1)
    daily.columns = [c.lower().strip() for c in daily.columns]

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(daily.columns)):
        return None

    daily = daily.dropna(subset=["close"]).copy()
    if not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily.index)

    # ── Split: data up to signal date (for screening) ──
    daily_to_signal = daily[daily.index <= signal_date]
    if len(daily_to_signal) < 400:
        return None

    # Price/volume check as of signal date
    signal_price = daily_to_signal["close"].iloc[-1]
    avg_vol = daily_to_signal["volume"].tail(20).mean()
    if signal_price < MIN_PRICE or avg_vol < MIN_AVG_VOLUME:
        return None

    # ── Resample to quarterly (up to signal date only) ──
    qdf = resample_ohlcv(daily_to_signal, 'QE')

    min_bars = RSI_PERIOD + RSI_SIGNAL_PERIOD + 2
    if len(qdf) < min_bars:
        return None

    # ── Compute indicators ──
    qdf = compute_all(
        qdf,
        rsi_period=RSI_PERIOD, rsi_signal_period=RSI_SIGNAL_PERIOD,
        stoch_k=STOCH_K_PERIOD, stoch_slow_k=STOCH_SLOW_K,
        stoch_slow_d=STOCH_SLOW_D,
        smi_period=SMI_PERIOD, smi_smooth1=SMI_SMOOTH1,
        smi_smooth2=SMI_SMOOTH2,
    )

    # ── Validate ──
    for col in ['RSI', 'RSI_signal', 'K', 'D', 'SMI']:
        if pd.isna(qdf[col].iloc[-1]) or pd.isna(qdf[col].iloc[-2]):
            return None

    curr = qdf.iloc[-1]
    prev = qdf.iloc[-2]

    # ── FILTER ──
    current_smi       = curr['SMI']
    current_k         = curr['K']
    current_d         = curr['D']
    current_stoch_avg = (current_k + current_d) / 2.0
    current_rsi       = curr['RSI']
    current_rsi_sig   = curr['RSI_signal']

    smi_below   = current_smi < SMI_THRESHOLD
    stoch_below = current_stoch_avg < STOCH_AVG_THRESHOLD
    rsi_below   = current_rsi < RSI_AVG_THRESHOLD

    if not (smi_below or stoch_below or rsi_below):
        return None

    # ── SIGNAL ──
    stoch_cross = (prev['K'] <= prev['D']) and (current_k > current_d)
    rsi_cross   = (prev['RSI'] <= prev['RSI_signal']) and (current_rsi > current_rsi_sig)

    if not (stoch_cross or rsi_cross):
        return None

    # ── PERFORMANCE: signal quarter close → today's close ──
    signal_quarter_end = qdf.index[-1]
    entry_price = curr['close']

    # Get the most recent close from the full dataset
    today_price = daily["close"].iloc[-1]
    today_date = daily.index[-1]

    if entry_price <= 0 or pd.isna(entry_price):
        return None

    total_return_pct = ((today_price - entry_price) / entry_price) * 100

    # Annualized return
    days_held = (today_date - signal_quarter_end).days
    years_held = max(days_held / 365.25, 0.01)
    if total_return_pct > -100:
        annualized = ((1 + total_return_pct / 100) ** (1 / years_held) - 1) * 100
    else:
        annualized = -100.0

    signals = []
    if stoch_cross:
        signals.append("Stoch %K > %D")
    if rsi_cross:
        signals.append("RSI > Signal")

    return {
        "Ticker":          ticker,
        "Entry Date":      signal_quarter_end.strftime("%Y-%m-%d"),
        "Entry Price":     round(entry_price, 2),
        "Current Price":   round(today_price, 2),
        "Total Return %":  round(total_return_pct, 1),
        "Annualized %":    round(annualized, 1),
        "Years Held":      round(years_held, 1),
        "SMI":             round(current_smi, 2),
        "Stoch Avg":       round(current_stoch_avg, 2),
        "RSI":             round(current_rsi, 2),
        "Signal":          ", ".join(signals),
    }


# ══════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════

def print_results(results_df, signal_date_str, n_top, n_bottom):
    total = len(results_df)
    avg_return = results_df["Total Return %"].mean()
    median_return = results_df["Total Return %"].median()
    pct_positive = (results_df["Total Return %"] > 0).sum() / total * 100

    print(f"\n{'=' * 78}")
    print(f"  BACKTEST RESULTS — Screener triggered as of {signal_date_str}")
    print(f"{'=' * 78}")
    print(f"  Stocks that passed filter + signal:  {total}")
    print(f"  Average total return:                {avg_return:>+.1f}%")
    print(f"  Median total return:                 {median_return:>+.1f}%")
    print(f"  % positive:                          {pct_positive:.0f}%")

    # ── Top N ──
    top = results_df.head(n_top)
    print(f"\n  {'─' * 74}")
    print(f"  🏆 TOP {n_top} BEST PERFORMERS")
    print(f"  {'─' * 74}")
    display_cols = ["Ticker", "Entry Date", "Entry Price", "Current Price",
                    "Total Return %", "Annualized %", "Signal"]
    print(top[display_cols].to_string(index=False))

    # ── Bottom N ──
    bottom = results_df.tail(n_bottom)
    print(f"\n  {'─' * 74}")
    print(f"  📉 BOTTOM {n_bottom} WORST PERFORMERS")
    print(f"  {'─' * 74}")
    print(bottom[display_cols].to_string(index=False))

    # ── Distribution ──
    print(f"\n  {'─' * 74}")
    print(f"  RETURN DISTRIBUTION")
    print(f"  {'─' * 74}")
    bins = [
        ("  > +500%",  results_df["Total Return %"] > 500),
        ("  +200–500%", (results_df["Total Return %"] > 200) & (results_df["Total Return %"] <= 500)),
        ("  +100–200%", (results_df["Total Return %"] > 100) & (results_df["Total Return %"] <= 200)),
        ("  +50–100%",  (results_df["Total Return %"] > 50)  & (results_df["Total Return %"] <= 100)),
        ("  +0–50%",    (results_df["Total Return %"] > 0)   & (results_df["Total Return %"] <= 50)),
        ("  -0–50%",    (results_df["Total Return %"] <= 0)  & (results_df["Total Return %"] > -50)),
        ("  < -50%",    results_df["Total Return %"] <= -50),
    ]
    for label, mask in bins:
        count = mask.sum()
        bar = "█" * count
        print(f"  {label:>14}  {count:>4}  {bar}")

    print()


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='Backtest: how did screener picks perform from a historical date to today?')
    ap.add_argument('--date', required=True,
                    help='Signal date: "Q1 2015", "Q4 2008", "March 2020", "2015-06-30"')
    ap.add_argument('--top', type=int, default=10,
                    help='Number of top performers to show (default: 10)')
    ap.add_argument('--bottom', type=int, default=10,
                    help='Number of bottom performers to show (default: 10)')
    ap.add_argument('--out', default=None,
                    help='Output CSV filename (default: auto-generated)')
    args = ap.parse_args()

    signal_date = parse_date(args.date)
    signal_ts = pd.Timestamp(signal_date)

    print()
    print("=" * 78)
    print("  HISTORICAL BACKTEST")
    print(f"  Signal date:  {signal_date.strftime('%Y-%m-%d')}")
    print(f"  Measuring performance from signal → {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 78)
    print()
    print("  ⚠️  SURVIVORSHIP BIAS WARNING: This only tests stocks still listed today.")
    print("     Stocks that went bankrupt or were delisted after the signal date are")
    print("     missing, which makes results look better than they actually were.")
    print()

    # ── Exchange selection ──
    exchanges = prompt_exchange()

    # ── Get tickers ──
    print()
    tickers = get_stock_tickers(exchanges)

    # ── Download ──
    all_data = download_all(tickers, signal_date)

    # ── Screen ──
    print(f"  Screening as of {signal_date.strftime('%Y-%m-%d')}...")
    results = []
    for i, (ticker, df) in enumerate(all_data.items()):
        if (i + 1) % 200 == 0:
            sys.stdout.write(f"\r  Screened {i+1}/{len(all_data)}...")
            sys.stdout.flush()

        result = screen_at_date(ticker, df, signal_ts)
        if result is not None:
            results.append(result)

    print(f"\r  Screened {len(all_data)}/{len(all_data)} — done!")

    if not results:
        print("\n  No stocks triggered the screener as of that date.")
        print("  Try a different date (quarters ending in market downturns work best).")
        return

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("Total Return %", ascending=False).reset_index(drop=True)

    # ── Display ──
    print_results(results_df, signal_date.strftime('%Y-%m-%d'),
                  args.top, args.bottom)

    # ── Save ──
    if args.out:
        out_file = args.out
    else:
        date_tag = signal_date.strftime('%Y%m%d')
        exch_tag = "_".join(e.lower() for e in exchanges)
        out_file = f"backtest_{exch_tag}_{date_tag}.csv"

    results_df.to_csv(out_file, index=False)
    print(f"  Full results saved → {out_file}")
    print(f"  ({len(results_df)} stocks)")
    print()


if __name__ == "__main__":
    main()