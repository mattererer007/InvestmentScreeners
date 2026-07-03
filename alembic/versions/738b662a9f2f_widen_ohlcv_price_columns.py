"""widen ohlcv price columns

Revision ID: 738b662a9f2f
Revises: 3535470b9b78
Create Date: 2026-07-03 13:06:53.509071
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '738b662a9f2f'
down_revision: Union[str, None] = '3535470b9b78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('daily_ohlcv', 'open', type_=sa.Numeric(20, 4))
    op.alter_column('daily_ohlcv', 'high', type_=sa.Numeric(20, 4))
    op.alter_column('daily_ohlcv', 'low', type_=sa.Numeric(20, 4))
    op.alter_column('daily_ohlcv', 'close', type_=sa.Numeric(20, 4))


def downgrade() -> None:
    op.alter_column('daily_ohlcv', 'open', type_=sa.Numeric(12, 4))
    op.alter_column('daily_ohlcv', 'high', type_=sa.Numeric(12, 4))
    op.alter_column('daily_ohlcv', 'low', type_=sa.Numeric(12, 4))
    op.alter_column('daily_ohlcv', 'close', type_=sa.Numeric(12, 4))