"""
data/db.py — Database engine and session management
=====================================================
Provides get_engine() and get_session() helpers that read
DATABASE_URL from the .env file.

Models live in data/models/ (one file per table).  This module
re-exports them for convenience so existing imports still work:

    from data.db import get_session, Ticker, DailyOHLCV

Usage:
    with get_session() as session:
        tickers = session.query(Ticker).filter_by(asset_type="stock").all()
"""

import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Re-export models so callers can do: from data.db import Ticker, ...
from data.models import (  # noqa: F401
    Base,
    DailyOHLCV,
    RefreshLog,
    ScreenResult,
    Ticker,
)
from data.constants import DATABASE_URL

load_dotenv()

# ──────────────────────────────────────────────────────────────
#  ENGINE + SESSION
# ──────────────────────────────────────────────────────────────

_engine = None
_SessionLocal = None


def get_engine():
    """
    Return a singleton SQLAlchemy engine.

    Reads DATABASE_URL from the environment (loaded from .env).
    Pool size is kept small for a local single-user tool.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=5,
            max_overflow=10,
            echo=False,  # set True to see SQL in console
        )
    return _engine


def get_session_factory():
    """Return a singleton sessionmaker bound to the engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal


@contextmanager
def get_session():
    """
    Context manager that yields a SQLAlchemy session.

    Commits on clean exit, rolls back on exception, always closes.

    Usage:
        with get_session() as session:
            session.add(ticker)
    """
    Session = get_session_factory()
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
