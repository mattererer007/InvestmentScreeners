"""
data/models/refresh_log.py — RefreshLog model
===============================================
Tracks the last date fetched for each ticker's OHLCV data.

The refresh process checks this table to decide whether to do a
full backfill or an incremental update (only fetch days after
last_date_fetched).
"""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import relationship

from data.models.base import Base


class RefreshLog(Base):
    __tablename__ = "refresh_log"

    symbol = Column(
        String(20),
        ForeignKey("tickers.symbol"),
        primary_key=True,
    )
    last_date_fetched = Column(Date)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    ticker_rel = relationship("Ticker", back_populates="refresh_entry")

    def __repr__(self):
        return f"<RefreshLog {self.symbol} last={self.last_date_fetched}>"
