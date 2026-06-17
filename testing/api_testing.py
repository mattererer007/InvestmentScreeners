import os
from io import StringIO

import pandas as pd
from datetime import datetime

import requests


ALPHA_VANTAGE_API='79KZ635LE6FXFU02'
FMP_API='x5zZyH1X8eeiaPKuBjOIeavuia5fJ9Cm'

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


if __name__ == "__main__":
    call_aa_ibm_daily_30_years_json()

