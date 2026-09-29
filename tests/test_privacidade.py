"""LGPD: retenção automática (só com prazo definido), pedido do titular e o que nunca pode ser alterado."""
from datetime import datetime, timedelta

import pytest

from app import cnpj as cn, privacidade as privacy, seed, services as svc
from app.core import db, now
from app.models import (AuditLog, CommissionLine, Notification, Partner, Referral, TermAcceptance, User)
from app.rules import active_ruleset, new_version


def login(client, email, pw):
    return client.post("/entrar", data={"email": email, "password": pw})


def _policy(**months):
    new_version(active_ruleset(), {f"retention_{k}_months": v for k, v in months.items()}, "t", "prazos de teste")
    db.session.commit()


def _p(email):
    return Partner.query.filter_by(email=email).first()


@pytest.fixture()
def world(webapp):
    seed.seed_demo()
    return webapp


def _rejected_referral(months_ago):
    ref = Referral.query.filter_by(eligibility="rejeitada").first()
    ref.reviewed_at = now() - timedelta(days=31 * months_ago)
    db.session.commit()
    return ref


# ------------------------------------------------------------------ sem prazo, nada acontece
def test_sem_prazo_definido_nada_e_anonimizado(world):
    _rejected_referral(months_ago=60)
    prev = privacy.retention_preview()
    assert prev["referrals"] == [] and prev["partners"] == [] and prev["notifications"] == 0
    assert svc.run_daily_jobs()["retencao"] == {"indicacoes": 0, "parceiros": 0, "parceiros_bloqueados": 0, "avisos": 0}
    assert Referral.query.filter(Referral.anonymized_at.isnot(None)).count() == 0
    assert AuditLog.query.filter(AuditLog.action.like("privacy.%")).count() == 0


# ------------------------------------------------------------------ indicações
def test_indicacao_encerrada_ha_mais_que_o_prazo_e_anonimizada(world):
    old = _rejected_referral(months_ago=13)
    cnpj, code = old.cnpj, old.code
    _policy(referral=12)
    assert privacy.retention_preview()["referrals"] == [old]
    assert svc.run_daily_jobs()["retencao"]["indicacoes"] == 1
    ref = db.session.get(Referral, old.id)
    assert ref.anonymized_at and ref.decisor_name is None and ref.company_name is None
    assert ref.signal == privacy.MARK and ref.reject_reason == privacy.MARK
    assert (ref.cnpj, ref.code) == (cnpj, code)                     # empresa e código ficam
    log = AuditLog.query.filter_by(action="privacy.anonymize_referral").one()
    assert log.details == {"code": code, "reason": "prazo de retenção", "closed": False}
    assert svc.run_daily_jobs()["retencao"]["indicacoes"] == 0      # não repete


def test_indicacao_recente_em_andamento_ou_com_contrato_fica(world):
    _rejected_referral(months_ago=1)
    com_contrato = Referral.query.filter(Referral.contracts.any()).one()
    com_contrato.stage, com_contrato.stage_updated_at = "perdida", now() - timedelta(days=900)
    em_validacao = Referral.query.filter_by(eligibility="em_validacao").one()
    em_validacao.created_at = now() - timedelta(days=900)
    db.session.commit()
    _policy(referral=12)
    assert privacy.retention_preview()["referrals"] == []


def test_oportunidade_perdida_conta_da_data_da_perda(world):
    p = _p("parceiro1@demo.test")
    ref = svc.register_referral(p, {"cnpj": cn.make_valid("83000001"), "company_name": "Perdida SA", "decisor_name": "Ana",
                                    "signal": "sinal"})
    svc.validate_referral(ref, "accept", "t", fits_target=True)
    svc.register_consent(ref, "Aceite por e-mail de ana@perdida.test", "t")
    svc.set_stage(ref, "perdida", "t", lost_reason="sem orçamento (conversa com Ana)")
    ref.stage_updated_at = now() - timedelta(days=400)
    db.session.commit()
    _policy(referral=12)
    privacy.apply_retention()
    ref = db.session.get(Referral, ref.id)
    assert ref.decisor_name is None and ref.lost_reason is None and ref.consent_evidence is None
    assert ref.consent_status == "registrado"                      # o fato de ter havido consentimento fica
    assert Notification.query.filter_by(key="indicacao_encerrada").count() == 0   # já estava encerrada: sem aviso


def test_pedido_do_titular_na_indicacao_com_contrato_mantem_a_empresa(world):
    ref = Referral.query.filter(Referral.contracts.any()).one()
    c = world.test_client()
    login(c, "gestor@demo.test", "demo-senha-123")
    r = c.post(f"/gestao/indicacoes/{ref.id}", data={"action": "anonymize", "reason": "pedido do titular"}, follow_redirects=True)
    assert "Marque a confirmação" in r.get_data(as_text=True)
    assert db.session.get(Referral, ref.id).anonymized_at is None
    c.post(f"/gestao/indicacoes/{ref.id}", data={"action": "anonymize", "reason": "pedido do titular", "confirm": "1"})
    ref = db.session.get(Referral, ref.id)
    assert ref.anonymized_at and ref.decisor_name is None
    assert ref.company_name == "Metalúrgica Bela Vista"               # contrato precisa da empresa
    assert CommissionLine.query.filter_by(referral_id=ref.id).count() > 0   # financeiro intacto
    r = c.post(f"/gestao/indicacoes/{ref.id}", data={"action": "anonymize", "reason": "de novo", "confirm": "1"}, follow_redirects=True)
    assert "já foram anonimizados" in r.get_data(as_text=True)
    # o parceiro vê que foi anonimizado
    p3 = world.test_client()
    login(p3, "parceiro3@demo.test", "senha-segura-123")
    assert "Anonimizados em" in p3.get(f"/indicacoes/{ref.id}").get_data(as_text=True)


def test_so_administrador_anonimiza(world):
    seed.create_staff("ger@t.test", "Gerente", "manager,commercial", "senha-longa-123")
    ref = Referral.query.first()
    c = world.test_client()
    login(c, "ger@t.test", "senha-longa-123")
    assert c.post(f"/gestao/indicacoes/{ref.id}", data={"action": "anonymize", "reason": "x", "confirm": "1"}).status_code == 403
    p = _p("parceiro2@demo.test")
    assert c.post(f"/gestao/parceiros/{p.id}", data={"action": "anonymize", "reason": "x", "confirm": "1"}).status_code == 403
    assert "Anonimizar dados pessoais" not in c.get(f"/gestao/indicacoes/{ref.id}").get_data(as_text=True)
    assert c.get("/gestao/privacidade").status_code == 200           # gestor consulta
    assert c.post("/gestao/privacidade").status_code == 403           # mas não aplica
    assert db.session.get(Referral, ref.id).anonymized_at is None


# ------------------------------------------------------------------ parceiros
def test_parceiro_ativo_ou_com_pagamento_pendente_nao_pode(world):
    p2, p3 = _p("parceiro2@demo.test"), _p("parceiro3@demo.test")
    assert any("Suspenda" in b for b in privacy.partner_blockers(p2))
    p3.status = "suspenso"
    db.session.commit()
    assert any("não paga" in b for b in privacy.partner_blockers(p3))
    with pytest.raises(privacy.PrivacyError):
        privacy.anonymize_partner(p3, "t", "pedido")


def test_pedido_do_titular_no_parceiro(world):
    p = _p("parceiro2@demo.test")
    pid, cnpj, legal, code = p.id, p.cnpj, p.legal_name, p.code
    parceiro = world.test_client()
    login(parceiro, "parceiro2@demo.test", "senha-segura-123")
    assert parceiro.get("/painel").status_code == 200
    token_antigo = p.invite_token
    adm = world.test_client()
    login(adm, "gestor@demo.test", "demo-senha-123")
    adm.post(f"/gestao/parceiros/{pid}", data={"action": "suspend", "reason": "saiu do programa"})
    adm.post(f"/gestao/parceiros/{pid}", data={"action": "anonymize", "reason": "pedido do titular", "confirm": "1"})

    p = db.session.get(Partner, pid)
    assert p.anonymized_at and p.contact_name is None and p.email is None and p.phone is None and p.bank_account is None
    assert (p.cnpj, p.legal_name, p.code) == (cnpj, legal, code)
    u = User.query.filter_by(partner_id=pid).one()
    assert not u.active and u.email.endswith("@anonimizado.invalid") and u.name == "Anonimizado"
    assert parceiro.get("/painel").status_code == 302                                      # sessão encerrada
    assert login(world.test_client(), "parceiro2@demo.test", "senha-segura-123").status_code == 200   # não entra mais
    assert all(t.ip is None for t in TermAcceptance.query.filter_by(partner_id=pid))
    assert TermAcceptance.query.filter_by(partner_id=pid).count() == 6                    # a prova do aceite fica
    assert Notification.query.filter_by(partner_id=pid).count() == 0
    assert AuditLog.query.filter_by(actor="parceiro2@demo.test").count() == 0
    assert AuditLog.query.filter_by(actor=code).count() > 0
    assert world.test_client().get(f"/convite/{token_antigo}").status_code == 404


def test_retencao_de_parceiro_nao_aprovado_e_suspenso(world):
    inv = svc.create_direct_invite("t", "Candidato")
    cand = svc.apply_partner({"cnpj": cn.make_valid("12000001"), "legal_name": "Cand Ltda", "contact_name": "Beto",
                              "email": "beto@t.test"}, "senha-longa-123", direct_token=inv.token)
    svc.review_partner(cand, False, "t", reason="fora do perfil")
    cand.reviewed_at = now() - timedelta(days=800)
    p2 = _p("parceiro2@demo.test")
    p2.status = "suspenso"
    db.session.add(AuditLog(actor="t", action="partner.suspend", entity="partner", entity_id=p2.id, at=now() - timedelta(days=800)))
    p3 = _p("parceiro3@demo.test")                                   # suspenso há muito, mas com comissão a pagar
    p3.status = "suspenso"
    db.session.add(AuditLog(actor="t", action="partner.suspend", entity="partner", entity_id=p3.id, at=now() - timedelta(days=800)))
    db.session.commit()
    _policy(partner=24)
    prev = privacy.retention_preview()
    assert {p.id for p in prev["partners"]} == {cand.id, p2.id}
    assert [p.id for p in prev["partners_blocked"]] == [p3.id]
    out = privacy.apply_retention()
    assert out["parceiros"] == 2 and out["parceiros_bloqueados"] == 1
    assert db.session.get(Partner, cand.id).reject_reason == privacy.MARK
    assert db.session.get(Partner, p3.id).anonymized_at is None
    assert AuditLog.query.filter_by(action="privacy.retention").one().details["blocked"] == [p3.code]


# ------------------------------------------------------------------ avisos, parâmetros, tela e comando
def test_avisos_antigos_sao_apagados(world):
    n0 = Notification.query.count()
    velho = Notification.query.first()
    velho.created_at = now() - timedelta(days=400)
    db.session.commit()
    _policy(notification=12)
    assert privacy.apply_retention()["avisos"] == 1
    assert Notification.query.count() == n0 - 1


def test_parametros_aceitam_meses_ou_vazio(world):
    c = world.test_client()
    login(c, "gestor@demo.test", "demo-senha-123")
    html = c.get("/gestao/parametros").get_data(as_text=True)
    assert "LGPD: anonimizar indicação encerrada" in html
    import re
    form = {m.group(1): m.group(2) for m in re.finditer(r'name="([a-z_]+)" value="([^"]*)"', html)}
    form.update({"note": "prazos LGPD", "retention_referral_months": "0"})
    r = c.post("/gestao/parametros", data=form, follow_redirects=True)
    assert "ao menos 1 mês" in r.get_data(as_text=True)
    form["retention_referral_months"] = "60"
    c.post("/gestao/parametros", data=form)
    assert active_ruleset().rules["retention_referral_months"] == 60
    assert active_ruleset().rules["retention_partner_months"] is None


def test_tela_e_comando(world):
    _rejected_referral(months_ago=13)
    _policy(referral=12)
    c = world.test_client()
    login(c, "gestor@demo.test", "demo-senha-123")
    html = c.get("/gestao/privacidade").get_data(as_text=True)
    assert "12 meses" in html and "A definir" in html
    r = world.test_cli_runner().invoke(args=["retention"])
    assert r.exit_code == 0 and "Seriam anonimizados agora: 1 indicação(ões)" in r.output
    assert Referral.query.filter(Referral.anonymized_at.isnot(None)).count() == 0        # só mostrou
    r = world.test_cli_runner().invoke(args=["retention", "--apply"])
    assert r.exit_code == 0 and "'indicacoes': 1" in r.output
    r = c.get("/gestao/privacidade").get_data(as_text=True)
    assert "Prazos aplicados: 1 indicação(ões)" in r
    fin = world.test_client()
    seed.create_staff("fin@t.test", "Fin", "finance", "senha-longa-123")
    login(fin, "fin@t.test", "senha-longa-123")
    assert fin.get("/gestao/privacidade").status_code == 403


def test_pedido_do_titular_encerra_indicacao_em_andamento(world):
    """Sem os dados do indicado não há contato: a indicação em validação é encerrada e a empresa fica livre."""
    ref = Referral.query.filter_by(eligibility="em_validacao").one()
    cnpj, partner_id = ref.cnpj, ref.partner_id
    privacy.anonymize_referral(ref, "t", "pedido do titular")
    db.session.commit()
    ref = db.session.get(Referral, ref.id)
    assert ref.eligibility == "rejeitada" and not ref.active and ref.reject_reason == privacy.CLOSED_BY_HOLDER
    assert Notification.query.filter_by(partner_id=partner_id, key="indicacao_encerrada").count() == 1
    assert AuditLog.query.filter_by(action="privacy.anonymize_referral").one().details["closed"] is True
    outro = _p("parceiro2@demo.test")
    assert svc.check_availability(outro, cnpj)                      # a empresa pode ser indicada de novo


def test_pedido_do_titular_encerra_indicacao_aceita_como_perdida(world):
    p = _p("parceiro1@demo.test")
    ref = svc.register_referral(p, {"cnpj": cn.make_valid("84000001"), "company_name": "Aceita SA", "decisor_name": "Rui",
                                    "signal": "sinal"})
    svc.validate_referral(ref, "accept", "t", fits_target=True)
    privacy.anonymize_referral(ref, "t", "pedido do titular")
    db.session.commit()
    ref = db.session.get(Referral, ref.id)
    assert (ref.eligibility, ref.stage, ref.active) == ("aceita", "perdida", False)
    assert ref.lost_reason == privacy.CLOSED_BY_HOLDER
