"""Código de duas etapas não pode ser reutilizado: guarda o último intervalo aceito

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 23:27:31.777611

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('totp_last_step', sa.BigInteger(), nullable=True))



def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('totp_last_step')

