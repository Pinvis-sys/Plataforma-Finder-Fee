"""Esquema inicial do Piloto v1

Revision ID: 0001
Revises: 
Create Date: 2026-09-28 23:26:24.015819

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Bancos criados antes das migrações (flask init-db com create_all) já têm parte das tabelas:
    # esta versão cria só as que faltam. Por isso cada tabela vem dentro de um "if".
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'audit_log' not in existing:
        op.create_table('audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(), nullable=False),
        sa.Column('actor', sa.String(length=120), nullable=True),
        sa.Column('action', sa.String(length=60), nullable=False),
        sa.Column('entity', sa.String(length=40), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('audit_log', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_audit_log_at'), ['at'], unique=False)

    if 'known_company' not in existing:
        op.create_table('known_company',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cnpj', sa.String(length=14), nullable=False),
        sa.Column('cnpj_base', sa.String(length=8), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('known_company', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_known_company_cnpj'), ['cnpj'], unique=False)
            batch_op.create_index(batch_op.f('ix_known_company_cnpj_base'), ['cnpj_base'], unique=False)

    if 'login_attempt' not in existing:
        op.create_table('login_attempt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ip', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('login_attempt', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_login_attempt_at'), ['at'], unique=False)
            batch_op.create_index(batch_op.f('ix_login_attempt_ip'), ['ip'], unique=False)

    if 'material' not in existing:
        op.create_table('material',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('url', sa.String(length=400), nullable=False),
        sa.Column('profile', sa.String(length=40), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('published', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'partner' not in existing:
        op.create_table('partner',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=10), nullable=True),
        sa.Column('cnpj', sa.String(length=14), nullable=False),
        sa.Column('cnpj_base', sa.String(length=8), nullable=True),
        sa.Column('legal_name', sa.String(length=200), nullable=False),
        sa.Column('trade_name', sa.String(length=200), nullable=True),
        sa.Column('kind', sa.String(length=40), nullable=True),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('is_mei', sa.Boolean(), nullable=False),
        sa.Column('contact_name', sa.String(length=120), nullable=True),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('email', sa.String(length=160), nullable=True),
        sa.Column('city', sa.String(length=80), nullable=True),
        sa.Column('uf', sa.String(length=2), nullable=True),
        sa.Column('market_time', sa.String(length=60), nullable=True),
        sa.Column('company_size', sa.String(length=40), nullable=True),
        sa.Column('conflict_notes', sa.Text(), nullable=True),
        sa.Column('invited_by_id', sa.Integer(), nullable=True),
        sa.Column('invite_token', sa.String(length=40), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('application_at', sa.DateTime(), nullable=False),
        sa.Column('bonus_notice_accepted_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reject_reason', sa.String(length=300), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('workshop_at', sa.DateTime(), nullable=True),
        sa.Column('terms_accepted_at', sa.DateTime(), nullable=True),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
        sa.Column('cnpj_verified', sa.Boolean(), nullable=False),
        sa.Column('bank', sa.String(length=60), nullable=True),
        sa.Column('bank_agency', sa.String(length=20), nullable=True),
        sa.Column('bank_account', sa.String(length=30), nullable=True),
        sa.Column('bank_holder_doc', sa.String(length=14), nullable=True),
        sa.Column('bank_verified', sa.Boolean(), nullable=False),
        sa.Column('is_pilot_direct', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['invited_by_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cnpj'),
        sa.UniqueConstraint('code')
        )
        with op.batch_alter_table('partner', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_partner_cnpj_base'), ['cnpj_base'], unique=False)
            batch_op.create_index(batch_op.f('ix_partner_invite_token'), ['invite_token'], unique=True)

    if 'report_edition' not in existing:
        op.create_table('report_edition',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('edition', sa.String(length=12), nullable=False),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('xlsx_path', sa.String(length=300), nullable=True),
        sa.Column('pdf_path', sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('edition')
        )
    if 'rule_set' not in existing:
        op.create_table('rule_set',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('frozen', sa.Boolean(), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.String(length=120), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if 'setting' not in existing:
        op.create_table('setting',
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('value', sa.String(length=400), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key')
        )
    if 'term_document' not in existing:
        op.create_table('term_document',
        sa.Column('key', sa.String(length=30), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('legal_approved', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key')
        )
    if 'direct_invite' not in existing:
        op.create_table('direct_invite',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=40), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('partner_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token')
        )
    if 'duplicate_attempt' not in existing:
        op.create_table('duplicate_attempt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('cnpj', sa.String(length=14), nullable=False),
        sa.Column('reason', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('disputed', sa.Boolean(), nullable=False),
        sa.Column('resolved', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'partner_invoice' not in existing:
        op.create_table('partner_invoice',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=40), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('value', sa.BigInteger(), nullable=False),
        sa.Column('file_path', sa.String(length=300), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('reject_reason', sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'referral' not in existing:
        op.create_table('referral',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=12), nullable=True),
        sa.Column('protocol', sa.String(length=24), nullable=True),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('cnpj', sa.String(length=14), nullable=False),
        sa.Column('cnpj_base', sa.String(length=8), nullable=False),
        sa.Column('dedupe_key', sa.String(length=14), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('company_name', sa.String(length=200), nullable=True),
        sa.Column('decisor_name', sa.String(length=120), nullable=True),
        sa.Column('decisor_role', sa.String(length=120), nullable=True),
        sa.Column('decisor_contact', sa.String(length=160), nullable=True),
        sa.Column('signal', sa.Text(), nullable=True),
        sa.Column('interest', sa.String(length=200), nullable=True),
        sa.Column('relationship', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('eligibility', sa.String(length=20), nullable=False),
        sa.Column('reject_reason', sa.String(length=300), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_by', sa.String(length=120), nullable=True),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('service_started_at', sa.DateTime(), nullable=True),
        sa.Column('waiting', sa.Boolean(), nullable=False),
        sa.Column('waiting_reason', sa.String(length=30), nullable=True),
        sa.Column('protection_until', sa.Date(), nullable=True),
        sa.Column('rule_set_id', sa.Integer(), nullable=True),
        sa.Column('stage', sa.String(length=20), nullable=False),
        sa.Column('stage_updated_at', sa.DateTime(), nullable=True),
        sa.Column('first_contact_at', sa.DateTime(), nullable=True),
        sa.Column('lost_reason', sa.String(length=300), nullable=True),
        sa.Column('next_action', sa.String(length=200), nullable=True),
        sa.Column('consent_status', sa.String(length=12), nullable=False),
        sa.Column('consent_at', sa.DateTime(), nullable=True),
        sa.Column('consent_evidence', sa.String(length=300), nullable=True),
        sa.Column('fits_target', sa.Boolean(), nullable=True),
        sa.Column('delayed_capacity', sa.Boolean(), nullable=False),
        sa.Column('old_contact_flag', sa.Boolean(), nullable=False),
        sa.Column('internal_notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.UniqueConstraint('protocol')
        )
        with op.batch_alter_table('referral', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_referral_cnpj'), ['cnpj'], unique=False)
            batch_op.create_index(batch_op.f('ix_referral_cnpj_base'), ['cnpj_base'], unique=False)
            batch_op.create_index('uq_referral_active_key', ['dedupe_key'], unique=True, sqlite_where=sa.text('active = 1'), postgresql_where=sa.text('active'))

    if 'survey_response' not in existing:
        op.create_table('survey_response',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('calc_doubt', sa.Boolean(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'term_acceptance' not in existing:
        op.create_table('term_acceptance',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('term_key', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=False),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('partner_id', 'term_key', 'version')
        )
    if 'user' not in existing:
        op.create_table('user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=160), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('roles', sa.String(length=120), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.Column('failed_logins', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        sa.Column('totp_secret', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_user_email'), ['email'], unique=True)

    if 'contract' not in existing:
        op.create_table('contract',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('referral_id', sa.Integer(), nullable=False),
        sa.Column('product', sa.String(length=120), nullable=False),
        sa.Column('audience', sa.String(length=40), nullable=True),
        sa.Column('signed_at', sa.Date(), nullable=False),
        sa.Column('is_simulation', sa.Boolean(), nullable=False),
        sa.Column('first_for_recruited', sa.Boolean(), nullable=False),
        sa.Column('bonus_recipient_id', sa.Integer(), nullable=True),
        sa.Column('cancelled_at', sa.Date(), nullable=True),
        sa.Column('cancel_reason', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['bonus_recipient_id'], ['partner.id'], ),
        sa.ForeignKeyConstraint(['referral_id'], ['referral.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'notification' not in existing:
        op.create_table('notification',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('partner_id', sa.Integer(), nullable=True),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('message', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('emailed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'review_request' not in existing:
        op.create_table('review_request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('partner_id', sa.Integer(), nullable=False),
        sa.Column('referral_id', sa.Integer(), nullable=True),
        sa.Column('cnpj', sa.String(length=14), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['partner_id'], ['partner.id'], ),
        sa.ForeignKeyConstraint(['referral_id'], ['referral.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'user_session' not in existing:
        op.create_table('user_session',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
        )
        with op.batch_alter_table('user_session', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_user_session_last_seen_at'), ['last_seen_at'], unique=False)
            batch_op.create_index(batch_op.f('ix_user_session_user_id'), ['user_id'], unique=False)

    if 'installment' not in existing:
        op.create_table('installment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contract_id', sa.Integer(), nullable=False),
        sa.Column('item_index', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('gross_amount', sa.BigInteger(), nullable=False),
        sa.Column('in_window', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.ForeignKeyConstraint(['contract_id'], ['contract.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'receipt' not in existing:
        op.create_table('receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('installment_id', sa.Integer(), nullable=False),
        sa.Column('received_at', sa.Date(), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('nf_number', sa.String(length=40), nullable=True),
        sa.Column('nf_value', sa.BigInteger(), nullable=False),
        sa.Column('taxes', sa.BigInteger(), nullable=False),
        sa.Column('tax_detail', sa.JSON(), nullable=True),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('registered_by', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['installment_id'], ['installment.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'commission_line' not in existing:
        op.create_table('commission_line',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('receipt_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('beneficiary_id', sa.Integer(), nullable=False),
        sa.Column('referral_id', sa.Integer(), nullable=True),
        sa.Column('source_partner_id', sa.Integer(), nullable=True),
        sa.Column('rule_set_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('pct_applied', sa.String(length=10), nullable=True),
        sa.Column('base_net', sa.BigInteger(), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('state', sa.String(length=24), nullable=False),
        sa.Column('hold_reason', sa.String(length=200), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approved_by', sa.String(length=120), nullable=True),
        sa.Column('invoice_id', sa.Integer(), nullable=True),
        sa.Column('cycle', sa.Date(), nullable=True),
        sa.Column('paid_at', sa.Date(), nullable=True),
        sa.Column('paid_reference', sa.String(length=160), nullable=True),
        sa.Column('checked', sa.Boolean(), nullable=False),
        sa.Column('divergence_note', sa.String(length=300), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['beneficiary_id'], ['partner.id'], ),
        sa.ForeignKeyConstraint(['invoice_id'], ['partner_invoice.id'], ),
        sa.ForeignKeyConstraint(['receipt_id'], ['receipt.id'], ),
        sa.ForeignKeyConstraint(['referral_id'], ['referral.id'], ),
        sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id'], ),
        sa.ForeignKeyConstraint(['source_partner_id'], ['partner.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_id', 'kind', name='uq_line_receipt_kind')
        )


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('commission_line')
    op.drop_table('receipt')
    op.drop_table('installment')
    with op.batch_alter_table('user_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_session_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_session_last_seen_at'))

    op.drop_table('user_session')
    op.drop_table('review_request')
    op.drop_table('notification')
    op.drop_table('contract')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_email'))

    op.drop_table('user')
    op.drop_table('term_acceptance')
    op.drop_table('survey_response')
    with op.batch_alter_table('referral', schema=None) as batch_op:
        batch_op.drop_index('uq_referral_active_key', sqlite_where=sa.text('active = 1'), postgresql_where=sa.text('active'))
        batch_op.drop_index(batch_op.f('ix_referral_cnpj_base'))
        batch_op.drop_index(batch_op.f('ix_referral_cnpj'))

    op.drop_table('referral')
    op.drop_table('partner_invoice')
    op.drop_table('duplicate_attempt')
    op.drop_table('direct_invite')
    op.drop_table('term_document')
    op.drop_table('setting')
    op.drop_table('rule_set')
    op.drop_table('report_edition')
    with op.batch_alter_table('partner', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_partner_invite_token'))
        batch_op.drop_index(batch_op.f('ix_partner_cnpj_base'))

    op.drop_table('partner')
    op.drop_table('material')
    with op.batch_alter_table('login_attempt', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_attempt_ip'))
        batch_op.drop_index(batch_op.f('ix_login_attempt_at'))

    op.drop_table('login_attempt')
    with op.batch_alter_table('known_company', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_known_company_cnpj_base'))
        batch_op.drop_index(batch_op.f('ix_known_company_cnpj'))

    op.drop_table('known_company')
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_at'))

    op.drop_table('audit_log')
