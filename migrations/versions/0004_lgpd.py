"""LGPD: marca de anonimização em parceiro e indicação

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 00:50:36.726225

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('partner', schema=None) as batch_op:
        batch_op.add_column(sa.Column('anonymized_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('referral', schema=None) as batch_op:
        batch_op.add_column(sa.Column('anonymized_at', sa.DateTime(), nullable=True))



def downgrade():
    with op.batch_alter_table('referral', schema=None) as batch_op:
        batch_op.drop_column('anonymized_at')

    with op.batch_alter_table('partner', schema=None) as batch_op:
        batch_op.drop_column('anonymized_at')

