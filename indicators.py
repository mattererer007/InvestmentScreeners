"""
indicators.py — Shared technical indicator library
====================================================
Single source of truth for RSI, Stochastic Slow, and SMI.
Imported by both etf_screener.py and validate_stock.py.

All indicators are computed on OHLC bars (daily, weekly, monthly,
quarterly — whatever you feed them). The calling code is responsible
for resampling daily data to the desired frequency first.
"""

import pandas as pd
import numpy as np


# ──────────────────────────────────────────────────────────────
#  RESAMPLING
# ──────────────────────────────────────────────────────────────

def resample_ohlcv(daily: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    Resample daily OHLCV to a lower frequency.

    freq examples:
        'W-FRI'  → weekly (ending Friday)
        'ME'     → month-end
        'QE'     → quarter-end

    Each bar:
        Open   = first trading day's Open
        High   = max High of the period
        Low    = min Low of the period
        Close  = last trading day's Close
        Volume = sum (if present)
    """
    if not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily.index)

    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in daily.columns:
        agg['volume'] = 'sum'

    return daily.resample(freq).agg(agg).dropna()


# ──────────────────────────────────────────────────────────────
#  RSI  (Wilder's smoothing)
# ──────────────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = pd.Series(np.nan, index=close.index)
    avg_loss = pd.Series(np.nan, index=close.index)

    # First Wilder seed = simple average of first 14 gains/losses
    avg_gain.iloc[period] = gain.iloc[1:period + 1].mean()
    avg_loss.iloc[period] = loss.iloc[1:period + 1].mean()

    # Wilder recursive smoothing
    for i in range(period + 1, len(close)):
        avg_gain.iloc[i] = ((avg_gain.iloc[i - 1] * (period - 1)) + gain.iloc[i]) / period
        avg_loss.iloc[i] = ((avg_loss.iloc[i - 1] * (period - 1)) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# ──────────────────────────────────────────────────────────────
#  STOCHASTIC SLOW
# ──────────────────────────────────────────────────────────────

def compute_stochastic_slow(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    k_smooth: int = 3,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    Slow Stochastic.
    Returns (slow %K, %D signal line).
    """
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()

    fast_k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)

    slow_k = fast_k.rolling(k_smooth).mean()
    slow_d = slow_k.rolling(d_period).mean()

    return slow_k, slow_d


# ──────────────────────────────────────────────────────────────
#  STOCHASTIC MOMENTUM INDEX (Blau)
# ──────────────────────────────────────────────────────────────

def compute_smi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    smooth1: int = 3,
    smooth2: int = 3,
) -> pd.Series:
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()

    midpoint = (highest + lowest) / 2.0
    range_ = highest - lowest

    diff = close - midpoint

    ema2_num = diff.ewm(span=smooth1, adjust=False).mean().ewm(span=smooth2, adjust=False).mean()
    ema2_den = range_.ewm(span=smooth1, adjust=False).mean().ewm(span=smooth2, adjust=False).mean()

    return 100 * ema2_num / ema2_den.replace(0, np.nan)


# ──────────────────────────────────────────────────────────────
#  CONVENIENCE: compute everything at once
# ──────────────────────────────────────────────────────────────

def compute_all(
    bars: pd.DataFrame,
    rsi_period: int = 14,
    rsi_signal_period: int = 5,
    stoch_k: int = 14,
    stoch_slow_k: int = 3,
    stoch_slow_d: int = 3,
    smi_period: int = 10,
    smi_smooth1: int = 3,
    smi_smooth2: int = 3,
) -> pd.DataFrame:
    """
    Compute all indicators on an OHLC DataFrame.
    Returns a copy with added columns:
        RSI, RSI_signal, K, D, SMI
    """
    df = bars.copy()

    df['RSI'] = compute_rsi(df['close'], rsi_period)
    df['RSI_signal'] = df['RSI'].rolling(rsi_signal_period).mean()

    df['K'], df['D'] = compute_stochastic_slow(
        df['high'], df['low'], df['close'],
        stoch_k, stoch_slow_k, stoch_slow_d,
    )

    df['SMI'] = compute_smi(
        df['high'], df['low'], df['close'],
        smi_period, smi_smooth1, smi_smooth2,
    )

    return df