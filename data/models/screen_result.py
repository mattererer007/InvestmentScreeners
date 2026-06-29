"""
data/models/screen_result.py — ScreenResult model
===================================================
Log of screener hits.  Every time the screener runs, each symbol
that passes the filters gets a row here so you can track what
was flagged and when.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)

from data.models.base import Base


class ScreenResult(Base):
    __tablename__ = "screen_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_date = Column(Date, nullable=False, server_default=func.current_date())
    screener_type = Column(
        String(10),
        CheckConstraint("screener_type IN ('stock', 'etf')"),
    )
    symbol = Column(String(20), ForeignKey("tickers.symbol"), nullable=False)
    smi = Column(Numeric(8, 4))
    stoch_k = Column(Numeric(8, 4))
    stoch_d = Column(Numeric(8, 4))
    rsi = Column(Numeric(8, 4))
    crossover_type = Column(String(30))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_screen_run", "run_date", "screener_type"),
    )

    def __repr__(self):
        return f"<ScreenResult {self.symbol} {self.run_date}>"
