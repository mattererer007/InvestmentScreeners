"""
Standalone ETF Screener — Quarterly Indicators
================================================
Resamples daily OHLCV data into QUARTERLY candles, then computes
RSI, Stochastic Slow, and SMI on those quarterly bars.

Each "bar" in the indicator series = one calendar quarter of price
action (Open of first day, High of the quarter, Low of the quarter,
Close of last day, sum of Volume).

Dependencies:
    pip install yfinance pandas requests

Data sources (all free, no API key needed):
    1. Alpha Vantage LISTING_STATUS → full list of US-traded ETFs
    2. yfinance → daily OHLCV price history (resampled to quarterly)

FILTER CONDITIONS (all must be true on quarterly view):
    1. Stochastic Momentum Index (SMI) has dropped below -40
    2. Stochastic Slow average (%K + %D)/2 is below 25
    3. Relative Strength Index (RSI) average is below 30

SIGNAL CONDITION (at least one must be true):
    - Stochastic Slow %K has crossed above %D
    - RSI has crossed above its signal (SMA) line

Usage:
    python etf_screener_standalone.py
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import yfinance as yf
import requests
import io
import time
import sys
from datetime import datetime, timedelta


# ══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════

# Indicator periods (applied to QUARTERLY bars)
RSI_PERIOD = 14
RSI_SIGNAL_PERIOD = 5         # SMA of RSI (the "average" / signal line)
STOCH_K_PERIOD = 14            # Stochastic %K lookback (in quarters)
STOCH_SLOW_K = 3               # %K smoothing
STOCH_SLOW_D = 3               # %D smoothing (signal / average line)
SMI_PERIOD = 10                # SMI lookback (in quarters)
SMI_SMOOTH1 = 3                # SMI first EMA smoothing
SMI_SMOOTH2 = 3                # SMI second EMA smoothing

# Thresholds
SMI_THRESHOLD = -40            # SMI must have dropped below this
STOCH_AVG_THRESHOLD = 25       # Stochastic average must be below this
RSI_AVG_THRESHOLD = 30         # RSI must be below this

# Data settings
HISTORY_YEARS = 20             # years of daily data to fetch (need enough
                               # for 14+ quarterly bars after resampling)
MIN_PRICE = 5.0                # skip penny ETFs
MIN_AVG_VOLUME = 100_000      # minimum average daily volume
BATCH_SIZE = 50                # tickers per yfinance download batch


# ══════════════════════════════════════════════════════════════
#  STEP 1: Get the full list of US-traded ETFs
# ══════════════════════════════════════════════════════════════

def get_etf_tickers_alphavantage() -> list[str]:
    """
    Download the full US listing from Alpha Vantage (free, no key needed)
    and filter to ETFs only.
    """
    url = "https://www.alphavantage.co/query?function=LISTING_STATUS&apikey=demo"
    print("Fetching US ETF listing from Alpha Vantage...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        etfs = df[df["assetType"] == "ETF"]["symbol"].tolist()
        etfs = [t for t in etfs if isinstance(t, str) and len(t) <= 6]
        print(f"  Found {len(etfs)} US-traded ETFs")
        return etfs
    except Exception as e:
        print(f"  Alpha Vantage failed: {e}")
        return []


def get_etf_tickers_fallback() -> list[str]:
    """Hardcoded fallback list of ~200 major ETFs."""
    return [
        "SPY", "QQQ", "IWM", "DIA", "EFA", "EEM", "VTI", "VOO",
        "VEA", "VWO", "BND", "AGG", "TLT", "LQD", "HYG", "GLD",
        "SLV", "USO", "XLF", "XLK", "XLE", "XLV", "XLI", "XLY",
        "XLP", "XLU", "XLB", "XLRE", "XLC", "VNQ", "VIG", "SCHD",
        "ARKK", "ARKW", "ARKG", "IEMG", "IJR", "IJH", "IVV",
        "IEFA", "GOVT", "SHY", "IEF", "TIP", "EMB", "JNK",
        "VCIT", "VCSH", "BNDX", "VGK", "VPL", "MCHI", "FXI",
        "EWJ", "EWZ", "EWG", "EWU", "INDA", "KRE", "XBI",
        "SMH", "SOXX", "ITB", "XHB", "IYR", "GDXJ", "GDX",
        "XME", "KWEB", "VTV", "VUG", "MTUM", "QUAL",
        "USMV", "DVY", "HDV", "PFF", "VYM", "SPYG", "SPYV",
        "IWF", "IWD", "IWO", "IWN", "VBK", "VBR", "VO", "MDY",
        "SCHG", "SCHV", "SCHM", "SCHA", "SCHF", "SCHE",
        "ICLN", "TAN", "LIT", "DRIV", "BITO", "MSOS",
        "TQQQ", "SQQQ", "UPRO", "TNA", "SOXL", "FAS",
        "RSP", "NOBL", "SDY", "DGRO", "VDE", "OIH", "XOP",
        "VHT", "IBB", "IHI", "VFH", "VIS", "VCR", "VDC",
        "VAW", "VPU", "VOX", "VXUS", "ACWI",
        "SIL", "COPX", "PICK", "DBA", "DBC", "IAU", "GLDM",
        "VIXY", "SVXY", "JETS", "BLOK", "FINX", "CIBR", "HACK",
        "BOTZ", "ROBO", "AIQ", "IGV", "SKYY", "WCLD", "NVO",
    ]


def get_etf_tickers() -> list[str]:
    tickers = get_etf_tickers_alphavantage()
    if len(tickers) < 50:
        print("  Falling back to hardcoded ETF list")
        tickers = get_etf_tickers_fallback()
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
    """Download data for all tickers in batches."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=HISTORY_YEARS * 365)).strftime("%Y-%m-%d")

    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    all_data = {}

    print(f"\nDownloading {len(tickers)} ETFs in {len(batches)} batches "
          f"({HISTORY_YEARS} years of daily data each)...")

    for idx, batch in enumerate(batches):
        pct = (idx + 1) / len(batches) * 100
        sys.stdout.write(f"\r  Batch {idx+1}/{len(batches)} ({pct:.0f}%) — "
                         f"{len(all_data)} tickers loaded so far")
        sys.stdout.flush()

        batch_data = download_batch(batch, start_date, end_date)
        all_data.update(batch_data)

        if idx < len(batches) - 1:
            time.sleep(0.5)

    print(f"\n  Successfully downloaded {len(all_data)} ETFs with sufficient data")
    return all_data


# ══════════════════════════════════════════════════════════════
#  STEP 3: Resample daily OHLCV → quarterly OHLCV
# ══════════════════════════════════════════════════════════════

def resample_to_quarterly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample daily OHLCV bars into quarterly (calendar quarter) bars.

    Each quarterly bar:
        Open  = first trading day's Open
        High  = highest High of the quarter
        Low   = lowest Low of the quarter
        Close = last trading day's Close
        Volume = sum of all daily Volume
    """
    # Ensure we have a DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    quarterly = df.resample("QE").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    return quarterly


# ══════════════════════════════════════════════════════════════
#  STEP 4: Compute indicators on quarterly bars
# ══════════════════════════════════════════════════════════════

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_stochastic_slow(
    high: pd.Series, low: pd.Series, close: pd.Series,
    k_period: int = 14, slow_k: int = 3, slow_d: int = 3
) -> tuple[pd.Series, pd.Series]:
    """
    Stochastic Slow.
    Returns (%K smoothed, %D signal line).
    """
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()

    fast_k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)

    slow_k_line = fast_k.rolling(slow_k).mean()
    slow_d_line = slow_k_line.rolling(slow_d).mean()

    return slow_k_line, slow_d_line


def compute_smi(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 14, smooth1: int = 3, smooth2: int = 3
) -> pd.Series:
    """
    Stochastic Momentum Index (SMI).
    SMI = 100 * EMA(EMA(Close - Midpoint)) / EMA(EMA(HalfRange))
    """
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()

    midpoint = (highest + lowest) / 2.0
    half_range = (highest - lowest) / 2.0

    numerator = close - midpoint
    denominator = half_range

    ema1_num = numerator.ewm(span=smooth1, adjust=False).mean()
    ema2_num = ema1_num.ewm(span=smooth2, adjust=False).mean()

    ema1_den = denominator.ewm(span=smooth1, adjust=False).mean()
    ema2_den = ema1_den.ewm(span=smooth2, adjust=False).mean()

    smi = 100 * ema2_num / ema2_den.replace(0, np.nan)
    return smi


# ══════════════════════════════════════════════════════════════
#  STEP 5: Screen one ETF
# ══════════════════════════════════════════════════════════════

def analyze_etf(ticker: str, df: pd.DataFrame) -> dict | None:
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
    qdf = resample_to_quarterly(df)

    # Need enough quarterly bars for indicators to be valid.
    # With period=14 + smoothing, we need at least ~20 quarterly bars
    # (= 5 years of data). ETFs with less history will be skipped.
    min_quarters_needed = RSI_PERIOD + RSI_SIGNAL_PERIOD + 2
    if len(qdf) < min_quarters_needed:
        return None

    close_q = qdf["close"]
    high_q = qdf["high"]
    low_q = qdf["low"]

    # ── Compute indicators on QUARTERLY bars ──
    rsi = compute_rsi(close_q, RSI_PERIOD)
    rsi_signal = rsi.rolling(RSI_SIGNAL_PERIOD).mean()

    stoch_k, stoch_d = compute_stochastic_slow(
        high_q, low_q, close_q, STOCH_K_PERIOD, STOCH_SLOW_K, STOCH_SLOW_D
    )

    smi = compute_smi(high_q, low_q, close_q, SMI_PERIOD, SMI_SMOOTH1, SMI_SMOOTH2)

    # ── Validate latest values are not NaN ──
    for series in [rsi, rsi_signal, stoch_k, stoch_d, smi]:
        if pd.isna(series.iloc[-1]) or pd.isna(series.iloc[-2]):
            return None

    # ── Current quarterly values ──
    current_smi = smi.iloc[-1]
    current_stoch_k = stoch_k.iloc[-1]
    current_stoch_d = stoch_d.iloc[-1]
    current_stoch_avg = (current_stoch_k + current_stoch_d) / 2.0
    current_rsi = rsi.iloc[-1]
    current_rsi_signal = rsi_signal.iloc[-1]

    # ── Previous quarter values (for crossover detection) ──
    prev_stoch_k = stoch_k.iloc[-2]
    prev_stoch_d = stoch_d.iloc[-2]
    prev_rsi = rsi.iloc[-2]
    prev_rsi_signal = rsi_signal.iloc[-2]

    # ── What quarter are the indicators reflecting? ──
    latest_quarter_end = qdf.index[-1].strftime("%Y-%m-%d")

    # ══════════════════════════════════════════
    #  FILTER: one of three must be true
    # ══════════════════════════════════════════
    smi_below = current_smi < SMI_THRESHOLD
    stoch_below = current_stoch_avg < STOCH_AVG_THRESHOLD
    rsi_below = current_rsi < RSI_AVG_THRESHOLD

    if not (smi_below or stoch_below or rsi_below):
        return None

    # ══════════════════════════════════════════
    #  SIGNAL: at least one crossover
    # ══════════════════════════════════════════
    stoch_cross = (prev_stoch_k <= prev_stoch_d and current_stoch_k > current_stoch_d)
    rsi_cross = (prev_rsi <= prev_rsi_signal and current_rsi > current_rsi_signal)

    if not (stoch_cross or rsi_cross):
        return None

    # ── Build result ──
    signals = []
    if stoch_cross:
        signals.append("Stoch %K > %D")
    if rsi_cross:
        signals.append("RSI > Signal")

    return {
        "Ticker": ticker,
        "Price": round(latest_price, 2),
        "Quarter": latest_quarter_end,
        "Qtrs": len(qdf),
        "SMI": round(current_smi, 2),
        "Stoch %K": round(current_stoch_k, 2),
        "Stoch %D": round(current_stoch_d, 2),
        "Stoch Avg": round(current_stoch_avg, 2),
        "RSI": round(current_rsi, 2),
        "RSI Sig": round(current_rsi_signal, 2),
        "Crossover": ", ".join(signals),
    }


# ══════════════════════════════════════════════════════════════
#  STEP 6: Run the full screen
# ══════════════════════════════════════════════════════════════

def run_screen():
    print("=" * 70)
    print("  ETF MOMENTUM SCREENER — QUARTERLY INDICATORS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()
    print("  How it works:")
    print("    1. Download daily OHLCV data")
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
    tickers = get_etf_tickers()

    # ── Download daily data ──
    all_data = download_all_data(tickers)

    # ── Screen each ETF ──
    print(f"\nResampling to quarterly & screening {len(all_data)} ETFs...")
    results = []

    for i, (ticker, df) in enumerate(all_data.items()):
        if (i + 1) % 100 == 0:
            sys.stdout.write(f"\r  Analyzed {i+1}/{len(all_data)}...")
            sys.stdout.flush()

        result = analyze_etf(ticker, df)
        if result is not None:
            results.append(result)

    print(f"\r  Analyzed {len(all_data)}/{len(all_data)} — done!")

    # ── Display results ──
    print()
    print("=" * 70)
    if results:
        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values("SMI", ascending=True)

        print(f"  {len(results)} ETF(s) PASSED all criteria "
              f"(quarterly indicators):")
        print("=" * 70)
        print()
        print(results_df.to_string(index=False))
        print()

        # Save to CSV
        out_file = f"etf_screen_quarterly_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        results_df.to_csv(out_file, index=False)
        print(f"  Results saved to: {out_file}")
    else:
        print("  No ETFs met all filter + signal conditions this quarter.")
        print("=" * 70)

    print()
    return results


if __name__ == "__main__":
    run_screen()