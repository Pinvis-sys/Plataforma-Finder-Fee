"""Modelo de dados. Valores monetários em centavos (tipo Money)."""
from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint, text
from werkzeug.security import check_password_hash, generate_password_hash

from .core import Money, db, now
from .rules import RuleSet  # noqa: F401  (registra a tabela)

ROLES = ("participant", "manager", "commercial", "finance", "admin")
STAFF_ROLES = ("manager", "commercial", "finance", "admin")

PARTNER_STATUS = {
    "candidato": "Candidatura recebida",
    "em_analise": "Em análise",
    "aprovado": "Aprovado, aguardando workshop e termos",
    "ativo": "Ativo",
    "rejeitado": "Não aprovado",
    "suspenso": "Suspenso",
}
PARTNER_KINDS = {
    "contador": "Contador (médio/grande)", "assessor_invest": "Assessor/gestor de investimentos",
    "consultoria_comercial": "Consultoria comercial", "consultoria_pessoas": "Consultoria de Pessoas",
    "consultoria_treinamento": "Consultoria de treinamentos", "agencia_marketing": "Agência de marketing",
    "filantropia": "Consultor de filantropia/associativismo", "prof_mba_a": "Professor de MBA (canal A)",
    "prof_mba_b": "Professor de MBA (canal B)", "alto_ticket": "Vendedor/representante de alto ticket",
    "outro": "Outro perfil (Nível 2 ou 3)",
}
ELIGIBILITY = {"recebida": "Recebida", "em_validacao": "Em validação", "aceita": "Aceita",
               "rejeitada": "Não aceita", "expirada": "Expirada"}
ACTIVE_ELIGIBILITY = ("recebida", "em_validacao", "aceita")
STAGES = {"aguardando_contato": "Aguardando contato", "qualificacao": "Qualificação", "reuniao": "Reunião",
          "proposta": "Proposta", "negociacao": "Negociação", "ganha": "Ganha", "perdida": "Perdida"}
FIN_STATES = {"aguardando_condicao": "Aguardando condição", "aprovada": "Aprovada", "programada": "Programada",
              "paga": "Paga", "ajustada": "Ajustada", "cancelada": "Cancelada"}
TERM_KEYS = {
    "contrato": "Contrato de parceria (Camada 1)",
    "carater_eventual": "Cláusula de caráter eventual",
    "codigo_conduta": "Código de conduta do parceiro",
    "nda": "NDA / termo de confidencialidade",
    "conflito_interesse": "Política de conflito de interesse",
    "pos_workshop": "Termo de aceite pós-workshop",
}


class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    roles = db.Column(db.String(120), default="participant", nullable=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    failed_logins = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    totp_secret = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    last_login_at = db.Column(db.DateTime)

    partner = db.relationship("Partner", foreign_keys=[partner_id], back_populates="users")

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)

    @property
    def role_set(self) -> set:
        return {r.strip() for r in (self.roles or "").split(",") if r.strip()}

    def has_role(self, *roles) -> bool:
        rs = self.role_set
        return "admin" in rs or any(r in rs for r in roles)

    @property
    def is_staff(self) -> bool:
        return bool(self.role_set & set(STAFF_ROLES))


class Partner(db.Model):
    __tablename__ = "partner"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True)            # P01... usado nos relatórios
    cnpj = db.Column(db.String(14), unique=True, nullable=False)
    cnpj_base = db.Column(db.String(8), index=True)
    legal_name = db.Column(db.String(200), nullable=False)
    trade_name = db.Column(db.String(200))
    kind = db.Column(db.String(40), default="outro")
    level = db.Column(db.Integer, default=1, nullable=False)   # 1, 2 ou 3 (Nível 3 não recebe comissão direta)
    is_mei = db.Column(db.Boolean, default=False, nullable=False)
    contact_name = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    email = db.Column(db.String(160))
    city = db.Column(db.String(80))
    uf = db.Column(db.String(2))
    market_time = db.Column(db.String(60))                 # tempo de mercado (critério de elegibilidade)
    company_size = db.Column(db.String(40))                # porte (critério a definir)
    conflict_notes = db.Column(db.Text)                    # conflito de interesse sinalizado
    invited_by_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    invite_token = db.Column(db.String(40), unique=True, index=True)
    status = db.Column(db.String(20), default="candidato", nullable=False)
    application_at = db.Column(db.DateTime, default=now, nullable=False)
    bonus_notice_accepted_at = db.Column(db.DateTime)
    reviewed_at = db.Column(db.DateTime)
    reject_reason = db.Column(db.String(300))
    approved_at = db.Column(db.DateTime)
    workshop_at = db.Column(db.DateTime)
    terms_accepted_at = db.Column(db.DateTime)
    activated_at = db.Column(db.DateTime)
    cnpj_verified = db.Column(db.Boolean, default=False, nullable=False)
    bank = db.Column(db.String(60))
    bank_agency = db.Column(db.String(20))
    bank_account = db.Column(db.String(30))
    bank_holder_doc = db.Column(db.String(14))
    bank_verified = db.Column(db.Boolean, default=False, nullable=False)
    is_pilot_direct = db.Column(db.Boolean, default=True, nullable=False)   # False = recrutado

    users = db.relationship("User", foreign_keys="User.partner_id", back_populates="partner")
    invited_by = db.relationship("Partner", remote_side=[id], foreign_keys=[invited_by_id])

    @property
    def display_name(self) -> str:
        return self.trade_name or self.legal_name

    @property
    def can_receive(self) -> bool:
        from . import cnpj as c
        return bool(self.cnpj_verified and self.bank_verified and self.bank_holder_doc
                    and c.digits(self.bank_holder_doc) == self.cnpj)


class TermDocument(db.Model):
    __tablename__ = "term_document"
    key = db.Column(db.String(30), primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    version = db.Column(db.Integer, default=1, nullable=False)
    body = db.Column(db.Text, nullable=False)
    legal_approved = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, nullable=False)


class TermAcceptance(db.Model):
    __tablename__ = "term_acceptance"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    term_key = db.Column(db.String(30), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    accepted_at = db.Column(db.DateTime, default=now, nullable=False)
    ip = db.Column(db.String(45))
    __table_args__ = (UniqueConstraint("partner_id", "term_key", "version"),)


class Referral(db.Model):
    __tablename__ = "referral"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(12), unique=True)            # I-001 (relatórios)
    protocol = db.Column(db.String(24), unique=True)        # protocolo exibido ao parceiro
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    cnpj_base = db.Column(db.String(8), nullable=False, index=True)
    dedupe_key = db.Column(db.String(14), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    company_name = db.Column(db.String(200))
    decisor_name = db.Column(db.String(120))
    decisor_role = db.Column(db.String(120))
    decisor_contact = db.Column(db.String(160))
    signal = db.Column(db.Text)                             # sinal observado que motivou a indicação
    interest = db.Column(db.String(200))                    # necessidade/serviço de interesse
    relationship = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    eligibility = db.Column(db.String(20), default="recebida", nullable=False)
    reject_reason = db.Column(db.String(300))
    reviewed_at = db.Column(db.DateTime)
    reviewed_by = db.Column(db.String(120))
    accepted_at = db.Column(db.DateTime)
    service_started_at = db.Column(db.DateTime)             # "entrada em atendimento"
    waiting = db.Column(db.Boolean, default=False, nullable=False)
    waiting_reason = db.Column(db.String(30))               # teto_parceiro | teto_agregado | capacidade
    protection_until = db.Column(db.Date)
    rule_set_id = db.Column(db.Integer, db.ForeignKey("rule_set.id"))
    stage = db.Column(db.String(20), default="aguardando_contato", nullable=False)
    stage_updated_at = db.Column(db.DateTime, default=now)
    first_contact_at = db.Column(db.DateTime)
    lost_reason = db.Column(db.String(300))
    next_action = db.Column(db.String(200))                 # próxima ação combinada (visível ao parceiro)
    consent_status = db.Column(db.String(12), default="pendente", nullable=False)   # pendente | registrado
    consent_at = db.Column(db.DateTime)
    consent_evidence = db.Column(db.String(300))
    fits_target = db.Column(db.Boolean)                     # aderência ao cliente ideal / faixa-alvo
    delayed_capacity = db.Column(db.Boolean, default=False, nullable=False)
    old_contact_flag = db.Column(db.Boolean, default=False, nullable=False)
    internal_notes = db.Column(db.Text)

    partner = db.relationship("Partner", foreign_keys=[partner_id])
    rule_set = db.relationship("RuleSet")
    contracts = db.relationship("Contract", back_populates="referral")

    __table_args__ = (
        # Garante a prioridade do primeiro registro mesmo com envios simultâneos.
        Index("uq_referral_active_key", "dedupe_key", unique=True,
              sqlite_where=text("active = 1"), postgresql_where=text("active")),
    )


class Contract(db.Model):
    __tablename__ = "contract"
    id = db.Column(db.Integer, primary_key=True)
    referral_id = db.Column(db.Integer, db.ForeignKey("referral.id"), nullable=False)
    product = db.Column(db.String(120), nullable=False)
    audience = db.Column(db.String(40))
    signed_at = db.Column(db.Date, nullable=False)
    is_simulation = db.Column(db.Boolean, default=False, nullable=False)   # contrato simulado (teste do bônus)
    first_for_recruited = db.Column(db.Boolean, default=False, nullable=False)
    bonus_recipient_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    cancelled_at = db.Column(db.Date)
    cancel_reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    referral = db.relationship("Referral", back_populates="contracts")
    installments = db.relationship("Installment", back_populates="contract", order_by="Installment.due_date")


class Installment(db.Model):
    __tablename__ = "installment"
    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("contract.id"), nullable=False)
    item_index = db.Column(db.Integer, default=0, nullable=False)
    description = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    total = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    gross_amount = db.Column(Money, nullable=False)
    in_window = db.Column(db.Boolean, nullable=False)
    status = db.Column(db.String(12), default="aberta", nullable=False)   # aberta | recebida | cancelada

    contract = db.relationship("Contract", back_populates="installments")
    receipts = db.relationship("Receipt", back_populates="installment")


class Receipt(db.Model):
    __tablename__ = "receipt"
    id = db.Column(db.Integer, primary_key=True)
    installment_id = db.Column(db.Integer, db.ForeignKey("installment.id"), nullable=False)
    received_at = db.Column(db.Date, nullable=False)
    amount = db.Column(Money, nullable=False)          # negativo = estorno
    nf_number = db.Column(db.String(40))
    nf_value = db.Column(Money, nullable=False)
    taxes = db.Column(Money, nullable=False)
    tax_detail = db.Column(db.JSON)
    kind = db.Column(db.String(12), default="recebimento", nullable=False)   # recebimento | estorno
    registered_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    installment = db.relationship("Installment", back_populates="receipts")


class PartnerInvoice(db.Model):
    __tablename__ = "partner_invoice"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    number = db.Column(db.String(40), nullable=False)
    issue_date = db.Column(db.Date, nullable=False)
    value = db.Column(Money, nullable=False)
    file_path = db.Column(db.String(300))
    uploaded_at = db.Column(db.DateTime, default=now, nullable=False)
    status = db.Column(db.String(12), default="enviada", nullable=False)   # enviada | conferida | rejeitada
    reject_reason = db.Column(db.String(300))

    partner = db.relationship("Partner")


class CommissionLine(db.Model):
    __tablename__ = "commission_line"
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey("receipt.id"))
    kind = db.Column(db.String(10), nullable=False)          # cliente | bonus | ajuste
    beneficiary_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    referral_id = db.Column(db.Integer, db.ForeignKey("referral.id"))
    source_partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))   # bônus: parceiro recrutado
    rule_set_id = db.Column(db.Integer, db.ForeignKey("rule_set.id"))
    amount = db.Column(Money, nullable=False)
    pct_applied = db.Column(db.String(10))
    base_net = db.Column(Money)
    detail = db.Column(db.JSON)                                # fórmula e parâmetros para o demonstrativo
    state = db.Column(db.String(24), default="aguardando_condicao", nullable=False)
    hold_reason = db.Column(db.String(200))
    approved_at = db.Column(db.DateTime)
    approved_by = db.Column(db.String(120))
    invoice_id = db.Column(db.Integer, db.ForeignKey("partner_invoice.id"))
    cycle = db.Column(db.Date)                                 # 1º dia do mês do pagamento
    paid_at = db.Column(db.Date)
    paid_reference = db.Column(db.String(160))
    checked = db.Column(db.Boolean, default=False, nullable=False)   # conferida contra a planilha
    divergence_note = db.Column(db.String(300))
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    receipt = db.relationship("Receipt")
    beneficiary = db.relationship("Partner", foreign_keys=[beneficiary_id])
    source_partner = db.relationship("Partner", foreign_keys=[source_partner_id])
    referral = db.relationship("Referral")
    invoice = db.relationship("PartnerInvoice")

    __table_args__ = (UniqueConstraint("receipt_id", "kind", name="uq_line_receipt_kind"),)


class KnownCompany(db.Model):
    """Base comercial da All Targets: clientes atuais, oportunidades abertas e contatos antigos."""
    __tablename__ = "known_company"
    id = db.Column(db.Integer, primary_key=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    cnpj_base = db.Column(db.String(8), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)          # cliente_atual | oportunidade_aberta | contato_antigo
    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=now, nullable=False)


class DuplicateAttempt(db.Model):
    __tablename__ = "duplicate_attempt"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    cnpj = db.Column(db.String(14), nullable=False)
    reason = db.Column(db.String(30), nullable=False)        # outro_parceiro | propria | base_comercial
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    disputed = db.Column(db.Boolean, default=False, nullable=False)
    resolved = db.Column(db.Boolean, default=False, nullable=False)


class ReviewRequest(db.Model):
    __tablename__ = "review_request"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    referral_id = db.Column(db.Integer, db.ForeignKey("referral.id"))
    cnpj = db.Column(db.String(14))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(12), default="aberto", nullable=False)   # aberto | resolvido
    resolution = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    resolved_at = db.Column(db.DateTime)

    partner = db.relationship("Partner")
    referral = db.relationship("Referral")


class Material(db.Model):
    __tablename__ = "material"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(12), default="video", nullable=False)   # video | pdf | link
    url = db.Column(db.String(400), nullable=False)
    profile = db.Column(db.String(40))                                 # perfil do playbook (opcional)
    position = db.Column(db.Integer, default=0, nullable=False)
    published = db.Column(db.Boolean, default=True, nullable=False)


class SurveyResponse(db.Model):
    __tablename__ = "survey_response"
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    calc_doubt = db.Column(db.Boolean, default=False, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now, nullable=False)


class Notification(db.Model):
    __tablename__ = "notification"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))
    key = db.Column(db.String(40), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    read_at = db.Column(db.DateTime)
    emailed_at = db.Column(db.DateTime)


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    actor = db.Column(db.String(120))
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.JSON)


class ReportEdition(db.Model):
    __tablename__ = "report_edition"
    id = db.Column(db.Integer, primary_key=True)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    edition = db.Column(db.String(12), nullable=False, unique=True)     # 2027-W05
    generated_at = db.Column(db.DateTime, default=now, nullable=False)
    metrics = db.Column(db.JSON)
    xlsx_path = db.Column(db.String(300))
    pdf_path = db.Column(db.String(300))


class DirectInvite(db.Model):
    """Convite da All Targets para os parceiros iniciais do piloto (sem convidante)."""
    __tablename__ = "direct_invite"
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(40), unique=True, nullable=False)
    note = db.Column(db.String(200))
    created_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    used_at = db.Column(db.DateTime)
    partner_id = db.Column(db.Integer, db.ForeignKey("partner.id"))


class Setting(db.Model):
    __tablename__ = "setting"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(400))
    updated_at = db.Column(db.DateTime, default=now, nullable=False)


def setting(key: str, default=None):
    row = db.session.get(Setting, key)
    return row.value if row else default


def set_setting(key: str, value: str):
    row = db.session.get(Setting, key)
    if row:
        row.value, row.updated_at = value, now()
    else:
        db.session.add(Setting(key=key, value=value))
