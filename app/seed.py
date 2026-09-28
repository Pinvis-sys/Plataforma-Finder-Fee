"""Dados iniciais e de demonstração."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

from . import cnpj as cn
from . import engine as eng
from . import services as svc
from .core import db, now, today
from .models import (TERM_KEYS, KnownCompany, Material, Partner, TermDocument, User)
from .rules import active_ruleset

PENDING_TEXT = ("[Texto pendente. Este documento será redigido com o jurídico da All Targets (parecer sobre "
                "representação comercial, cláusula de caráter eventual e demais termos) antes do primeiro parceiro ser aprovado.]")


def seed_defaults():
    active_ruleset()
    for key, title in TERM_KEYS.items():
        if not db.session.get(TermDocument, key):
            db.session.add(TermDocument(key=key, title=title, body=PENDING_TEXT, legal_approved=False))
    db.session.commit()


def create_staff(email: str, name: str, roles: str, password: str) -> User:
    if User.query.filter_by(email=email.lower()).first():
        raise svc.ServiceError("Já existe usuário com este e-mail.")
    u = User(email=email.lower(), name=name, roles=roles)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


def make_active_partner(name: str, root8: str, email: str, kind="contador", level=1, invited_token: str | None = None,
                        bank=True, password="senha-segura-123") -> Partner:
    """Cria um parceiro já aprovado, com workshop e termos concluídos (demonstração e testes)."""
    for d in TermDocument.query.all():
        d.legal_approved = True
    if invited_token:
        p = svc.apply_partner({"cnpj": cn.make_valid(root8), "legal_name": name, "contact_name": f"Resp. {name}",
                               "email": email, "kind": kind}, password, token=invited_token, accept_notice=True)
    else:
        inv = svc.create_direct_invite("sistema", name)
        p = svc.apply_partner({"cnpj": cn.make_valid(root8), "legal_name": name, "contact_name": f"Resp. {name}",
                               "email": email, "kind": kind}, password, direct_token=inv.token)
    p.cnpj_verified = True
    if bank:
        p.bank, p.bank_agency, p.bank_account, p.bank_holder_doc, p.bank_verified = "Banco Demo", "0001", "12345-6", p.cnpj, True
    db.session.commit()
    svc.review_partner(p, True, "sistema", level=level, kind=kind)
    svc.mark_workshop(p, "sistema")
    for key in TERM_KEYS:
        svc.accept_term(p, key, "127.0.0.1")
    return p


def seed_demo():
    """Cenário de demonstração: 3 parceiros (um recrutado), indicações em vários estados e um contrato com recebimentos."""
    seed_defaults()
    if User.query.filter_by(email="gestor@demo.test").first():
        return
    create_staff("gestor@demo.test", "Marco (demonstração)", "admin,manager,commercial,finance", "demo-senha-123")
    p1 = make_active_partner("Contábil Horizonte Ltda", "11000001", "parceiro1@demo.test", kind="contador")
    p2 = make_active_partner("Consultoria Mentor Ltda", "11000002", "parceiro2@demo.test", kind="prof_mba_a")
    p3 = make_active_partner("Agência Vetor Ltda", "11000003", "parceiro3@demo.test", kind="agencia_marketing",
                             invited_token=p1.invite_token)
    for i, t in enumerate(["Educação e Cultura Materiais", "Logística Sul"], start=1):
        pass
    db.session.add(Material(title="Playbook — como reconhecer o sinal (contadores)", kind="video",
                            url="https://example.com/playbook-contadores", position=1))
    db.session.add(KnownCompany(cnpj=cn.make_valid("90000001"), cnpj_base="90000001", kind="cliente_atual",
                                note="Cliente atual (demonstração)"))
    db.session.commit()

    def ref(partner, root, name, sig):
        r = svc.register_referral(partner, {"cnpj": cn.make_valid(root), "company_name": name, "decisor_name": "Diretor Financeiro",
                                            "signal": sig, "interest": "Diagnóstico"})
        return r

    r1 = ref(p3, "80000001", "Metalúrgica Bela Vista", "Financiamento em vez de compra à vista de equipamento")
    svc.validate_referral(r1, "accept", "gestor@demo.test", fits_target=True)
    svc.register_consent(r1, "Aceite eletrônico em " + today().isoformat(), "gestor@demo.test")
    svc.set_stage(r1, "reuniao", "gestor@demo.test")
    signed = today() - timedelta(days=75)
    items = [eng.ScheduleItem("Setup Diagnóstico", "evento_unico", D("40000"), signed + timedelta(days=5)),
             eng.ScheduleItem("Retainer Gestão Financeira", "retainer", D("18000"), eng.add_months(signed, 1), months=12)]
    c = svc.win_contract(r1, "Ciclo de Geração de Valor", "prof_mba", signed, items, "gestor@demo.test")
    inst = c.installments[0]
    rc, lines, warns = svc.register_receipt(inst, signed + timedelta(days=12), D("40000"), "NF-1001", D("40000"), D("4000"), "gestor@demo.test")
    for line in lines:
        svc.approve_line(line, "gestor@demo.test")

    r2 = ref(p1, "80000002", "Distribuidora Aurora", "Empresário comentou que vai vender a empresa")
    svc.validate_referral(r2, "start_review", "gestor@demo.test")
    r3 = ref(p2, "80000003", "Transportes Planalto", "Adiamento de CAPEX e pedido de leasing")
    svc.validate_referral(r3, "reject", "gestor@demo.test", reason="Faturamento abaixo da faixa-alvo do programa.", fits_target=False)
    return True
