"""Retenção e anonimização de dados pessoais (LGPD).

Anonimizar, não apagar: os campos pessoais são limpos e a linha fica, para códigos, valores e relatórios continuarem
fechando. Nunca mexe no que tem obrigação legal de guarda: contratos, recebimentos, comissões, pagamentos e NFs.
Os prazos automáticos vêm das regras (retention_*); vazio = não aplicar, até o jurídico definir.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta

from .core import db, now, today
from .engine import add_months
from .models import (AuditLog, CommissionLine, Notification, Partner, PartnerInvoice, Referral, ReviewRequest,
                     SurveyResponse, TermAcceptance, User)
from .rules import active_ruleset
from .services import audit

MARK = "[anonimizado]"

# Campos pessoais da indicação: o decisor (terceiro que não se cadastrou) e textos livres que podem citá-lo.
REFERRAL_FIELDS = ("decisor_name", "decisor_role", "decisor_contact", "signal", "interest", "relationship",
                   "internal_notes", "next_action", "lost_reason", "reject_reason", "consent_evidence")
# Contato da pessoa física por trás do parceiro. CNPJ, razão social e dados financeiros ficam.
PARTNER_FIELDS = ("contact_name", "phone", "email", "city", "uf", "market_time", "company_size", "conflict_notes",
                  "bank", "bank_agency", "bank_account", "bank_holder_doc")
OPEN_LINE_STATES = ("aguardando_condicao", "aprovada", "programada")


class PrivacyError(Exception):
    pass


# ------------------------------------------------------------------ indicação
def referral_closed_at(ref: Referral) -> datetime | None:
    """Quando a indicação terminou sem contrato (None se ainda está em andamento ou tem contrato)."""
    if any(c.cancelled_at is None for c in ref.contracts):
        return None
    if ref.eligibility == "rejeitada":
        return ref.reviewed_at
    if ref.eligibility == "expirada":
        return datetime.combine(ref.protection_until, datetime.min.time()) if ref.protection_until else ref.reviewed_at
    if ref.eligibility == "aceita" and ref.stage == "perdida":
        return ref.stage_updated_at
    return None


def anonymize_referral(ref: Referral, actor: str, reason: str, keep_company: bool | None = None):
    """Limpa os dados pessoais da indicação. A empresa (CNPJ e nome) fica quando há contrato, porque o contrato e os
    recebimentos precisam dela. Não faz commit."""
    if ref.anonymized_at:
        raise PrivacyError("Os dados pessoais desta indicação já foram anonimizados.")
    if not (reason or "").strip():
        raise PrivacyError("Informe o motivo (por exemplo, o pedido do titular ou o prazo de retenção).")
    for f in REFERRAL_FIELDS:
        if getattr(ref, f):
            setattr(ref, f, MARK if f in ("signal", "reject_reason") else None)
    if keep_company is None:
        keep_company = bool(ref.contracts)
    if not keep_company:
        ref.company_name = None
    for rr in ReviewRequest.query.filter_by(referral_id=ref.id):
        rr.message = MARK
        rr.resolution = MARK if rr.resolution else None
    closed = _close_open_referral(ref)
    ref.anonymized_at = now()
    audit(actor, "privacy.anonymize_referral", "referral", ref.id, code=ref.code, reason=reason.strip(), closed=closed)


CLOSED_BY_HOLDER = "Encerrada: o indicado pediu a eliminação dos dados (LGPD)."


def _close_open_referral(ref: Referral) -> bool:
    """Sem os dados do indicado não há como seguir com o contato: a indicação em andamento (sem contrato) é
    encerrada e a empresa fica livre, como numa indicação não aceita. O parceiro é avisado."""
    from .services import notify
    if ref.contracts or referral_closed_at(ref) or ref.eligibility not in ("recebida", "em_validacao", "aceita"):
        return False                                    # tem contrato ou já terminou (não aceita, expirada, perdida)
    if ref.eligibility == "aceita":
        ref.stage, ref.stage_updated_at, ref.lost_reason = "perdida", now(), CLOSED_BY_HOLDER
    else:
        ref.eligibility, ref.reviewed_at, ref.reject_reason = "rejeitada", now(), CLOSED_BY_HOLDER
    ref.active, ref.waiting = False, False
    notify("indicacao_encerrada", f"A indicação {ref.protocol} foi encerrada: o indicado pediu a eliminação dos "
           "próprios dados (LGPD).", partner_id=ref.partner_id)
    return True


# ------------------------------------------------------------------ parceiro
def partner_blockers(p: Partner) -> list[str]:
    """O que impede anonimizar o parceiro agora (vazio = pode)."""
    out = []
    if p.anonymized_at:
        out.append("Os dados pessoais deste parceiro já foram anonimizados.")
        return out
    if p.status not in ("rejeitado", "suspenso"):
        out.append("Suspenda o parceiro antes (ou a candidatura precisa estar não aprovada).")
    n = CommissionLine.query.filter(CommissionLine.beneficiary_id == p.id, CommissionLine.state.in_(OPEN_LINE_STATES)).count()
    if n:
        out.append(f"Há {n} comissão(ões) ainda não paga(s) ou cancelada(s): conclua os pagamentos antes, porque "
                   "eles precisam dos dados bancários.")
    if PartnerInvoice.query.filter_by(partner_id=p.id, status="enviada").count():
        out.append("Há nota fiscal aguardando conferência.")
    return out


def anonymize_partner(p: Partner, actor: str, reason: str):
    """Limpa os dados da pessoa de contato e desativa o acesso. Ficam CNPJ, razão social, código, contratos,
    comissões, pagamentos e NFs (obrigação legal de guarda). Não faz commit."""
    from .security import end_sessions
    blockers = partner_blockers(p)
    if blockers:
        raise PrivacyError(" ".join(blockers))
    if not (reason or "").strip():
        raise PrivacyError("Informe o motivo (por exemplo, o pedido do titular ou o prazo de retenção).")
    old_emails = {e for e in [p.email] + [u.email for u in p.users] if e}
    for f in PARTNER_FIELDS:
        setattr(p, f, None)
    if p.reject_reason:
        p.reject_reason = MARK
    p.bank_verified = False
    p.invite_token = secrets.token_urlsafe(18)          # o link de convite antigo deixa de funcionar
    for u in p.users:
        end_sessions(u)
        Notification.query.filter_by(user_id=u.id).delete(synchronize_session=False)
        u.email = f"anonimizado-{u.id}@anonimizado.invalid"
        u.name = "Anonimizado"
        u.set_password(secrets.token_urlsafe(32))
        u.active, u.totp_secret, u.totp_last_step = False, None, None
    Notification.query.filter_by(partner_id=p.id).delete(synchronize_session=False)
    TermAcceptance.query.filter_by(partner_id=p.id).update({"ip": None}, synchronize_session=False)
    for s in SurveyResponse.query.filter_by(partner_id=p.id):
        s.comment = None
    for rr in ReviewRequest.query.filter_by(partner_id=p.id):
        rr.message = MARK
        rr.resolution = MARK if rr.resolution else None
    # a auditoria continua dizendo quem fez o quê, pelo código do parceiro em vez do e-mail
    if old_emails:
        AuditLog.query.filter(AuditLog.actor.in_(old_emails)).update({"actor": p.code}, synchronize_session=False)
        for log in AuditLog.query.filter(AuditLog.action == "email.refused"):
            if (log.details or {}).get("to") in old_emails:
                log.details = {**log.details, "to": MARK}
    p.anonymized_at = now()
    audit(actor, "privacy.anonymize_partner", "partner", p.id, code=p.code, reason=reason.strip())


def _partner_ended_at(p: Partner) -> datetime | None:
    if p.status == "rejeitado":
        return p.reviewed_at
    if p.status == "suspenso":
        last = (AuditLog.query.filter_by(action="partner.suspend", entity="partner", entity_id=p.id)
                .order_by(AuditLog.id.desc()).first())
        return last.at if last else None
    return None


# ------------------------------------------------------------------ prazos automáticos
def policy(rules: dict | None = None) -> dict:
    rules = rules or active_ruleset().rules
    return {k: rules.get(k) for k in ("retention_referral_months", "retention_partner_months",
                                       "retention_notification_months")}


def _cutoff(months, on: date) -> datetime | None:
    if not months:
        return None
    return datetime.combine(add_months(on, -int(months)), datetime.min.time())


def retention_preview(on: date | None = None) -> dict:
    """O que o prazo de retenção anonimizaria hoje. Nada é alterado."""
    on = on or today()
    pol = policy()
    out = {"policy": pol, "referrals": [], "partners": [], "partners_blocked": [], "notifications": 0}
    cut = _cutoff(pol["retention_referral_months"], on)
    if cut:
        for ref in Referral.query.filter(Referral.anonymized_at.is_(None), Referral.active.is_(False)
                                         | (Referral.stage == "perdida")):
            closed = referral_closed_at(ref)
            if closed and closed < cut:
                out["referrals"].append(ref)
    cut = _cutoff(pol["retention_partner_months"], on)
    if cut:
        for p in Partner.query.filter(Partner.anonymized_at.is_(None), Partner.status.in_(("rejeitado", "suspenso"))):
            ended = _partner_ended_at(p)
            if ended and ended < cut:
                (out["partners_blocked"] if partner_blockers(p) else out["partners"]).append(p)
    cut = _cutoff(pol["retention_notification_months"], on)
    if cut:
        out["notifications"] = Notification.query.filter(Notification.created_at < cut).count()
    return out


def apply_retention(actor: str = "sistema", on: date | None = None) -> dict:
    """Aplica os prazos definidos nas regras. Sem prazo definido, não faz nada."""
    on = on or today()
    prev = retention_preview(on)
    for ref in prev["referrals"]:
        anonymize_referral(ref, actor, "prazo de retenção", keep_company=False)
    for p in prev["partners"]:
        anonymize_partner(p, actor, "prazo de retenção")
    removed = 0
    cut = _cutoff(prev["policy"]["retention_notification_months"], on)
    if cut:
        removed = Notification.query.filter(Notification.created_at < cut).delete(synchronize_session=False)
    if prev["referrals"] or prev["partners"] or removed:
        audit(actor, "privacy.retention", None, None, referrals=len(prev["referrals"]), partners=len(prev["partners"]),
               notifications=removed, blocked=[p.code for p in prev["partners_blocked"]])
    db.session.commit()
    return {"indicacoes": len(prev["referrals"]), "parceiros": len(prev["partners"]),
            "parceiros_bloqueados": len(prev["partners_blocked"]), "avisos": removed}
