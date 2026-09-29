"""Feriados cadastrados na tela (fora das regras versionadas)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29 00:38:33.460572

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('holiday',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('created_by', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('day')
    )


def downgrade():
    op.drop_table('holiday')
