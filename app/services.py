"""Regras de negócio. As views só chamam estas funções."""
from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import extract, func
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from . import cnpj as cn
from . import core, engine as eng
from .core import db, now, today
from .models import (ACTIVE_ELIGIBILITY, TERM_KEYS, AuditLog, CommissionLine, Contract, DirectInvite, DuplicateAttempt,
                     Installment, KnownCompany, Notification, Partner, PartnerInvoice, Receipt, Referral,
                     ReviewRequest, TermAcceptance, TermDocument, User)
from .rules import (active_ruleset, holidays, recruit_cap_for_year, tax_rate_on)

UNAVAILABLE_MSG = "Esta empresa não está disponível para nova indicação. Solicite uma revisão se necessário."


class ServiceError(Exception):
    """Erro de regra de negócio com mensagem pronta para o usuário."""


class Unavailable(ServiceError):
    """Empresa indisponível para indicação (nunca revela quem já indicou)."""


# ================================================================== utilidades
def audit(actor: str, action: str, entity: str | None = None, entity_id: int | None = None, **details):
    db.session.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id, details=details or None))


def notify(key: str, message: str, partner_id: int | None = None, user_id: int | None = None, staff: bool = False):
    """Registra aviso na plataforma (painel). O envio por e-mail é feito por send_pending_emails()."""
    if staff:
        for u in User.query.filter(User.active.is_(True)).all():
            if u.role_set & {"manager", "admin"}:
                db.session.add(Notification(user_id=u.id, key=key, message=message))
    if partner_id:
        for u in User.query.filter_by(partner_id=partner_id, active=True).all():
            db.session.add(Notification(user_id=u.id, partner_id=partner_id, key=key, message=message))
    if user_id:
        db.session.add(Notification(user_id=user_id, key=key, message=message))


def _smtp_connect(cfg):
    import smtplib
    import ssl
    security = (cfg.get("SMTP_SECURITY") or ("ssl" if int(cfg["SMTP_PORT"]) == 465 else "starttls")).lower()
    if security not in ("starttls", "ssl", "none"):
        raise ValueError(f"FF_SMTP_SECURITY inválido: {security!r} (use starttls, ssl ou none).")
    # verifica certificado e nome do servidor (o padrão do smtplib não verifica)
    ctx = ssl.create_default_context(cafile=cfg.get("SMTP_CA_FILE") or None)
    if security == "ssl":
        smtp = smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"], context=ctx)
    else:
        smtp = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"])
    try:
        if security == "starttls":
            smtp.starttls(context=ctx)
        if cfg.get("SMTP_USER"):
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
    except Exception:
        smtp.close()
        raise
    return smtp


def _permanent_refusal(exc) -> tuple[int, str] | None:
    """Recusa definitiva (5xx) deste destinatário/mensagem; None = falha temporária ou da conexão."""
    import smtplib
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, msg = next(iter(exc.recipients.values()))
    elif isinstance(exc, (smtplib.SMTPDataError, smtplib.SMTPSenderRefused)):
        code, msg = exc.smtp_code, exc.smtp_error
    else:
        return None
    if not 500 <= code < 600:
        return None
    return code, msg.decode(errors="replace") if isinstance(msg, bytes) else str(msg)


def _email_queue():
    return Notification.query.filter(Notification.emailed_at.is_(None), Notification.user_id.isnot(None))


def email_backlog() -> dict:
    """Avisos que ainda sairiam por e-mail: quantos são e de quando é o mais antigo."""
    q = _email_queue()
    oldest = q.order_by(Notification.created_at).first()
    return {"pendentes": q.count(), "mais_antigo": oldest.created_at if oldest else None}


def discard_email_backlog(actor: str, older_than: datetime | None = None) -> int:
    """Marca avisos da fila como tratados sem enviar (continuam visíveis no portal). Sem `older_than`, descarta todos."""
    q = _email_queue()
    if older_than is not None:
        q = q.filter(Notification.created_at < older_than)
    n = q.update({"emailed_at": now()}, synchronize_session=False)
    if n:
        audit(actor, "email.discarded", "notification", None, count=n,
              older_than=older_than.isoformat(timespec="minutes") if older_than else None)
    db.session.commit()
    return n


def send_pending_emails(app) -> int:
    """Envia por SMTP os avisos ainda não enviados (se SMTP configurado). Rodar por cron.

    Cada aviso enviado é gravado na hora, então uma falha no meio do lote não gera reenvio.
    Endereço recusado em definitivo (5xx) é marcado como tratado e registrado na auditoria,
    para não travar a fila. Falha temporária (4xx, conexão) interrompe o lote; o resto fica
    para a próxima rodada."""
    cfg = app.config
    if not cfg.get("SMTP_HOST"):
        return 0
    from email.message import EmailMessage
    discard_email_backlog("sistema", now() - timedelta(days=cfg["EMAIL_MAX_AGE_DAYS"]))
    sent = 0
    pend = _email_queue().order_by(Notification.id).limit(200).all()
    if not pend:
        return 0
    with _smtp_connect(cfg) as smtp:
        for n in pend:
            user = db.session.get(User, n.user_id)
            if not user:
                continue
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], user.email, "Programa Finder Fee All Targets"
            msg.set_content(f"{n.message}\n\nAcesse: {cfg['PUBLIC_BASE_URL']}")
            try:
                smtp.send_message(msg)
            except Exception as e:  # noqa: BLE001 - classificada abaixo
                refused = _permanent_refusal(e)
                if refused is None:
                    raise
                n.emailed_at = now()
                audit("sistema", "email.refused", "notification", n.id, to=user.email, code=refused[0], reason=refused[1][:200])
                db.session.commit()
                continue
            n.emailed_at = now()
            db.session.commit()
            sent += 1
    return sent


def _code_for(prefix: str, row_id: int, width: int) -> str:
    """Código de relatório derivado do id (único pelo banco): envios simultâneos nunca colidem.
    No PostgreSQL a sequência pode pular números após um envio desfeito; o código continua único e estável."""
    return f"{prefix}{row_id:0{width}d}"


def is_web_link(url: str | None) -> bool:
    """Link que pode ir para um href: só http(s). Barra javascript:, data: e afins."""
    from urllib.parse import urlsplit
    u = (url or "").strip()
    if any(ord(ch) < 32 for ch in u):
        return False
    parts = urlsplit(u)
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def rules_now() -> dict:
    return active_ruleset().rules


def _hol(rules: dict) -> frozenset:
    return holidays(rules)


# ================================================================== parceiros
def recruit_slots_used() -> int:
    return Partner.query.filter(Partner.invited_by_id.isnot(None), Partner.status != "rejeitado").count()


def recruits_of(partner: Partner, year: int | None = None) -> int:
    """Recrutamentos usados pelo parceiro (teto conta pela data de aprovação do recrutado;
    candidaturas ainda em análise ocupam a vaga até serem rejeitadas)."""
    q = Partner.query.filter(Partner.invited_by_id == partner.id, Partner.status != "rejeitado")
    rows = q.all()
    if year is None:
        return len(rows)
    return sum(1 for r in rows if (r.approved_at or r.application_at).year == year)


def recruit_link_state(partner: Partner) -> tuple[bool, str]:
    """(aberto?, motivo). O contador de recrutamentos é visível ao parceiro; o motivo não expõe tetos internos."""
    rules = rules_now()
    if partner.status != "ativo":
        return False, "O convite fica disponível depois que seu acesso for liberado."
    year = today().year
    if recruits_of(partner, year) >= recruit_cap_for_year(rules, year):
        return False, "Você usou todos os recrutamentos previstos para este ano."
    if rules["pilot_mode"] and recruit_slots_used() >= rules["pilot_recruit_limit"]:
        return False, "Os convites do piloto estão encerrados."
    return True, ""


def apply_partner(data: dict, password: str, token: str | None = None, accept_notice: bool = False,
                  direct_token: str | None = None) -> Partner:
    """Candidatura por convite (de um parceiro ou direto da All Targets). Cria a conta de acesso,
    que só libera o portal depois de aprovação, workshop e termos."""
    rules = rules_now()
    if not token and not direct_token:
        raise ServiceError("A candidatura é feita por convite.")
    direct = None
    if direct_token:
        direct = DirectInvite.query.filter_by(token=direct_token, used_at=None).first()
        if not direct:
            raise ServiceError("Convite inválido ou já utilizado.")
    raw = data.get("cnpj", "")
    if not cn.is_valid(raw):
        raise ServiceError("CNPJ inválido. Confira os 14 dígitos.")
    num = cn.normalize(raw)
    if Partner.query.filter_by(cnpj=num).first():
        raise ServiceError("Já existe uma candidatura ou cadastro para este CNPJ. Fale com a All Targets se precisar de correção.")
    email = (data.get("email") or "").strip().lower()
    if "@" not in email or User.query.filter_by(email=email).first():
        raise ServiceError("Informe um e-mail válido e ainda não cadastrado.")
    if len(password or "") < 10:
        raise ServiceError("A senha precisa ter ao menos 10 caracteres.")
    if not (data.get("legal_name") or "").strip() or not (data.get("contact_name") or "").strip():
        raise ServiceError("Informe a razão social e o nome do responsável.")

    inviter = None
    if token:
        inviter = Partner.query.filter_by(invite_token=token).first()
        if not inviter:
            raise ServiceError("Link de convite inválido.")
        open_, reason = recruit_link_state(inviter)
        if not open_:
            raise ServiceError("Este convite não está mais disponível.")
        if not accept_notice:
            raise ServiceError("Para concluir, aceite o aviso sobre o bônus de expansão: a primeira comissão gera bônus a quem convidou.")
    p = Partner(
        cnpj=num, cnpj_base=cn.base(num), legal_name=data["legal_name"].strip(),
        trade_name=(data.get("trade_name") or "").strip() or None, kind=data.get("kind") or "outro",
        level=1, is_mei=bool(data.get("is_mei")), contact_name=data["contact_name"].strip(),
        phone=data.get("phone"), email=email, city=data.get("city"), uf=(data.get("uf") or "").upper()[:2] or None,
        market_time=data.get("market_time"), company_size=data.get("company_size"),
        invited_by_id=inviter.id if inviter else None, is_pilot_direct=inviter is None,
        bonus_notice_accepted_at=now() if inviter else None,
        invite_token=secrets.token_urlsafe(18), status="candidato",
    )
    db.session.add(p)
    db.session.flush()
    p.code = _code_for("P", p.id, 2)
    u = User(email=email, name=p.contact_name, roles="participant", partner_id=p.id)
    u.set_password(password)
    db.session.add(u)
    if direct:
        direct.used_at, direct.partner_id = now(), p.id
    audit(email, "partner.apply", "partner", p.id, invited_by=inviter.code if inviter else None)
    notify("nova_candidatura", f"Nova candidatura: {p.display_name} ({p.code}).", staff=True)
    db.session.commit()
    return p


def create_direct_invite(actor: str, note: str = "") -> DirectInvite:
    inv = DirectInvite(token=secrets.token_urlsafe(18), note=note, created_by=actor)
    db.session.add(inv)
    audit(actor, "invite.direct", "direct_invite", None, note=note)
    db.session.commit()
    return inv


def link_inviter(partner: Partner, inviter: Partner | None, reason: str, actor: str):
    """Correção de vínculo (cadastro sem link), com evidência e aprovação administrativa."""
    if not reason:
        raise ServiceError("Informe a evidência da correção.")
    partner.invited_by_id = inviter.id if inviter else None
    partner.is_pilot_direct = inviter is None
    audit(actor, "partner.link_inviter", "partner", partner.id, inviter=inviter.code if inviter else None, reason=reason)
    db.session.commit()


def review_partner(partner: Partner, approve: bool, actor: str, reason: str | None = None,
                   level: int | None = None, kind: str | None = None) -> Partner:
    if partner.status not in ("candidato", "em_analise"):
        raise ServiceError("Esta candidatura já foi decidida.")
    rules = rules_now()
    if approve:
        if not partner.cnpj_verified:
            raise ServiceError("Confirme a situação ativa do CNPJ antes de aprovar.")
        if rules["pilot_mode"] and partner.is_pilot_direct:
            direct = Partner.query.filter(Partner.is_pilot_direct.is_(True), Partner.status.in_(("aprovado", "ativo")),
                                          Partner.id != partner.id).count()
            if direct >= rules["pilot_max_partners"]:
                raise ServiceError(f"O piloto admite até {rules['pilot_max_partners']} parceiros iniciais.")
        if level:
            partner.level = int(level)
        if kind:
            partner.kind = kind
        partner.status, partner.approved_at = "aprovado", now()
    else:
        if not reason:
            raise ServiceError("Informe o motivo da não aprovação.")
        partner.status, partner.reject_reason = "rejeitado", reason
    partner.reviewed_at = now()
    audit(actor, "partner.approve" if approve else "partner.reject", "partner", partner.id, reason=reason)
    notify("candidatura_decidida",
           "Sua candidatura foi aprovada. Falta concluir o workshop e aceitar os termos." if approve
           else f"Sua candidatura não foi aprovada. Motivo: {reason}", partner_id=partner.id)
    try_activate(partner)
    db.session.commit()
    return partner


def mark_workshop(partner: Partner, actor: str, when: datetime | None = None):
    if partner.status not in ("aprovado", "ativo"):
        raise ServiceError("O workshop vale a partir da aprovação da candidatura.")
    partner.workshop_at = when or now()
    audit(actor, "partner.workshop", "partner", partner.id)
    try_activate(partner)
    db.session.commit()


def pending_terms(partner: Partner) -> list[TermDocument]:
    docs = TermDocument.query.filter(TermDocument.key.in_(list(TERM_KEYS))).all()
    accepted = {(a.term_key, a.version) for a in TermAcceptance.query.filter_by(partner_id=partner.id)}
    return [d for d in docs if (d.key, d.version) not in accepted]


def accept_term(partner: Partner, key: str, ip: str | None = None):
    doc = db.session.get(TermDocument, key)
    if not doc:
        raise ServiceError("Termo não encontrado.")
    if not doc.legal_approved:
        raise ServiceError("Este termo ainda não foi liberado pelo jurídico da All Targets.")
    if partner.status not in ("aprovado", "ativo"):
        raise ServiceError("Os termos são aceitos depois da aprovação da candidatura.")
    if not TermAcceptance.query.filter_by(partner_id=partner.id, term_key=key, version=doc.version).first():
        db.session.add(TermAcceptance(partner_id=partner.id, term_key=key, version=doc.version, ip=ip))
        audit(partner.email or "parceiro", "term.accept", "partner", partner.id, key=key, version=doc.version)
    db.session.flush()
    try_activate(partner)
    db.session.commit()


def try_activate(partner: Partner) -> bool:
    """Acesso ao portal só depois de: aprovação, workshop e todos os termos aceitos."""
    if partner.status != "aprovado" or not partner.workshop_at:
        return False
    if TermDocument.query.count() < len(TERM_KEYS) or pending_terms(partner):
        return False
    partner.status, partner.activated_at, partner.terms_accepted_at = "ativo", now(), now()
    audit("sistema", "partner.activate", "partner", partner.id)
    notify("acesso_liberado", "Seu acesso ao portal foi liberado.", partner_id=partner.id)
    return True


def receiving_block_reasons(partner: Partner) -> list[str]:
    out = []
    if not partner.cnpj_verified:
        out.append("A situação ativa do CNPJ ainda não foi confirmada pela All Targets.")
    if not (partner.bank and partner.bank_account and partner.bank_holder_doc):
        out.append("Informe os dados bancários da empresa em Meu cadastro.")
    elif cn.digits(partner.bank_holder_doc) != partner.cnpj:
        out.append("A conta bancária precisa estar no CNPJ da empresa parceira.")
    elif not partner.bank_verified:
        out.append("A titularidade da conta ainda não foi conferida pela All Targets.")
    return out


# ================================================================== indicações
def _dedupe_key(rules: dict, num: str) -> str:
    return cn.base(num) if rules["duplicate_scope"] == "cnpj_base" else num


def check_availability(partner: Partner, raw_cnpj: str) -> str:
    """Verificação prévia (passo 2). Devolve 'ok' ou levanta Unavailable/ServiceError."""
    rules = rules_now()
    if not cn.is_valid(raw_cnpj):
        raise ServiceError("CNPJ inválido. Confira os 14 dígitos.")
    num = cn.normalize(raw_cnpj)
    key = _dedupe_key(rules, num)
    col = Referral.cnpj_base if rules["duplicate_scope"] == "cnpj_base" else Referral.cnpj
    ref_key = cn.base(num) if rules["duplicate_scope"] == "cnpj_base" else num
    kc_col = KnownCompany.cnpj_base if rules["duplicate_scope"] == "cnpj_base" else KnownCompany.cnpj
    for kc in KnownCompany.query.filter(kc_col == ref_key).all():
        if kc.kind in ("cliente_atual", "oportunidade_aberta") or rules["old_contact_policy"] == "block":
            db.session.add(DuplicateAttempt(partner_id=partner.id, cnpj=num, reason="base_comercial"))
            db.session.commit()
            raise Unavailable(UNAVAILABLE_MSG)
    existing = Referral.query.filter(col == ref_key, Referral.active.is_(True)).first()
    if existing:
        own = existing.partner_id == partner.id
        db.session.add(DuplicateAttempt(partner_id=partner.id, cnpj=num, reason="propria" if own else "outro_parceiro"))
        db.session.commit()
        if own:
            raise Unavailable(f"Você já indicou esta empresa (protocolo {existing.protocol}).")
        raise Unavailable(UNAVAILABLE_MSG)
    return "ok"


def register_referral(partner: Partner, data: dict) -> Referral:
    if partner.status != "ativo":
        raise ServiceError("Seu acesso ao programa ainda não está ativo.")
    rules = rules_now()
    check_availability(partner, data.get("cnpj", ""))
    num = cn.normalize(data["cnpj"])
    if not (data.get("signal") or "").strip():
        raise ServiceError("Descreva o sinal que você observou e que motivou a indicação.")
    if not (data.get("decisor_name") or "").strip():
        raise ServiceError("Informe o nome do decisor na empresa.")
    old_contact = KnownCompany.query.filter(
        (KnownCompany.cnpj_base if rules["duplicate_scope"] == "cnpj_base" else KnownCompany.cnpj)
        == (cn.base(num) if rules["duplicate_scope"] == "cnpj_base" else num),
        KnownCompany.kind == "contato_antigo").first() is not None
    ref = Referral(
        partner_id=partner.id, cnpj=num, cnpj_base=cn.base(num), dedupe_key=_dedupe_key(rules, num),
        company_name=(data.get("company_name") or "").strip() or None,
        decisor_name=data["decisor_name"].strip(), decisor_role=data.get("decisor_role"),
        decisor_contact=data.get("decisor_contact"), signal=data["signal"].strip(),
        interest=data.get("interest"), relationship=data.get("relationship"),
        eligibility="em_validacao" if old_contact else "recebida", old_contact_flag=old_contact,
        consent_status="pendente" if rules["consent_required"] else "registrado",
        consent_at=None if rules["consent_required"] else now(),
    )
    db.session.add(ref)
    try:
        db.session.flush()
    except IntegrityError:
        # envio simultâneo: outro registro chegou primeiro (prioridade por data e hora)
        db.session.rollback()
        db.session.add(DuplicateAttempt(partner_id=partner.id, cnpj=num, reason="outro_parceiro", disputed=True))
        db.session.commit()
        raise Unavailable(UNAVAILABLE_MSG)
    ref.protocol = f"IND-{ref.created_at.year}-{ref.id:05d}"
    ref.code = _code_for("I-", ref.id, 3)
    audit(partner.email or partner.code, "referral.register", "referral", ref.id, code=ref.code)
    notify("nova_indicacao", f"Nova indicação {ref.protocol} de {partner.display_name}.", staff=True)
    db.session.commit()
    return ref


def _year_accepted_count(partner_id: int, year: int, exclude_id: int | None = None) -> int:
    q = Referral.query.filter(Referral.partner_id == partner_id, Referral.accepted_at.isnot(None),
                              extract("year", Referral.accepted_at) == year)
    if exclude_id:
        q = q.filter(Referral.id != exclude_id)
    return q.count()


def period_bounds(period: str, ref_day: date) -> tuple[date, date]:
    if period == "year":
        return date(ref_day.year, 1, 1), date(ref_day.year + 1, 1, 1)
    q0 = 3 * ((ref_day.month - 1) // 3) + 1
    start = date(ref_day.year, q0, 1)
    return start, eng.add_months(start, 3)


def aggregate_used(rules: dict, ref_day: date | None = None) -> int:
    start, end = period_bounds(rules["aggregate_cap_period"], ref_day or today())
    return Referral.query.filter(Referral.service_started_at.isnot(None),
                                 Referral.service_started_at >= datetime.combine(start, datetime.min.time()),
                                 Referral.service_started_at < datetime.combine(end, datetime.min.time())).count()


def _capacity_block(ref: Referral, rules: dict) -> str | None:
    year = (ref.accepted_at or now()).year
    if _year_accepted_count(ref.partner_id, year, ref.id) >= int(rules["annual_cap_per_partner"]):
        return "teto_parceiro"
    cap = rules.get("aggregate_cap")
    if cap is not None and aggregate_used(rules) >= int(cap):
        return "teto_agregado"
    return None


def validate_referral(ref: Referral, action: str, actor: str, reason: str | None = None,
                      fits_target: bool | None = None) -> Referral:
    if ref.eligibility not in ("recebida", "em_validacao"):
        raise ServiceError("Esta indicação já foi decidida.")
    if action == "start_review":
        ref.eligibility = "em_validacao"
    elif action == "accept":
        if ref.partner.status != "ativo":
            raise ServiceError("O parceiro não está ativo.")
        ref.eligibility, ref.accepted_at = "aceita", now()
        ref.rule_set_id = active_ruleset().id        # a regra vigente na data de aceite fica congelada
        ref.fits_target = fits_target
        block = _capacity_block(ref, rules_now())
        if block:
            ref.waiting, ref.waiting_reason, ref.protection_until = True, block, None
        else:
            _start_service(ref)
    elif action == "reject":
        if not reason:
            raise ServiceError("Informe o motivo. O parceiro verá este texto.")
        ref.eligibility, ref.reject_reason, ref.active = "rejeitada", reason, False
        ref.fits_target = fits_target
    else:
        raise ServiceError("Ação inválida.")
    ref.reviewed_at, ref.reviewed_by = now(), actor
    audit(actor, f"referral.{action}", "referral", ref.id, reason=reason)
    if action == "accept":
        msg = (f"Indicação {ref.protocol} aceita. Aguardando início do atendimento." if ref.waiting
               else f"Indicação {ref.protocol} aceita e protegida.")
        notify("indicacao_aceita", msg, partner_id=ref.partner_id)
    elif action == "reject":
        notify("indicacao_rejeitada", f"Indicação {ref.protocol} não aceita. Motivo: {reason}", partner_id=ref.partner_id)
    db.session.commit()
    return ref


def _start_service(ref: Referral):
    rules = rules_now()
    ref.service_started_at = now()
    ref.waiting, ref.waiting_reason = False, None
    ref.protection_until = today() + timedelta(days=int(rules["protection_days"]))


def start_service(ref: Referral, actor: str, override_reason: str | None = None):
    """Tira a indicação da fila de espera interna e inicia o atendimento (e a proteção)."""
    if ref.eligibility != "aceita" or not ref.waiting:
        raise ServiceError("A indicação não está em espera.")
    block = _capacity_block(ref, rules_now())
    if block and not override_reason:
        raise ServiceError("O teto do período foi atingido. Informe o motivo para liberar mesmo assim.")
    _start_service(ref)
    audit(actor, "referral.start_service", "referral", ref.id, override=override_reason)
    notify("atendimento_iniciado", f"Indicação {ref.protocol}: atendimento iniciado.", partner_id=ref.partner_id)
    db.session.commit()


def extend_protection(ref: Referral, days: int, reason: str, actor: str):
    if not reason:
        raise ServiceError("Informe o motivo da prorrogação.")
    if ref.eligibility != "aceita" or ref.waiting:
        raise ServiceError("Só é possível prorrogar a proteção de indicação aceita e em atendimento.")
    base = max(ref.protection_until or today(), today())
    ref.protection_until = base + timedelta(days=int(days))
    audit(actor, "referral.extend_protection", "referral", ref.id, days=days, reason=reason)
    db.session.commit()


def expire_protections(on: date | None = None) -> int:
    on = on or today()
    n = 0
    for ref in Referral.query.filter(Referral.eligibility == "aceita", Referral.waiting.is_(False),
                                     Referral.protection_until.isnot(None), Referral.protection_until < on).all():
        if any(c.cancelled_at is None for c in ref.contracts):
            continue
        ref.eligibility, ref.active = "expirada", False
        audit("sistema", "referral.expire", "referral", ref.id)
        notify("indicacao_expirada", f"A proteção da indicação {ref.protocol} expirou.", partner_id=ref.partner_id)
        n += 1
    db.session.commit()
    return n


def register_consent(ref: Referral, evidence: str, actor: str):
    if not evidence:
        raise ServiceError("Informe como o consentimento foi registrado (ex.: aceite eletrônico, e-mail, data).")
    ref.consent_status, ref.consent_at, ref.consent_evidence = "registrado", now(), evidence
    audit(actor, "referral.consent", "referral", ref.id)
    db.session.commit()


def set_stage(ref: Referral, stage: str, actor: str, lost_reason: str | None = None, next_action: str | None = None):
    rules = rules_now()
    if stage == "ganha":
        raise ServiceError("Para marcar como ganha, registre o contrato.")
    if ref.eligibility != "aceita":
        raise ServiceError("A etapa comercial só muda em indicação aceita.")
    if ref.waiting and stage != "aguardando_contato":
        raise ServiceError("A indicação aguarda início do atendimento.")
    if stage != "aguardando_contato" and rules["consent_required"] and ref.consent_status != "registrado":
        raise ServiceError("Registre o consentimento do indicado (LGPD) antes do contato comercial.")
    if stage == "perdida" and not lost_reason:
        raise ServiceError("Informe o motivo da perda.")
    if stage != "aguardando_contato" and not ref.first_contact_at:
        ref.first_contact_at = now()
    ref.stage, ref.stage_updated_at, ref.lost_reason = stage, now(), lost_reason
    ref.next_action = (next_action or "").strip() or None
    audit(actor, "referral.stage", "referral", ref.id, stage=stage)
    db.session.commit()


# ================================================================== contratos e parcelas
def win_contract(ref: Referral, product: str, audience: str | None, signed_at: date,
                 items: list, actor: str) -> Contract:
    rules = ref.rule_set.rules if ref.rule_set else rules_now()
    if ref.eligibility != "aceita" or ref.waiting:
        raise ServiceError("O contrato só pode ser registrado em indicação aceita e em atendimento.")
    if rules_now()["consent_required"] and ref.consent_status != "registrado":
        raise ServiceError("Registre o consentimento do indicado (LGPD) antes de registrar o contrato.")
    if ref.protection_until and signed_at > ref.protection_until:
        raise ServiceError("A proteção da indicação já tinha terminado na data da assinatura. Prorrogue a proteção com motivo, se for o caso.")
    if not items:
        raise ServiceError("Informe ao menos um item de cobrança.")
    if not product:
        raise ServiceError("Informe o produto contratado.")
    plan = eng.build_schedule(signed_at, items, int(rules["window_months"]), bool(rules["window_end_inclusive"]))
    partner = ref.partner
    first, recipient = False, None
    if partner.invited_by_id:
        prior = (Contract.query.join(Referral, Contract.referral_id == Referral.id)
                 .filter(Referral.partner_id == partner.id, Contract.cancelled_at.is_(None)).count())
        first, recipient = prior == 0, partner.invited_by_id
    c = Contract(referral_id=ref.id, product=product, audience=audience, signed_at=signed_at,
                 first_for_recruited=first and bool(recipient), bonus_recipient_id=recipient if first else None)
    db.session.add(c)
    db.session.flush()
    for p in plan:
        db.session.add(Installment(contract_id=c.id, item_index=p.item_index, description=p.description, kind=p.kind,
                                   number=p.number, total=p.total, due_date=p.due_date, gross_amount=p.gross_amount,
                                   in_window=p.in_window))
    ref.stage, ref.stage_updated_at = "ganha", now()
    audit(actor, "contract.win", "contract", c.id, product=product, parcels=len(plan))
    notify("contrato_assinado", f"Indicação {ref.protocol}: contrato assinado. As comissões seguem o recebimento das parcelas.",
           partner_id=ref.partner_id)
    db.session.commit()
    return c


def cancel_contract(c: Contract, when: date, reason: str, actor: str):
    if c.cancelled_at:
        raise ServiceError("Contrato já cancelado.")
    if not reason:
        raise ServiceError("Informe o motivo do cancelamento.")
    c.cancelled_at, c.cancel_reason = when, reason
    for i in c.installments:
        if i.status == "aberta" and not i.receipts:
            i.status = "cancelada"
    audit(actor, "contract.cancel", "contract", c.id, reason=reason)
    db.session.commit()


def apply_discount(inst: Installment, new_gross: Decimal, reason: str, actor: str):
    if inst.receipts:
        raise ServiceError("Parcela com recebimento: use estorno ou ajuste.")
    if new_gross <= 0 or not reason:
        raise ServiceError("Informe o novo valor (positivo) e o motivo.")
    audit(actor, "installment.discount", "installment", inst.id, old=str(inst.gross_amount), new=str(new_gross), reason=reason)
    inst.gross_amount = eng.q(new_gross)
    db.session.commit()


# ================================================================== recebimento e comissões
def eligibility_hold(line_kind: str, ref: Referral, beneficiary: Partner, rules: dict) -> str | None:
    if beneficiary.status != "ativo":
        return "Parceiro não está ativo."
    if beneficiary.level not in rules["eligible_levels"]:
        return "Perfil de Nível 3 não gera comissão direta."
    if line_kind == "cliente":
        if ref.eligibility != "aceita":
            return "Indicação não está aceita."
        year = (ref.accepted_at or now()).year
        ordered = (Referral.query.filter(Referral.partner_id == ref.partner_id, Referral.accepted_at.isnot(None),
                                         extract("year", Referral.accepted_at) == year)
                   .order_by(Referral.accepted_at, Referral.id).all())
        rank = [r.id for r in ordered].index(ref.id) + 1 if ref in ordered else 1
        if rank > int(rules["annual_cap_per_partner"]):
            return "Fora do limite anual de indicações."
    return None


def register_receipt(inst: Installment, received_at: date, amount: Decimal, nf_number: str, nf_value: Decimal,
                     taxes: Decimal, actor: str, tax_detail: dict | None = None) -> tuple[Receipt, list, list]:
    """Registra recebimento (ou estorno, se negativo) e gera as linhas de comissão e bônus.
    Devolve (recebimento, linhas, avisos)."""
    c = inst.contract
    ref = c.referral
    rules = ref.rule_set.rules if ref.rule_set else rules_now()
    amount, nf_value, taxes = eng.q(amount), eng.q(nf_value), eng.q(taxes)
    if inst.status == "cancelada" or c.cancelled_at and amount > 0:
        raise ServiceError("Parcela ou contrato cancelado: não aceita novo recebimento.")
    if amount == 0:
        raise ServiceError("Informe um valor diferente de zero.")
    try:
        eng.net_ratio(nf_value, taxes)
    except ValueError as e:
        raise ServiceError(str(e))
    received_total = sum((r.amount for r in inst.receipts), Decimal("0"))
    if amount > 0 and received_total + amount > nf_value + Decimal("0.01"):
        raise ServiceError("O total recebido não pode passar do valor da NF desta parcela.")
    if amount < 0 and received_total + amount < 0:
        raise ServiceError("O estorno não pode passar do total já recebido.")
    warnings = []
    rate = tax_rate_on(rules, inst.due_date)
    if abs(taxes / nf_value - rate) > Decimal(rules["tax_tolerance"]):
        warnings.append(f"Impostos informados ({taxes / nf_value:.1%}) diferem da alíquota de referência ({rate:.1%}). Confira a NF.")
    rc = Receipt(installment_id=inst.id, received_at=received_at, amount=amount, nf_number=nf_number, nf_value=nf_value,
                 taxes=taxes, tax_detail=tax_detail, kind="recebimento" if amount > 0 else "estorno", registered_by=actor)
    db.session.add(rc)
    db.session.flush()
    total_after = received_total + amount
    inst.status = "recebida" if total_after >= nf_value - Decimal("0.005") else "aberta"
    lines = []
    if inst.in_window:
        pct = eng.resolve_pct(rules, c.product, inst.kind, c.audience)
        com = eng.commission(amount, nf_value, taxes, pct)
        base = eng.net_base(amount, nf_value, taxes)
        hold = eligibility_hold("cliente", ref, ref.partner, rules)
        line = CommissionLine(
            receipt_id=rc.id, kind="cliente", beneficiary_id=ref.partner_id, referral_id=ref.id, rule_set_id=ref.rule_set_id,
            amount=com, pct_applied=str(pct), base_net=base, state="aguardando_condicao",
            hold_reason=hold or "Aguardando conferência do financeiro.",
            detail={"formula": "valor recebido × (1 − impostos ÷ valor da NF) × percentual", "received": str(amount),
                    "nf_value": str(nf_value), "taxes": str(taxes), "pct": str(pct), "window_months": rules["window_months"],
                    "parcel": f"{inst.description} {inst.number}/{inst.total}", "due_date": inst.due_date.isoformat()})
        db.session.add(line)
        lines.append(line)
        if c.first_for_recruited and c.bonus_recipient_id:
            recip = db.session.get(Partner, c.bonus_recipient_id)
            bpct = Decimal(rules["bonus_pct"])
            bonus_amt = eng.bonus(com, bpct)
            bhold = eligibility_hold("bonus", ref, recip, rules)
            bline = CommissionLine(
                receipt_id=rc.id, kind="bonus", beneficiary_id=recip.id, referral_id=ref.id, source_partner_id=ref.partner_id,
                rule_set_id=ref.rule_set_id, amount=bonus_amt, pct_applied=str(bpct), base_net=None, state="aguardando_condicao",
                hold_reason=bhold or "Aguardando conferência do financeiro.",
                detail={"formula": "percentual do bônus × comissão do parceiro convidado (mesma parcela)", "pct": str(bpct),
                        "parcel": f"{inst.description} {inst.number}/{inst.total}"})
            db.session.add(bline)
            lines.append(bline)
    audit(actor, "receipt.register", "receipt", rc.id, amount=str(amount), installment=inst.id)
    db.session.commit()
    return rc, lines, warnings


def add_adjustment(partner: Partner, amount: Decimal, reason: str, actor: str) -> CommissionLine:
    if not reason or eng.q(amount) == 0:
        raise ServiceError("Informe valor diferente de zero e o motivo.")
    line = CommissionLine(kind="ajuste", beneficiary_id=partner.id, amount=eng.q(amount), state="aprovada", note=reason,
                          hold_reason=None, approved_at=now(), approved_by=actor, detail={"reason": reason})
    db.session.add(line)
    audit(actor, "adjustment.add", "partner", partner.id, amount=str(amount), reason=reason)
    db.session.commit()
    return line


def approve_line(line: CommissionLine, actor: str):
    if line.state != "aguardando_condicao":
        raise ServiceError("A linha não está aguardando conferência.")
    ref = line.referral
    rules = ref.rule_set.rules if ref and ref.rule_set else rules_now()
    hold = eligibility_hold(line.kind, ref, line.beneficiary, rules) if ref else None
    if hold:
        line.hold_reason = hold
        db.session.commit()
        raise ServiceError(hold)
    line.state, line.hold_reason, line.approved_at, line.approved_by = "aprovada", None, now(), actor
    audit(actor, "line.approve", "commission_line", line.id)
    kind = "bônus de expansão" if line.kind == "bonus" else "comissão"
    notify("comissao_aprovada", f"Uma {kind} de {core.brl(line.amount)} foi aprovada. Emita e anexe a nota fiscal.",
           partner_id=line.beneficiary_id)
    db.session.commit()


def mark_checked(line: CommissionLine, ok: bool, note: str | None, actor: str):
    """Conferência de 100% das parcelas contra a planilha (critério do piloto)."""
    if not ok and not note:
        raise ServiceError("Descreva a divergência encontrada.")
    line.checked, line.divergence_note = ok, None if ok else note
    audit(actor, "line.check", "commission_line", line.id, ok=ok, note=note)
    db.session.commit()


# ---------------------------------------------------------------- nota fiscal e pagamento
def _save_upload(app_config: dict, partner: Partner, file_storage) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in app_config["ALLOWED_UPLOADS"]:
        raise ServiceError("Formato de arquivo não aceito. Envie PDF, XML, PNG ou JPG.")
    folder = os.path.join(app_config["UPLOAD_DIR"], f"partner_{partner.id}")
    os.makedirs(folder, exist_ok=True)
    name = f"{now():%Y%m%d%H%M%S}_{secrets.token_hex(3)}_{secure_filename(file_storage.filename)}"
    path = os.path.join(folder, name)
    file_storage.save(path)
    return path


def attach_invoice(app_config: dict, partner: Partner, number: str, issue_date: date, value: Decimal,
                   file_storage, line_ids: list[int]) -> PartnerInvoice:
    lines = CommissionLine.query.filter(CommissionLine.id.in_(line_ids or [0]), CommissionLine.beneficiary_id == partner.id).all()
    if not lines or len(lines) != len(set(line_ids)):
        raise ServiceError("Selecione as comissões que a nota cobre.")
    if any(l.state != "aprovada" for l in lines):
        raise ServiceError("Só é possível anexar nota a comissões aprovadas.")
    total = sum((l.amount for l in lines), Decimal("0"))
    if total <= 0:
        raise ServiceError("O valor líquido das comissões selecionadas precisa ser positivo.")
    if abs(eng.q(value) - total) > Decimal("0.01"):
        raise ServiceError(f"O valor da nota ({core.brl(value)}) deve ser igual ao total das comissões selecionadas ({core.brl(total)}).")
    if not number:
        raise ServiceError("Informe o número da nota fiscal.")
    path = _save_upload(app_config, partner, file_storage)
    inv = PartnerInvoice(partner_id=partner.id, number=number, issue_date=issue_date, value=eng.q(value), file_path=path)
    db.session.add(inv)
    db.session.flush()
    rules = rules_now()
    upload_day = today()
    cycles = []
    for l in lines:
        base_day = l.receipt.received_at if l.receipt else upload_day
        cycles.append(eng.payment_cycle(base_day, upload_day, rules["nf_deadline_day"]))
    cycle = max(cycles)
    for l in lines:
        l.invoice_id, l.cycle, l.state, l.hold_reason = inv.id, cycle, "programada", None
    audit(partner.email or partner.code, "invoice.attach", "partner_invoice", inv.id, lines=line_ids)
    notify("nf_recebida", f"NF {number} de {partner.display_name} recebida para o ciclo {cycle:%m/%Y}.", staff=True)
    db.session.commit()
    return inv


def review_invoice(inv: PartnerInvoice, ok: bool, reason: str | None, actor: str):
    if inv.status != "enviada":
        raise ServiceError("Esta nota já foi conferida.")
    lines = CommissionLine.query.filter_by(invoice_id=inv.id).all()
    if ok:
        inv.status = "conferida"
    else:
        if not reason:
            raise ServiceError("Informe o motivo da recusa.")
        inv.status, inv.reject_reason = "rejeitada", reason
        for l in lines:
            l.invoice_id, l.cycle, l.state = None, None, "aprovada"
        notify("nf_rejeitada", f"A NF {inv.number} não foi aceita. Motivo: {reason}. Envie uma nova nota.", partner_id=inv.partner_id)
    audit(actor, "invoice.review", "partner_invoice", inv.id, ok=ok, reason=reason)
    db.session.commit()


def pay_cycle(partner: Partner, cycle: date, reference: str, actor: str) -> dict:
    """Paga as linhas programadas até o ciclo. Saldo líquido negativo (estornos) acumula para o mês seguinte."""
    blocks = receiving_block_reasons(partner)
    if blocks:
        raise ServiceError("Pagamento bloqueado: " + " ".join(blocks))
    lines = CommissionLine.query.filter(CommissionLine.beneficiary_id == partner.id, CommissionLine.state == "programada",
                                        CommissionLine.cycle <= cycle).all()
    if not lines:
        raise ServiceError("Não há comissões programadas para este ciclo.")
    if any(l.invoice and l.invoice.status != "conferida" for l in lines):
        raise ServiceError("Há nota fiscal ainda não conferida. Confira a NF antes de pagar.")
    if not reference:
        raise ServiceError("Informe a referência do pagamento (comprovante ou transferência).")
    total = sum((l.amount for l in lines), Decimal("0"))
    if total <= 0:
        nxt = eng.next_month(cycle)
        for l in lines:
            l.cycle = nxt
        audit(actor, "payment.carry", "partner", partner.id, total=str(total))
        db.session.commit()
        return {"paid": Decimal("0"), "carried": True, "total": total}
    for l in lines:
        l.state, l.paid_at, l.paid_reference = "paga", today(), reference
    audit(actor, "payment.pay", "partner", partner.id, total=str(total), cycle=cycle.isoformat(), ref=reference)
    notify("pagamento", f"Pagamento de {core.brl(total)} realizado ({reference}).", partner_id=partner.id)
    db.session.commit()
    return {"paid": total, "carried": False, "total": total}


# ---------------------------------------------------------------- projeções (estimada)
def projected_lines(partner: Partner) -> list[dict]:
    """Parcelas na janela ainda sem recebimento: estimativa, nunca saldo disponível."""
    out = []
    rules_default = rules_now()
    refs = Referral.query.filter_by(partner_id=partner.id).all()
    for ref in refs:
        rules = ref.rule_set.rules if ref.rule_set else rules_default
        for c in ref.contracts:
            if c.cancelled_at:
                continue
            for i in c.installments:
                if i.status != "aberta" or i.receipts or not i.in_window:
                    continue
                pct = eng.resolve_pct(rules, c.product, i.kind, c.audience)
                est = eng.estimate_commission(i.gross_amount, pct, tax_rate_on(rules, i.due_date))
                out.append({"kind": "cliente", "ref": ref, "due": i.due_date, "label": f"{i.description} {i.number}/{i.total}",
                            "amount": est})
    for c in Contract.query.filter(Contract.bonus_recipient_id == partner.id, Contract.cancelled_at.is_(None)).all():
        rules = c.referral.rule_set.rules if c.referral.rule_set else rules_default
        for i in c.installments:
            if i.status != "aberta" or i.receipts or not i.in_window:
                continue
            pct = eng.resolve_pct(rules, c.product, i.kind, c.audience)
            est = eng.bonus(eng.estimate_commission(i.gross_amount, pct, tax_rate_on(rules, i.due_date)), Decimal(rules["bonus_pct"]))
            out.append({"kind": "bonus", "ref": None, "due": i.due_date, "label": f"Bônus de expansão — {i.description} {i.number}/{i.total}",
                        "amount": est, "source": c.referral.partner})
    return sorted(out, key=lambda r: r["due"])


# ================================================================== suporte
def request_review(partner: Partner, message: str, referral: Referral | None = None, raw_cnpj: str | None = None):
    if not (message or "").strip():
        raise ServiceError("Descreva o que deve ser revisado.")
    rr = ReviewRequest(partner_id=partner.id, referral_id=referral.id if referral else None,
                       cnpj=cn.normalize(raw_cnpj) if raw_cnpj else (referral.cnpj if referral else None), message=message.strip())
    db.session.add(rr)
    notify("pedido_revisao", f"Pedido de revisão de {partner.display_name}.", staff=True)
    db.session.commit()
    return rr


def resolve_review(rr: ReviewRequest, resolution: str, actor: str):
    if not resolution:
        raise ServiceError("Escreva a resposta ao parceiro.")
    rr.status, rr.resolution, rr.resolved_at = "resolvido", resolution, now()
    audit(actor, "review.resolve", "review_request", rr.id)
    notify("revisao_respondida", f"Seu pedido de revisão foi respondido: {resolution}", partner_id=rr.partner_id)
    db.session.commit()


def frequency_alerts(rules: dict, on: date | None = None) -> list[tuple]:
    """Parceiros com muitas indicações aceitas nos últimos 12 meses (risco de não eventualidade, Lei 4.886/65)."""
    on = on or today()
    since = datetime.combine(on - timedelta(days=365), datetime.min.time())
    rows = (db.session.query(Referral.partner_id, func.count(Referral.id))
            .filter(Referral.accepted_at.isnot(None), Referral.accepted_at >= since)
            .group_by(Referral.partner_id).all())
    thr = int(rules["high_frequency_threshold"])
    return [(db.session.get(Partner, pid), n) for pid, n in rows if n >= thr]


def manager_queues(on: date | None = None) -> dict:
    on = on or today()
    rules = rules_now()
    hol = _hol(rules)
    sla = int(rules["sla_review_days"])
    stale_cut = datetime.combine(on - timedelta(days=int(rules["stale_days"])), datetime.min.time())
    warn_until = on + timedelta(days=int(rules["protection_warning_days"]))
    q = {}
    q["indicacoes_sem_validacao"] = Referral.query.filter(Referral.eligibility.in_(("recebida", "em_validacao"))).order_by(Referral.created_at).all()
    q["oportunidades_sem_atualizacao"] = Referral.query.filter(
        Referral.eligibility == "aceita", Referral.waiting.is_(False), Referral.stage.notin_(("ganha", "perdida")),
        Referral.stage_updated_at < stale_cut).all()
    q["protecoes_proximas"] = Referral.query.filter(
        Referral.eligibility == "aceita", Referral.waiting.is_(False), Referral.protection_until.isnot(None),
        Referral.protection_until <= warn_until, Referral.stage.notin_(("ganha", "perdida"))).all()
    cands = Partner.query.filter(Partner.status.in_(("candidato", "em_analise"))).order_by(Partner.application_at).all()
    q["candidaturas"] = [(p, eng.business_days_between(p.application_at.date(), on, hol), sla) for p in cands]
    q["em_espera"] = Referral.query.filter(Referral.eligibility == "aceita", Referral.waiting.is_(True)).all()
    q["nfs_pendentes"] = CommissionLine.query.filter(CommissionLine.state == "aprovada").all()
    q["nfs_para_conferir"] = PartnerInvoice.query.filter_by(status="enviada").all()
    q["pagamentos_atrasados"] = CommissionLine.query.filter(CommissionLine.state == "programada", CommissionLine.cycle < eng.first_of_month(on)).all()
    q["aguardando_conferencia"] = CommissionLine.query.filter(CommissionLine.state == "aguardando_condicao").all()
    q["revisoes"] = ReviewRequest.query.filter_by(status="aberto").all()
    q["consentimento_pendente"] = Referral.query.filter(Referral.eligibility == "aceita", Referral.consent_status == "pendente").all()
    q["alertas_frequencia"] = frequency_alerts(rules, on)
    return q


def run_daily_jobs() -> dict:
    from .security import prune_login_attempts, prune_sessions
    return {"expiradas": expire_protections(), "tentativas_removidas": prune_login_attempts(),
            "sessoes_encerradas": prune_sessions()}
