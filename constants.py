"""
constants.py — Shared configuration
=====================================
Single source of truth for indicator periods, thresholds, and data settings.
Imported by etf_screener.py, stock_screener.py, and validate_stock.py.

Change a value here and it takes effect everywhere.
"""

# TODO modularize code (cosntants, utils, etc.)
# TODO enable to search by exchange or industry
# TODO for ETFs enable ability to then look at companies that make up holding and choose one 
# 

# ──────────────────────────────────────────────────────────────
#  INDICATOR PERIODS
# ──────────────────────────────────────────────────────────────

RSI_PERIOD          = 14
RSI_SIGNAL_PERIOD   = 5       # SMA of RSI (the "average" / signal line)

STOCH_K_PERIOD      = 14      # Stochastic %K lookback
STOCH_SLOW_K        = 3       # %K smoothing
STOCH_SLOW_D        = 3       # %D smoothing (signal / average line)

SMI_PERIOD          = 10      # SMI lookback
SMI_SMOOTH1         = 3       # SMI first EMA smoothing
SMI_SMOOTH2         = 3       # SMI second EMA smoothing


# ──────────────────────────────────────────────────────────────
#  SCREENING THRESHOLDS
# ──────────────────────────────────────────────────────────────

SMI_THRESHOLD       = -40     # SMI must have dropped below this
STOCH_AVG_THRESHOLD = 35      # Stochastic average (%K+%D)/2 must be below this
RSI_AVG_THRESHOLD   = 30      # RSI must be below this


# ──────────────────────────────────────────────────────────────
#  DATA SETTINGS
# ──────────────────────────────────────────────────────────────

HISTORY_YEARS       = 20      # years of daily data to fetch
MIN_PRICE           = 5.0     # skip penny stocks / ETFs
MIN_AVG_VOLUME      = 100_000 # minimum 20-day average daily volume
BATCH_SIZE          = 50      # tickers per yfinance download batch