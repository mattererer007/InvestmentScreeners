"""
data/models/daily_ohlcv.py — DailyOHLCV model
===============================================
Daily price bars.  One row per symbol per trading day.

Populated from Yahoo Finance (yfinance).  The composite PK on
(symbol, date) prevents duplicates.  The standalone index on
symbol speeds up per-ticker queries that don't filter by date.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import relationship

from data.models.base import Base


class DailyOHLCV(Base):
    __tablename__ = "daily_ohlcv"

    symbol = Column(
        String(20),
        ForeignKey("tickers.symbol"),
        primary_key=True,
    )
    date = Column(Date, primary_key=True)
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    volume = Column(BigInteger)
    last_updated = Column(DateTime(timezone=True), server_default=func.now())

    ticker_rel = relationship("Ticker", back_populates="ohlcv_rows")

    __table_args__ = (
        Index("idx_ohlcv_symbol", "symbol"),
    )

    def __repr__(self):
        return f"<DailyOHLCV {self.symbol} {self.date}>"
