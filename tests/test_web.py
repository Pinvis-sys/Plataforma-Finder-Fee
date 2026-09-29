from datetime import date
from decimal import Decimal as D

import pytest

from app import cnpj as cn, seed, services as svc
from app.core import db
from app.models import CommissionLine, Partner, Referral, User


def login(client, email, pw):
    return client.post("/entrar", data={"email": email, "password": pw}, follow_redirects=False)


@pytest.fixture()
def world(webapp):
    seed.seed_demo()
    return webapp


def test_paginas_de_acesso_renderizam(webapp):
    c = webapp.test_client()
    assert c.get("/entrar").status_code == 200
    assert c.get("/painel").status_code == 302              # exige login
    assert c.get("/gestao/").status_code == 302
    assert c.get("/convite/inexistente").status_code == 404


def test_login_errado_bloqueia_apos_tentativas(webapp):
    seed.seed_demo()
    c = webapp.test_client()
    for _ in range(5):
        r = c.post("/entrar", data={"email": "parceiro1@demo.test", "password": "errada"})
        assert r.status_code == 200
    r = c.post("/entrar", data={"email": "parceiro1@demo.test", "password": "senha-segura-123"})
    assert b"bloqueado" in r.data


def test_todas_as_telas_do_parceiro_renderizam(world):
    c = world.test_client()
    assert login(c, "parceiro2@demo.test", "senha-segura-123").status_code == 302
    for url in ("/painel", "/indicacoes", "/indicacoes/nova", "/parceiros", "/comissoes", "/materiais", "/cadastro", "/avisos", "/revisao"):
        r = c.get(url)
        assert r.status_code == 200, url
    p3 = Partner.query.filter_by(email="parceiro3@demo.test").first()
    ref = Referral.query.filter_by(partner_id=p3.id).first()
    c3 = world.test_client()
    login(c3, "parceiro3@demo.test", "senha-segura-123")
    assert c3.get(f"/indicacoes/{ref.id}").status_code == 200
    assert c3.get("/comissoes").status_code == 200


def test_todas_as_telas_de_gestao_renderizam(world):
    c = world.test_client()
    login(c, "gestor@demo.test", "demo-senha-123")
    for url in ("/gestao/", "/gestao/indicacoes", "/gestao/indicacoes?f=todas", "/gestao/parceiros", "/gestao/convites",
                "/gestao/financeiro", "/gestao/relatorios", "/gestao/parametros", "/gestao/termos", "/gestao/base",
                "/gestao/auditoria", "/gestao/usuarios", "/conta/senha", "/conta/2fa"):
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)
    for r in Referral.query.all():
        assert c.get(f"/gestao/indicacoes/{r.id}").status_code == 200
    for p in Partner.query.all():
        assert c.get(f"/gestao/parceiros/{p.id}").status_code == 200
    from app.models import Contract
    assert c.get(f"/gestao/contratos/{Contract.query.first().id}").status_code == 200


def test_parceiro_nao_acessa_gestao_e_nao_ve_dados_de_outro(world):
    c = world.test_client()
    login(c, "parceiro2@demo.test", "senha-segura-123")
    assert c.get("/gestao/").status_code == 403
    p3 = Partner.query.filter_by(email="parceiro3@demo.test").first()
    ref = Referral.query.filter_by(partner_id=p3.id).first()
    assert c.get(f"/indicacoes/{ref.id}").status_code == 404      # indicação de outro parceiro


def test_convite_e_candidatura_pela_web(webapp):
    inv = svc.create_direct_invite("t", "Prof. Teste")
    c = webapp.test_client()
    assert c.get(f"/convite-direto/{inv.token}").status_code == 200
    r = c.post(f"/convite-direto/{inv.token}", data={"cnpj": cn.make_valid("77000001"), "legal_name": "Nova Ltda", "contact_name": "Ana",
                                                     "email": "ana@t.test", "password": "senha-segura-123", "password2": "senha-segura-123", "kind": "contador"})
    assert r.status_code == 302
    r = login(c, "ana@t.test", "senha-segura-123")
    assert r.status_code == 302
    r = c.get("/painel", follow_redirects=True)
    assert "Candidatura em análise".encode() in r.data


def test_fluxo_completo_pela_web(webapp):
    """Indicação -> aceite -> consentimento -> contrato -> recebimento -> aprovação -> NF -> conferência -> pagamento."""
    seed.seed_defaults()
    partner = seed.make_active_partner("Parceiro Web", "78000001", "web@t.test")
    seed.create_staff("g@t.test", "Gestor", "admin,manager,commercial,finance", "gestor-senha-123")
    pc, gc = webapp.test_client(), webapp.test_client()
    login(pc, "web@t.test", "senha-segura-123")
    login(gc, "g@t.test", "gestor-senha-123")

    r = pc.post("/indicacoes/nova", data={"step": "check", "cnpj": cn.make_valid("78100001")})
    assert "Empresa disponível".encode() in r.data
    r = pc.post("/indicacoes/nova", data={"step": "register", "cnpj": cn.make_valid("78100001"), "company_name": "Cliente Web",
                                          "decisor_name": "Diretor", "signal": "Leasing para CAPEX"})
    assert r.status_code == 302
    ref = Referral.query.filter_by(company_name="Cliente Web").one()
    # outro parceiro tenta a mesma empresa: mensagem genérica
    other = seed.make_active_partner("Outro", "78000002", "outro@t.test")
    oc = webapp.test_client()
    login(oc, "outro@t.test", "senha-segura-123")
    r = oc.post("/indicacoes/nova", data={"step": "check", "cnpj": cn.make_valid("78100001")})
    assert "não está disponível".encode() in r.data and b"Parceiro Web" not in r.data

    assert gc.post(f"/gestao/indicacoes/{ref.id}", data={"action": "accept", "fits_target": "sim"}).status_code == 302
    gc.post(f"/gestao/indicacoes/{ref.id}", data={"action": "consent", "evidence": "aceite eletrônico"})
    gc.post(f"/gestao/indicacoes/{ref.id}", data={"action": "stage", "stage": "proposta", "next_action": "Enviar proposta"})
    r = gc.post(f"/gestao/indicacoes/{ref.id}", data={
        "action": "contract", "product": "CGV", "signed_at": date.today().isoformat(),
        "i1_desc": "Retainer", "i1_kind": "retainer", "i1_amount": "10.000,00", "i1_due": date.today().isoformat(), "i1_months": "3"})
    assert r.status_code == 302 and "/contratos/" in r.headers["Location"]
    cid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    from app.models import Installment
    inst = Installment.query.filter_by(contract_id=cid).order_by(Installment.due_date).first()
    r = gc.post(f"/gestao/contratos/{cid}", data={"action": "receipt", "installment_id": inst.id, "received_at": date.today().isoformat(),
                                                 "amount": "10.000,00", "nf_number": "NF1", "nf_value": "10.000,00", "taxes": "1.000,00"})
    assert r.status_code == 302
    line = CommissionLine.query.one()
    assert line.amount == D("1350.00")
    gc.post(f"/gestao/financeiro/linhas/{line.id}", data={"action": "approve"})
    gc.post(f"/gestao/financeiro/linhas/{line.id}", data={"action": "check"})
    page = pc.get("/comissoes")
    assert b"R$ 1.350,00" in page.data and "Comissões aprovadas".encode() in page.data
    r = pc.post("/comissoes/nf", data={"number": "123", "issue_date": date.today().isoformat(), "value": "1.350,00", "line_ids": [line.id]})
    assert r.status_code == 302
    db.session.refresh(line)
    assert line.state == "programada"
    from app.models import PartnerInvoice
    inv = PartnerInvoice.query.one()
    gc.post(f"/gestao/financeiro/notas/{inv.id}", data={"action": "ok"})
    r = gc.post("/gestao/financeiro/pagar", data={"partner_id": partner.id, "cycle": line.cycle.isoformat(), "reference": "PIX 1"})
    db.session.refresh(line)
    assert line.state == "paga"
    assert b"R$ 1.350,00" in pc.get("/painel").data


def test_csrf_bloqueia_post_sem_token(tmp_path):
    from app import create_app
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "x"}, web=True)
    with app.app_context():
        db.create_all()
        seed.seed_defaults()
        c = app.test_client()
        assert c.post("/entrar", data={"email": "a", "password": "b"}).status_code == 400


def test_2fa_totp_e_login(webapp):
    from app import security as sec
    u = seed.create_staff("s@t.test", "Staff", "admin", "senha-longa-123")
    u.totp_secret = sec.new_totp_secret()
    db.session.commit()
    c = webapp.test_client()
    r = c.post("/entrar", data={"email": "s@t.test", "password": "senha-longa-123"})
    assert r.headers["Location"].endswith("/entrar/verificacao")
    assert c.post("/entrar/verificacao", data={"code": "000000"}).status_code == 200
    r = c.post("/entrar/verificacao", data={"code": sec.totp_now(u.totp_secret)})
    assert r.status_code == 302
    assert c.get("/gestao/").status_code == 200


def test_totp_rfc6238_vetor_conhecido():
    from app import security as sec
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert sec.totp_now(secret, at=59) == "287082"           # vetor de teste da RFC 6238 (6 dígitos)
    assert sec.verify_totp(secret, "287082", at=59)


def test_papeis_financeiro_nao_edita_indicacao(webapp):
    seed.seed_defaults()
    seed.create_staff("f@t.test", "Fin", "finance", "senha-longa-123")
    p = seed.make_active_partner("P", "79000001", "p@t.test")
    ref = svc.register_referral(p, {"cnpj": cn.make_valid("79100001"), "decisor_name": "D", "signal": "s"})
    c = webapp.test_client()
    login(c, "f@t.test", "senha-longa-123")
    assert c.get(f"/gestao/indicacoes/{ref.id}").status_code == 200
    assert c.post(f"/gestao/indicacoes/{ref.id}", data={"action": "accept"}).status_code == 403
    assert c.get("/gestao/financeiro").status_code == 200
    assert c.get("/gestao/parametros").status_code == 403


def test_limite_por_ip_bloqueia_varias_contas(webapp):
    webapp.config["IP_MAX_FAILURES"] = 6
    seed.seed_demo()
    c = webapp.test_client()
    # erros espalhados por e-mails diferentes não travam nenhuma conta, mas travam o IP
    for i in range(6):
        assert c.post("/entrar", data={"email": f"x{i}@t.test", "password": "errada"}).status_code == 200
    r = c.post("/entrar", data={"email": "parceiro1@demo.test", "password": "senha-segura-123"})
    assert r.status_code == 429
    assert "Location" not in r.headers
    # outro endereço continua entrando normalmente
    outro = webapp.test_client()
    r = outro.post("/entrar", data={"email": "parceiro1@demo.test", "password": "senha-segura-123"},
                   environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert r.status_code == 302


def test_limite_por_ip_expira_e_e_limpo_pela_rotina(webapp):
    from datetime import timedelta
    from app import security as sec
    from app.core import now
    from app.models import LoginAttempt
    webapp.config["IP_MAX_FAILURES"] = 2
    old = now() - timedelta(minutes=webapp.config["IP_WINDOW_MINUTES"] + 1)
    db.session.add_all([LoginAttempt(ip="1.2.3.4", kind="senha", at=old) for _ in range(3)])
    db.session.commit()
    assert not sec.ip_blocked("1.2.3.4")                      # fora da janela
    db.session.add_all([LoginAttempt(ip="1.2.3.4", kind="senha") for _ in range(2)])
    db.session.commit()
    assert sec.ip_blocked("1.2.3.4")
    db.session.add(LoginAttempt(ip="1.2.3.4", kind="senha", at=now() - timedelta(days=2)))
    db.session.commit()
    assert svc.run_daily_jobs()["tentativas_removidas"] == 1


def test_codigo_2fa_errado_bloqueia_a_conta(webapp):
    from app import security as sec
    u = seed.create_staff("s2@t.test", "Staff", "admin", "senha-longa-123")
    u.totp_secret = sec.new_totp_secret()
    db.session.commit()
    c = webapp.test_client()
    c.post("/entrar", data={"email": "s2@t.test", "password": "senha-longa-123"})
    for _ in range(webapp.config["MAX_FAILED_LOGINS"] - 1):
        assert c.post("/entrar/verificacao", data={"code": "000000"}).status_code == 200
    r = c.post("/entrar/verificacao", data={"code": "000000"})
    assert r.headers["Location"].endswith("/entrar")          # sessão pendente descartada
    assert c.get("/entrar/verificacao").headers["Location"].endswith("/entrar")
    # nem a senha certa entra enquanto a conta está bloqueada
    r = c.post("/entrar", data={"email": "s2@t.test", "password": "senha-longa-123"})
    assert b"bloqueado" in r.data


def test_proxy_confiavel_usa_ip_do_cabecalho(tmp_path):
    from app import create_app
    from app import security as sec
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "t",
                      "TRUSTED_PROXIES": 1}, web=False)

    @app.route("/_ip")
    def _ip():
        return sec.client_ip()

    r = app.test_client().get("/_ip", headers={"X-Forwarded-For": "203.0.113.7"})
    assert r.get_data(as_text=True) == "203.0.113.7"


def test_saude_responde_sem_login(webapp):
    r = webapp.test_client().get("/saude")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}
