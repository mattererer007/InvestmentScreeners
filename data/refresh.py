"""
data/refresh.py — Orchestrates universe + OHLCV refresh
========================================================
High-level functions called from main.py's menu:

    refresh_universe(session, exchanges)
        Pull tickers from EDGAR + EODHD and upsert into the tickers table.

    refresh_ohlcv(session, asset_type, batch_limit)
        For each ticker in the tickers table, check refresh_log,
        download only missing days from Yahoo Finance, and bulk-insert
        into daily_ohlcv.  Updates refresh_log on success.

    refresh_all(session, exchanges, batch_limit)
        Convenience: refresh_universe + refresh_ohlcv for both types.

Design notes
------------
- All functions accept an explicit SQLAlchemy session so the caller
  controls the transaction boundary (and tests can pass a test session).
- Upserts use PostgreSQL's ON CONFLICT via sqlalchemy's insert().on_conflict_do_update().
- OHLCV inserts use ON CONFLICT DO NOTHING so re-running is safe.
- refresh_log is updated per-symbol after its OHLCV rows are written.
"""

import logging
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from data.db import DailyOHLCV, RefreshLog, Ticker
from data.downloader import download_ohlcv_incremental
from data.universe import fetch_etf_tickers, fetch_stock_tickers

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  UNIVERSE REFRESH
# ──────────────────────────────────────────────────────────────


def _upsert_tickers(session: Session, rows: list[dict]) -> int:
    """
    Bulk upsert a list of ticker dicts into the tickers table.

    On conflict (symbol already exists), updates name, exchange,
    status, and last_refreshed.  Returns the number of rows affected.
    """
    if not rows:
        return 0

    stmt = pg_insert(Ticker.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "name": stmt.excluded.name,
            "exchange": stmt.excluded.exchange,
            "status": stmt.excluded.status,
            "last_refreshed": datetime.utcnow(),
        },
    )
    result = session.execute(stmt)
    session.flush()
    return result.rowcount


def refresh_universe(
    session: Session,
    exchanges: list[str] | None = None,
) -> dict[str, int]:
    """
    Pull tickers from EDGAR (stocks) and EODHD (ETFs) and upsert
    into the tickers table.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session.
    exchanges : list[str] or None
        Exchanges to fetch for stocks (e.g. ["NYSE", "Nasdaq"]).
        Defaults to both.

    Returns
    -------
    dict with keys 'stocks' and 'etfs', values = row counts upserted.
    """
    if exchanges is None:
        exchanges = ["NYSE", "Nasdaq"]

    counts = {"stocks": 0, "etfs": 0}

    # ── Stocks from EDGAR ──
    try:
        stock_df = fetch_stock_tickers(exchanges=exchanges)
        stock_rows = stock_df.to_dict("records")

        # Set status for all incoming rows
        for row in stock_rows:
            row["status"] = "active"

        counts["stocks"] = _upsert_tickers(session, stock_rows)
        logger.info(f"Upserted {counts['stocks']} stock tickers")
    except Exception as e:
        logger.error(f"Failed to refresh stock universe: {e}")
        raise

    # ── ETFs from EODHD ──
    try:
        etf_df = fetch_etf_tickers()
        etf_rows = etf_df.to_dict("records")

        for row in etf_rows:
            row["status"] = "active"

        counts["etfs"] = _upsert_tickers(session, etf_rows)
        logger.info(f"Upserted {counts['etfs']} ETF tickers")
    except Exception as e:
        logger.error(f"Failed to refresh ETF universe: {e}")
        raise

    return counts


# ──────────────────────────────────────────────────────────────
#  OHLCV REFRESH
# ──────────────────────────────────────────────────────────────


def _get_symbols_with_last_date(
    session: Session,
    asset_type: str | None = None,
) -> dict[str, date | None]:
    """
    Build the {symbol: last_date_fetched} dict for all active tickers.

    Joins tickers LEFT JOIN refresh_log so new tickers (no refresh_log
    entry yet) come back with None → triggers a full backfill.
    """
    query = (
        session.query(Ticker.symbol, RefreshLog.last_date_fetched)
        .outerjoin(RefreshLog, Ticker.symbol == RefreshLog.symbol)
        .filter(Ticker.status == "active")
    )

    if asset_type:
        query = query.filter(Ticker.asset_type == asset_type)

    return {row.symbol: row.last_date_fetched for row in query.all()}


def _bulk_insert_ohlcv(session: Session, symbol: str, df, chunk_size: int = 500) -> int:
    """
    Insert OHLCV rows for one symbol.  Uses ON CONFLICT DO NOTHING
    so re-running with overlapping dates is safe.

    Inserts in chunks of `chunk_size` rows to avoid SQLAlchemy
    choking on huge parameter lists (some tickers have 5,000+ rows).

    Returns the number of new rows inserted.
    """
    if df.empty:
        return 0

    # Build rows using to_dict — avoids pandas Series indexing issues
    records = df.to_dict("records")
    rows = []
    for r in records:
        rows.append(
            {
                "symbol": symbol,
                "date": r["date"],
                "open": float(r["open"]) if pd.notna(r.get("open")) else None,
                "high": float(r["high"]) if pd.notna(r.get("high")) else None,
                "low": float(r["low"]) if pd.notna(r.get("low")) else None,
                "close": float(r["close"]) if pd.notna(r.get("close")) else None,
                "volume": int(r["volume"]) if pd.notna(r.get("volume")) else None,
            }
        )

    total_inserted = 0
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        stmt = pg_insert(DailyOHLCV.__table__).values(chunk)
        stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "date"])
        result = session.execute(stmt)
        total_inserted += result.rowcount

    session.flush()
    return total_inserted


def _update_refresh_log(session: Session, symbol: str, last_date: date):
    """
    Upsert the refresh_log entry for a symbol after its OHLCV
    data has been written.
    """
    stmt = pg_insert(RefreshLog.__table__).values(
        symbol=symbol,
        last_date_fetched=last_date,
        updated_at=datetime.utcnow(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "last_date_fetched": stmt.excluded.last_date_fetched,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)
    session.flush()


def refresh_ohlcv(
    session: Session,
    asset_type: str | None = None,
    batch_limit: int | None = None,
    commit_every: int = 50,
) -> dict[str, int]:
    """
    Download missing OHLCV data and insert into daily_ohlcv.

    Commits to the database every `commit_every` symbols so progress
    is saved incrementally and visible in pgAdmin in real time.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session.
    asset_type : str or None
        "stock", "etf", or None (both).
    batch_limit : int or None
        Max number of symbols to process (useful for testing).
    commit_every : int
        Commit to the database after this many symbols (default: 50).

    Returns
    -------
    dict with 'symbols_processed', 'new_rows', 'symbols_failed'.
    """
    symbols_last_date = _get_symbols_with_last_date(session, asset_type)

    if batch_limit:
        # Prioritise symbols that have never been fetched (None) first
        sorted_symbols = sorted(
            symbols_last_date.items(),
            key=lambda x: (x[1] is not None, x[1]),  # None sorts first
        )
        symbols_last_date = dict(sorted_symbols[:batch_limit])

    if not symbols_last_date:
        logger.info("No symbols need OHLCV refresh")
        return {"symbols_processed": 0, "new_rows": 0, "symbols_failed": 0}

    logger.info(
        f"Refreshing OHLCV for {len(symbols_last_date)} "
        f"{asset_type or 'all'} symbols..."
    )

    # Download from Yahoo Finance
    downloaded = download_ohlcv_incremental(symbols_last_date)

    stats = {"symbols_processed": 0, "new_rows": 0, "symbols_failed": 0}
    total = len(downloaded)
    pending = 0  # symbols since last commit

    for i, (symbol, df) in enumerate(downloaded.items(), 1):
        try:
            new_rows = _bulk_insert_ohlcv(session, symbol, df)
            stats["new_rows"] += new_rows

            # Update refresh_log with the latest date in this batch
            max_date = df["date"].max()
            _update_refresh_log(session, symbol, max_date)

            stats["symbols_processed"] += 1
            pending += 1
        except Exception as e:
            logger.error(f"  {symbol}: insert failed — {e}")
            session.rollback()  # clear the failed transaction so next symbol works
            stats["symbols_failed"] += 1

        # Commit every N symbols so progress is saved
        if pending >= commit_every:
            session.commit()
            logger.info(
                f"  Committed {i}/{total} symbols "
                f"({stats['new_rows']:,} total rows so far)"
            )
            pending = 0

    # Final commit for any remaining
    if pending > 0:
        session.commit()

    logger.info(
        f"OHLCV refresh complete: {stats['symbols_processed']} symbols, "
        f"{stats['new_rows']:,} new rows, {stats['symbols_failed']} failures"
    )
    return stats


# ──────────────────────────────────────────────────────────────
#  CONVENIENCE
# ──────────────────────────────────────────────────────────────


def refresh_all(
    session: Session,
    exchanges: list[str] | None = None,
    batch_limit: int | None = None,
) -> dict:
    """
    Full refresh: universe first, then OHLCV for all tickers.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session.
    exchanges : list[str] or None
        Exchanges for stock universe (default: ["NYSE", "Nasdaq"]).
    batch_limit : int or None
        Max symbols to download OHLCV for (useful for testing).

    Returns
    -------
    dict with 'universe' and 'ohlcv' sub-dicts.
    """
    universe_counts = refresh_universe(session, exchanges)
    ohlcv_stats = refresh_ohlcv(session, asset_type=None, batch_limit=batch_limit)

    return {
        "universe": universe_counts,
        "ohlcv": ohlcv_stats,
    }