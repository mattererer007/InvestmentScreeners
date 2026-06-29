import os
from io import StringIO
from mstarpy import Funds
from mstarpy.search import MorningstarSession
import pandas as pd
from datetime import datetime

import requests


ALPHA_VANTAGE_API='79KZ635LE6FXFU02'
FMP_API='x5zZyH1X8eeiaPKuBjOIeavuia5fJ9Cm'
EODHD_API='6a38a554527726.74213866'
FINNHUB_API='d8saf8hr01qkn75ck7q0d8saf8hr01qkn75ck7qg'
NINJA_API='DNYmuEeaTIlbmdKUFJpHrNjk2eEDHDrENs6N6VNY'

def call_aa_stocks():

    API_KEY = ALPHA_VANTAGE_API

    url = "https://www.alphavantage.co/query"

    params = {
        "function": "LISTING_STATUS",
        "apikey": API_KEY,
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    # Alpha Vantage returns CSV text for this endpoint
    csv_text = response.text

    # Basic guardrail in case Alpha Vantage returns an error/rate-limit message
    if csv_text.strip().startswith("{"):
        raise RuntimeError(f"Alpha Vantage returned JSON instead of CSV: {csv_text[:500]}")

    df = pd.read_csv(StringIO(csv_text))

    nyse_stocks = df[
        (df["exchange"].str.upper() == "NYSE")
        & (df["assetType"].str.upper() == "STOCK")
        & (df["status"].str.upper() == "ACTIVE")
    ].copy()

    nyse_stocks = nyse_stocks.sort_values("symbol")

    print(nyse_stocks.head())
    print(f"Total active NYSE stocks: {len(nyse_stocks)}")

def call_aa_ibm_daily_30_years_json():
    api_key = ALPHA_VANTAGE_API

    url = "https://www.alphavantage.co/query"

    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": "IBM",
        "outputsize": "full",
        "datatype": "json",
        "apikey": api_key,
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    if "Error Message" in data:
        raise RuntimeError(f"Alpha Vantage error: {data['Error Message']}")

    if "Note" in data:
        raise RuntimeError(f"Alpha Vantage rate limit message: {data['Note']}")

    time_series_key = "Time Series (Daily)"

    if time_series_key not in data:
        raise RuntimeError(f"Unexpected Alpha Vantage response: {data}")

    time_series = data[time_series_key]

    cutoff_date = datetime.today().replace(year=datetime.today().year - 30).date()

    filtered_prices = {
        date: values
        for date, values in time_series.items()
        if datetime.strptime(date, "%Y-%m-%d").date() >= cutoff_date
    }

    sorted_prices = dict(sorted(filtered_prices.items()))

    preview_items = list(sorted_prices.items())[:5]
    tail_items = list(sorted_prices.items())[-5:]

    print("First 5 rows:")
    for date, values in preview_items:
        print(date, values)

    print("\nLast 5 rows:")
    for date, values in tail_items:
        print(date, values)

    print(f"\nTotal daily rows for IBM over last 30 years: {len(sorted_prices)}")

    return sorted_prices

def call_eodhd_etf_list():
    """Grab all US ETFs from EODHD"""
    url = f"https://eodhd.com/api/exchange-symbol-list/US"
    params = {
        "api_token": EODHD_API,
        "type": "etf",
        "fmt": "json",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    df = pd.DataFrame(data)

    print("=== US ETFs ===")
    print(df.head(10))
    print(f"\nTotal US ETFs returned: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    return df

def call_eodhd_mutual_fund_list():
    """Grab all US Mutual Funds from EODHD"""
    url = f"https://eodhd.com/api/exchange-symbol-list/US"
    params = {
        "api_token": EODHD_API,
        "type": "fund",
        "fmt": "json",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    df = pd.DataFrame(data)

    print("=== US Mutual Funds ===")
    print(df.head(30))
    print(f"\nTotal US Mutual Funds returned: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    return df

def call_finnhub_mutual_fund_list():
    API_KEY = FINNHUB_API

    url = "https://finnhub.io/api/v1/mutual-fund/list"

    # Method 1: token as query param
    params = {"token": API_KEY}
    response = requests.get(url, params=params, timeout=30)
    print(f"Method 1 (query param) - Status: {response.status_code}")

    if response.status_code != 200:
        # Method 2: header
        headers = {"X-Finnhub-Token": API_KEY}
        response = requests.get(url, headers=headers, timeout=30)
        print(f"Method 2 (header) - Status: {response.status_code}")

    print(f"Response: {response.text[:500]}")

    # Print key to check for issues
    print(f"\nKey: [{API_KEY}]")
    print(f"Key length: {len(API_KEY)}")

def call_finnhub_etf_list():
    """Get ETF list from Finnhub"""
    API_KEY = FINNHUB_API

    url = "https://finnhub.io/api/v1/etf/list"
    params = {"token": API_KEY}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    print(f"Total ETFs: {len(data)}")
    print(f"Sample: {data[:5]}")
    return data


def call_api_ninjas_mutual_fund_list():
    """Paginate through API Ninjas to get all mutual fund tickers"""
    API_KEY = NINJA_API
    all_funds = []
    offset = 0

    while True:
        url = "https://api.api-ninjas.com/v1/mutualfundlist"
        headers = {"X-Api-Key": API_KEY}
        params = {"offset": offset}

        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data:
            break

        all_funds.extend(data)
        print(f"Offset {offset}: got {len(data)} tickers")

        if len(data) < 1000:
            break
        offset += 1000

    print(f"\nTotal mutual fund tickers: {len(all_funds)}")
    print(f"Sample: {all_funds[:10]}")
    return all_funds

def call_alpha_vantage_all_types():
    url = "https://www.alphavantage.co/query"
    params = {"function": "LISTING_STATUS", "apikey": ALPHA_VANTAGE_API}

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    df = pd.read_csv(StringIO(response.text))

    print("Asset types available:")
    print(df["assetType"].value_counts())
    print(f"\nTotal rows: {len(df)}")
    print(f"\nColumns: {list(df.columns)}")
    print(df.head(10))
    return df

def call_sec_edgar_tickers():
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    headers = {"User-Agent": "YourName your@email.com"}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    df = pd.DataFrame(data["data"], columns=data["fields"])

    print(f"Total tickers: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    print(f"\nExchanges:\n{df['exchange'].value_counts()}")
    print(df.head(10))
    return df

def call_sec_edgar_nyse_stocks():
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    headers = {"User-Agent": "YourName your@email.com"}

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    df = pd.DataFrame(data["data"], columns=data["fields"])

    nyse_stocks = df[df["exchange"].str.upper() == "NYSE"].copy()
    nyse_stocks = nyse_stocks.sort_values("ticker")

    print(f"Total NYSE tickers: {len(nyse_stocks)}")
    print(f"Columns: {list(nyse_stocks.columns)}")
    print(nyse_stocks.head(20))
    return nyse_stocks

def call_nasdaq_trader_list():
    url = "https://api.nasdaq.com/api/screener/stocks"
    params = {"tableonly": "true", "limit": 25, "offset": 0, "download": "true"}
    headers = {"User-Agent": "Mozilla/5.0"}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    rows = data.get("data", {}).get("rows", [])
    df = pd.DataFrame(rows)

    print(f"Total symbols: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(10))
    return df

def test_mstarpy_lookup():
    # Test with a few known EODHD Morningstar IDs
    test_ids = ["0P00000M7O", "0P0000CNNR", "0P0000A412"]
    
    for ms_id in test_ids:
        try:
            fund = Funds(term=ms_id, country="us")
            # See what data comes back
            print(f"\n{ms_id}:")
            print(f"  Name: {fund.name}")
            print(f"  Available attrs: {[a for a in dir(fund) if not a.startswith('_')]}")
        except Exception as e:
            print(f"{ms_id}: Error - {e}")


def test_mstarpy_search_by_name():
    """Alternative: search by fund name instead of ID"""
    try:
        fund = Funds(term="Vanguard 500 Index Fund", country="us")
        print(f"Name: {fund.name}")
        print(f"ID: {fund.SecurityId if hasattr(fund, 'SecurityId') else 'N/A'}")
    except Exception as e:
        print(f"Error: {e}")


def test_mstarpy_lookup():
    # Initialize a session (will open a Chrome window)
    session = MorningstarSession()

    # Test with a known fund name or ISIN
    fund = Funds("VFIAX", session=session)
    
    print(f"Name: {fund.name}")
    print(f"Code: {fund.code}")
    print(f"ISIN: {fund.isin}")
    print(f"Asset type: {fund.asset_type}")

    # Test with one of the EODHD Morningstar IDs
    fund2 = Funds("0P00000M7O", session=session)
    print(f"\nName: {fund2.name}")
    print(f"Code: {fund2.code}")
    print(f"ISIN: {fund2.isin}")


if __name__ == "__main__":
    call_sec_edgar_tickers()