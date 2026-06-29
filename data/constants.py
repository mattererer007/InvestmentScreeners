from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL not set. Add it to your .env file, e.g.:\n"
        "DATABASE_URL=postgresql://screener_admin:password@localhost:5432/investment_screeners"
    )

# yfinance settings
BATCH_SIZE = 50         # tickers per yfinance.download() call
HISTORY_YEARS = 20      # default lookback for full backfills
MIN_ROWS = 50


# SEC EDGAR requires a User-Agent header with contact info
_SEC_HEADERS = {
    "User-Agent": "InvestmentScreeners/1.0 (contact@example.com)",
    "Accept-Encoding": "gzip, deflate",
}

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

EODHD_BASE_URL = "https://eodhd.com/api/exchange-symbol-list/US"