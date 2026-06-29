"""
data/models/ticker.py — Ticker model
======================================
Universe of tradeable instruments (stocks and ETFs).

Populated from SEC EDGAR (stocks) and EODHD (ETFs).
The symbol is the natural primary key since all downstream
tables reference it.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from data.models.base import Base


class Ticker(Base):
    __tablename__ = "tickers"

    symbol = Column(String(20), primary_key=True)
    asset_type = Column(
        String(10),
        CheckConstraint("asset_type IN ('stock', 'etf')"),
        nullable=False,
    )
    exchange = Column(String(20))
    name = Column(Text)
    sector = Column(String(100))
    industry = Column(String(100))
    status = Column(
        String(10),
        CheckConstraint("status IN ('active', 'delisted')"),
        nullable=False,
        default="active",
    )
    source = Column(
        String(20),
        CheckConstraint("source IN ('edgar', 'eodhd')"),
    )
    last_refreshed = Column(DateTime(timezone=True), server_default=func.now())

    # relationships
    ohlcv_rows = relationship("DailyOHLCV", back_populates="ticker_rel", lazy="dynamic")
    refresh_entry = relationship("RefreshLog", back_populates="ticker_rel", uselist=False)

    def __repr__(self):
        return f"<Ticker {self.symbol} ({self.asset_type})>"
