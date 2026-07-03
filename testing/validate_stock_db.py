"""
validate_stock_db.py — Single-stock indicator validator (database version)
===========================================================================
Identical to validate_stock.py but pulls OHLCV data from the local
PostgreSQL database instead of downloading from Yahoo Finance.

This is faster (no network call) and uses the exact same data that
the screener will use, so results are guaranteed to match.

Usage:
    python validate_stock_db.py NFLX
    python validate_stock_db.py NFLX --end-date today
    python validate_stock_db.py NFLX --end-date 2025-01-15
    python validate_stock_db.py NFLX --end-date "Q1 2026"
    python validate_stock_db.py NFLX --end-date "March 2025"

Compare the printed values to Barchart's quarterly chart view with:
    SMI(10, 3)          → SMI column
    STOCHS(14, 3, 3)    → %K and %D columns
    RSI(14, MA, 5)       → RSI and RSI_signal columns
"""

import warnings
warnings.filterwarnings("ignore")

import argparse
import sys
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from data.db import get_session
from data.models import DailyOHLCV, Ticker
from indicators import resample_ohlcv, compute_all


# ──────────────────────────────────────────────────────────────
#  CONFIGURATION (must match etf_screener.py / validate_stock.py)
# ──────────────────────────────────────────────────────────────

RSI_PERIOD          = 14
RSI_SIGNAL_PERIOD   = 5
STOCH_K_PERIOD      = 14
STOCH_SLOW_K        = 3
STOCH_SLOW_D        = 3
SMI_PERIOD          = 10
SMI_SMOOTH1         = 3
SMI_SMOOTH2         = 3

SMI_THRESHOLD       = -40
STOCH_AVG_THRESHOLD = 35
RSI_AVG_THRESHOLD   = 30


# ──────────────────────────────────────────────────────────────
#  DATE PARSING (same as validate_stock.py)
# ──────────────────────────────────────────────────────────────

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


def parse_end_date(raw: str) -> datetime:
    """
    Parse flexible date input:
        'today'           → now
        '2025-01-15'      → Jan 15 2025
        '01/15/2025'      → Jan 15 2025
        'Q1 2026'         → Mar 31 2026
        'Q4 2024'         → Dec 31 2024
        'March 2025'      → Mar 31 2025
        'Jan 2025'        → Jan 31 2025
    """
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

    print(f"  Could not parse date '{raw}'. Use formats like:")
    print(f"    today, 2025-01-15, Q1 2026, March 2025")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────
#  DATA LOADING (from PostgreSQL)
# ──────────────────────────────────────────────────────────────

def load_daily_from_db(ticker: str, end_date: datetime) -> pd.DataFrame:
    """
    Load daily OHLCV from the database for a given ticker,
    ending at end_date.

    Returns a DataFrame with DatetimeIndex and columns:
        open, high, low, close, volume
    """
    end_dt = end_date.date() if isinstance(end_date, datetime) else end_date

    with get_session() as session:
        # Check ticker exists
        ticker_row = session.query(Ticker).filter_by(symbol=ticker.upper()).first()
        if not ticker_row:
            print(f"  ERROR: {ticker} not found in tickers table.")
            print(f"  Run refresh_universe() first to populate the ticker universe.")
            sys.exit(1)

        # Query OHLCV
        rows = (
            session.query(
                DailyOHLCV.date,
                DailyOHLCV.open,
                DailyOHLCV.high,
                DailyOHLCV.low,
                DailyOHLCV.close,
                DailyOHLCV.volume,
            )
            .filter(DailyOHLCV.symbol == ticker.upper())
            .filter(DailyOHLCV.date <= end_dt)
            .order_by(DailyOHLCV.date)
            .all()
        )

    if not rows:
        print(f"  ERROR: No OHLCV data found for {ticker} in the database.")
        print(f"  Run refresh_ohlcv() first to download price data.")
        sys.exit(1)

    df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')

    # Convert Decimal types to float
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
    df['volume'] = df['volume'].astype(int)

    print(f"  Loaded {len(df)} daily bars from database: {df.index[0].date()} → {df.index[-1].date()}")
    return df


# ──────────────────────────────────────────────────────────────
#  SCREENER LOGIC (mirrors etf_screener.py exactly)
# ──────────────────────────────────────────────────────────────

def evaluate_screen(qdf: pd.DataFrame) -> dict:
    """
    Apply the same filter + signal logic as etf_screener.py.
    Returns a dict with all the details.
    """
    current = qdf.iloc[-1]
    prev = qdf.iloc[-2]

    current_smi       = current['SMI']
    current_k         = current['K']
    current_d         = current['D']
    current_stoch_avg = (current_k + current_d) / 2.0
    current_rsi       = current['RSI']
    current_rsi_sig   = current['RSI_signal']

    prev_k       = prev['K']
    prev_d       = prev['D']
    prev_rsi     = prev['RSI']
    prev_rsi_sig = prev['RSI_signal']

    smi_below   = current_smi < SMI_THRESHOLD
    stoch_below = current_stoch_avg < STOCH_AVG_THRESHOLD
    rsi_below   = current_rsi < RSI_AVG_THRESHOLD

    filter_pass = smi_below or stoch_below or rsi_below
    filters_met = sum([smi_below, stoch_below, rsi_below])

    stoch_cross = (prev_k <= prev_d) and (current_k > current_d)
    rsi_cross   = (prev_rsi <= prev_rsi_sig) and (current_rsi > current_rsi_sig)

    signal_pass = stoch_cross or rsi_cross

    signals = []
    if stoch_cross:
        signals.append("Stoch %K crossed above %D")
    if rsi_cross:
        signals.append("RSI crossed above Signal")

    return {
        'filter_pass':  filter_pass,
        'signal_pass':  signal_pass,
        'flagged':      filter_pass and signal_pass,
        'filters_met':  filters_met,
        'smi_below':    smi_below,
        'stoch_below':  stoch_below,
        'rsi_below':    rsi_below,
        'stoch_cross':  stoch_cross,
        'rsi_cross':    rsi_cross,
        'signals':      signals,
        'current_smi':       round(current_smi, 2),
        'current_k':         round(current_k, 2),
        'current_d':         round(current_d, 2),
        'current_stoch_avg': round(current_stoch_avg, 2),
        'current_rsi':       round(current_rsi, 2),
        'current_rsi_sig':   round(current_rsi_sig, 2),
    }


# ──────────────────────────────────────────────────────────────
#  DISPLAY
# ──────────────────────────────────────────────────────────────

def print_bar_table(bars: pd.DataFrame, label: str, n: int = 8):
    """Print last n bars with indicator values."""
    cols = ['close', 'SMI', 'K', 'D', 'RSI', 'RSI_signal']
    display = bars[cols].tail(n).copy()
    display.index = display.index.strftime('%Y-%m-%d')
    display = display.round(2)

    print(f"\n  ┌─ {label} — last {n} bars {'─' * 40}")
    print(f"  │")
    header = f"  │  {'Date':>12}  {'Close':>8}  {'SMI':>8}  {'%K':>8}  {'%D':>8}  {'RSI':>8}  {'RSI Sig':>8}"
    print(header)
    print(f"  │  {'─'*12}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*8}")

    for date, row in display.iterrows():
        print(f"  │  {date:>12}  {row['close']:>8.2f}  {row['SMI']:>8.2f}  "
              f"{row['K']:>8.2f}  {row['D']:>8.2f}  {row['RSI']:>8.2f}  "
              f"{row['RSI_signal']:>8.2f}")

    print(f"  └{'─' * 74}")


def print_screen_result(result: dict, ticker: str, quarter_date: str):
    """Print the screener evaluation."""
    flagged = result['flagged']

    print(f"\n  ╔{'═' * 60}╗")
    if flagged:
        print(f"  ║  ✅  {ticker} → FLAGGED by screener{' ' * (28)}║")
    else:
        print(f"  ║  ❌  {ticker} → NOT flagged{' ' * (40 - len(ticker))}║")
    print(f"  ║  Quarter ending: {quarter_date}{' ' * (42 - len(quarter_date))}║")
    print(f"  ╠{'═' * 60}╣")

    def check(val): return "✓ YES" if val else "✗ no "

    print(f"  ║                                                            ║")
    print(f"  ║  FILTERS (need ≥1 true):                                   ║")
    print(f"  ║    SMI < {SMI_THRESHOLD:>4}        : {check(result['smi_below'])}   "
          f"(SMI = {result['current_smi']:>8.2f})       ║")
    print(f"  ║    Stoch avg < {STOCH_AVG_THRESHOLD:>2}   : {check(result['stoch_below'])}   "
          f"(avg = {result['current_stoch_avg']:>8.2f})       ║")
    print(f"  ║    RSI < {RSI_AVG_THRESHOLD:>2}          : {check(result['rsi_below'])}   "
          f"(RSI = {result['current_rsi']:>8.2f})       ║")
    print(f"  ║    → {result['filters_met']}/3 filters met   "
          f"{'  PASS' if result['filter_pass'] else '  FAIL'}"
          f"{' ' * 25}║")

    print(f"  ║                                                            ║")
    print(f"  ║  SIGNALS (need ≥1 crossover):                              ║")
    print(f"  ║    Stoch %K > %D  : {check(result['stoch_cross'])}"
          f"   (%K={result['current_k']:>6.2f}, %D={result['current_d']:>6.2f})    ║")
    print(f"  ║    RSI > Signal   : {check(result['rsi_cross'])}"
          f"   (RSI={result['current_rsi']:>6.2f}, Sig={result['current_rsi_sig']:>6.2f})   ║")
    if result['signals']:
        print(f"  ║    → {', '.join(result['signals']):<54}║")
    else:
        print(f"  ║    → No crossovers detected{' ' * 32}║")

    print(f"  ║                                                            ║")
    print(f"  ╚{'═' * 60}╝")


def print_barchart_comparison(qdf: pd.DataFrame):
    """Print the exact values to compare against Barchart."""
    curr = qdf.iloc[-1]
    print(f"\n  BARCHART COMPARISON — match these to your quarterly chart:")
    print(f"  ─────────────────────────────────────────────────────────")
    print(f"    STOMOMIX(10, 3)     →  SMI      = {curr['SMI']:.2f}")
    print(f"    STOCHS(14, 3, 3)    →  %K       = {curr['K']:.2f}")
    print(f"                        →  %D       = {curr['D']:.2f}")
    print(f"    RSI(14, MA, 5)      →  RSI      = {curr['RSI']:.2f}")
    print(f"                        →  RSI avg  = {curr['RSI_signal']:.2f}")
    print(f"    Close               →           = {curr['close']:.2f}")
    print(f"    Quarter ending      →           = {qdf.index[-1].strftime('%Y-%m-%d')}")
    print()


# ──────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='Validate indicator values for a single stock (from database)')
    ap.add_argument('ticker', help='Ticker symbol (e.g. NFLX, SPY, XLE)')
    ap.add_argument('--end-date', default='today',
                    help='Analysis end date: today, 2025-01-15, "Q1 2026", "March 2025"')
    ap.add_argument('--show-monthly', action='store_true',
                    help='Also show monthly bar indicators')
    ap.add_argument('--show-weekly', action='store_true',
                    help='Also show weekly bar indicators')
    ap.add_argument('--bars', type=int, default=8,
                    help='Number of recent bars to display (default: 8)')
    args = ap.parse_args()

    ticker = args.ticker.upper()
    end_date = parse_end_date(args.end_date)

    print()
    print("=" * 70)
    print(f"  STOCK VALIDATOR (DB) — {ticker}")
    print(f"  Analysis end date: {end_date.strftime('%Y-%m-%d')}")
    print(f"  Data source: PostgreSQL (investment_screeners)")
    print("=" * 70)

    # ── Load from database ──
    daily = load_daily_from_db(ticker, end_date)

    if len(daily) < 200:
        print(f"  ERROR: Only {len(daily)} daily bars — need at least 200.")
        sys.exit(1)

    # ── Ticker info from database ──
    with get_session() as session:
        ticker_row = session.query(Ticker).filter_by(symbol=ticker).first()
        if ticker_row:
            print(f"  Name:     {ticker_row.name or '?'}")
            print(f"  Type:     {ticker_row.asset_type}")
            print(f"  Exchange: {ticker_row.exchange or 'N/A'}")
            print(f"  Source:   {ticker_row.source or 'N/A'}")

    # ── Quarterly (primary) ──
    qdf = resample_ohlcv(daily, 'QE')
    min_bars = RSI_PERIOD + RSI_SIGNAL_PERIOD + 2
    if len(qdf) < min_bars:
        print(f"  ERROR: Only {len(qdf)} quarterly bars — need at least {min_bars}.")
        sys.exit(1)

    qdf = compute_all(
        qdf,
        rsi_period=RSI_PERIOD, rsi_signal_period=RSI_SIGNAL_PERIOD,
        stoch_k=STOCH_K_PERIOD, stoch_slow_k=STOCH_SLOW_K, stoch_slow_d=STOCH_SLOW_D,
        smi_period=SMI_PERIOD, smi_smooth1=SMI_SMOOTH1, smi_smooth2=SMI_SMOOTH2,
    )

    print_bar_table(qdf, "QUARTERLY BARS", args.bars)
    print_barchart_comparison(qdf)

    # ── Screener evaluation ──
    if pd.notna(qdf.iloc[-1]['SMI']) and pd.notna(qdf.iloc[-2]['SMI']):
        result = evaluate_screen(qdf)
        quarter_str = qdf.index[-1].strftime('%Y-%m-%d')
        print_screen_result(result, ticker, quarter_str)
    else:
        print("  WARNING: Indicator values are NaN — not enough history for evaluation.")

    # ── Optional: monthly ──
    if args.show_monthly:
        mdf = resample_ohlcv(daily, 'ME')
        if len(mdf) >= min_bars:
            mdf = compute_all(
                mdf,
                rsi_period=RSI_PERIOD, rsi_signal_period=RSI_SIGNAL_PERIOD,
                stoch_k=STOCH_K_PERIOD, stoch_slow_k=STOCH_SLOW_K,
                stoch_slow_d=STOCH_SLOW_D,
                smi_period=SMI_PERIOD, smi_smooth1=SMI_SMOOTH1,
                smi_smooth2=SMI_SMOOTH2,
            )
            print_bar_table(mdf, "MONTHLY BARS", args.bars)

    # ── Optional: weekly ──
    if args.show_weekly:
        wdf = resample_ohlcv(daily, 'W-FRI')
        if len(wdf) >= min_bars:
            wdf = compute_all(
                wdf,
                rsi_period=RSI_PERIOD, rsi_signal_period=RSI_SIGNAL_PERIOD,
                stoch_k=STOCH_K_PERIOD, stoch_slow_k=STOCH_SLOW_K,
                stoch_slow_d=STOCH_SLOW_D,
                smi_period=SMI_PERIOD, smi_smooth1=SMI_SMOOTH1,
                smi_smooth2=SMI_SMOOTH2,
            )
            print_bar_table(wdf, "WEEKLY BARS", args.bars)

    print()


if __name__ == '__main__':
    main()