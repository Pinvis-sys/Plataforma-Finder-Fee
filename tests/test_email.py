"""Envio de avisos por e-mail contra um servidor SMTP real (aiosmtpd), com TLS e autenticação."""
import os
import smtplib
import socket
import ssl

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from app import seed, services as svc
from app.core import db
from app.models import AuditLog, Notification

HERE = os.path.join(os.path.dirname(__file__), "smtp")
CRT, KEY = os.path.join(HERE, "teste.crt"), os.path.join(HERE, "teste.key")


class Caixa:
    """Servidor de teste: guarda as mensagens e recusa endereços escolhidos (5xx definitivo, 4xx temporário)."""

    def __init__(self, recusar=(), adiar=()):
        self.msgs, self.recusar, self.adiar = [], set(recusar), set(adiar)

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if address in self.recusar:
            return "550 5.1.1 caixa inexistente"
        if address in self.adiar:
            return "451 4.3.0 tente mais tarde"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        self.msgs.append({"para": envelope.rcpt_tos[0], "tls": bool(session.ssl),
                          "texto": envelope.content.decode(errors="replace")})
        return "250 OK"

    @property
    def destinos(self):
        return [m["para"] for m in self.msgs]


def _auth(server, session, envelope, mechanism, data):
    ok = isinstance(data, LoginPassword) and data.login == b"programa" and data.password == b"segredo"
    return AuthResult(success=ok, handled=False)


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def smtp_server():
    """Sobe um servidor SMTP local. modo: starttls (587) ou ssl (465)."""
    ativos = []

    def subir(modo="starttls", **caixa_kw):
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(CRT, KEY)
        caixa, porta = Caixa(**caixa_kw), _porta_livre()
        kw = dict(hostname="127.0.0.1", port=porta, authenticator=_auth, auth_exclude_mechanism=["LOGIN"])
        if modo == "ssl":
            kw.update(ssl_context=ctx, auth_require_tls=False)       # a conexão inteira já é TLS
        else:
            kw.update(tls_context=ctx, require_starttls=True, auth_require_tls=True)
        c = Controller(caixa, **kw)
        c.start()
        ativos.append(c)
        return caixa, porta

    yield subir
    for c in ativos:
        c.stop()


def _config(app, porta, **extra):
    app.config.update({"SMTP_HOST": "127.0.0.1", "SMTP_PORT": porta, "SMTP_USER": "programa", "SMTP_PASS": "segredo",
                       "SMTP_CA_FILE": CRT, "SMTP_TIMEOUT": 5, "SMTP_SECURITY": "", **extra})


def _avisos(*emails):
    for e in emails:
        u = seed.create_staff(e, e.split("@")[0], "manager", "senha-longa-123")
        svc.notify("teste", f"Aviso para {e}", user_id=u.id)
    db.session.commit()


def _pendentes():
    return Notification.query.filter(Notification.emailed_at.is_(None)).count()


def test_sem_smtp_configurado_nao_envia(app):
    _avisos("a@t.test")
    assert svc.send_pending_emails(app) == 0
    assert _pendentes() == 1                                   # fica só no portal


def test_starttls_com_login_entrega_e_nao_reenvia(app, smtp_server):
    caixa, porta = smtp_server("starttls")
    _config(app, porta)
    _avisos("a@t.test", "b@t.test")
    assert svc.send_pending_emails(app) == 2
    assert sorted(caixa.destinos) == ["a@t.test", "b@t.test"]
    assert all(m["tls"] for m in caixa.msgs)
    assert "Aviso para a@t.test" in caixa.msgs[0]["texto"] or "Aviso para a@t.test" in caixa.msgs[1]["texto"]
    assert "Subject: Programa Finder Fee All Targets" in caixa.msgs[0]["texto"]
    assert svc.send_pending_emails(app) == 0 and len(caixa.msgs) == 2


def test_ssl_direto_porta_465(app, smtp_server):
    caixa, porta = smtp_server("ssl")
    _config(app, porta, SMTP_SECURITY="ssl")
    _avisos("a@t.test")
    assert svc.send_pending_emails(app) == 1
    assert caixa.destinos == ["a@t.test"]


def test_porta_465_escolhe_ssl_sozinha(app, monkeypatch):
    usado = {}

    class FakeSSL:
        def __init__(self, host, port, timeout, context):
            usado.update(port=port, verifica=context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname)

        def login(self, *a):
            pass

        def close(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    svc._smtp_connect({**app.config, "SMTP_HOST": "smtp.exemplo", "SMTP_PORT": 465, "SMTP_SECURITY": ""})
    assert usado == {"port": 465, "verifica": True}


def test_certificado_nao_confiavel_e_recusado(app, smtp_server):
    caixa, porta = smtp_server("starttls")
    _config(app, porta, SMTP_CA_FILE="")                     # só as CAs do sistema: o autoassinado não vale
    _avisos("a@t.test")
    with pytest.raises(ssl.SSLCertVerificationError):
        svc.send_pending_emails(app)
    assert caixa.msgs == [] and _pendentes() == 1           # nada saiu, nem a senha


def test_senha_smtp_errada(app, smtp_server):
    caixa, porta = smtp_server("starttls")
    _config(app, porta, SMTP_PASS="errada")
    _avisos("a@t.test")
    with pytest.raises(smtplib.SMTPAuthenticationError):
        svc.send_pending_emails(app)
    assert _pendentes() == 1


def test_endereco_recusado_nao_trava_a_fila(app, smtp_server):
    caixa, porta = smtp_server("starttls", recusar={"ruim@t.test"})
    _config(app, porta)
    _avisos("a@t.test", "ruim@t.test", "c@t.test")
    assert svc.send_pending_emails(app) == 2
    db.session.rollback()                                     # próxima rodada do cron = processo novo
    assert svc.send_pending_emails(app) == 0
    assert sorted(caixa.destinos) == ["a@t.test", "c@t.test"]    # sem reenvio, e o de depois saiu
    assert _pendentes() == 0
    log = AuditLog.query.filter_by(action="email.refused").one()
    assert log.details["to"] == "ruim@t.test" and log.details["code"] == 550


def test_falha_temporaria_para_o_lote_sem_perder_o_que_saiu(app, smtp_server):
    caixa, porta = smtp_server("starttls", adiar={"b@t.test"})
    _config(app, porta)
    _avisos("a@t.test", "b@t.test", "c@t.test")
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        svc.send_pending_emails(app)
    db.session.rollback()
    assert caixa.destinos == ["a@t.test"]
    assert _pendentes() == 2                                  # "a" ficou gravado; "b" e "c" tentam de novo
    caixa.adiar.clear()
    assert svc.send_pending_emails(app) == 2
    assert caixa.destinos == ["a@t.test", "b@t.test", "c@t.test"]


def test_seguranca_invalida_e_recusada(app):
    with pytest.raises(ValueError, match="FF_SMTP_SECURITY"):
        svc._smtp_connect({**app.config, "SMTP_HOST": "x", "SMTP_SECURITY": "tls-sim"})


def test_aviso_antigo_nao_sai_por_email(app, smtp_server):
    """Ao ligar o SMTP, o que ficou acumulado há mais de FF_EMAIL_MAX_AGE_DAYS não vira uma enxurrada de e-mails."""
    from datetime import timedelta
    from app.core import now
    caixa, porta = smtp_server("starttls")
    _config(app, porta)
    _avisos("velho@t.test", "novo@t.test")
    for n in Notification.query.all():
        if "velho@" in n.message:
            n.created_at = now() - timedelta(days=app.config["EMAIL_MAX_AGE_DAYS"] + 1)
    db.session.commit()
    assert svc.send_pending_emails(app) == 1
    assert caixa.destinos == ["novo@t.test"]
    assert _pendentes() == 0
    log = AuditLog.query.filter_by(action="email.discarded").one()
    assert log.details["count"] == 1


def test_comando_mostra_e_descarta_a_fila(app):
    _avisos("a@t.test", "b@t.test")
    runner = app.test_cli_runner()
    r = runner.invoke(args=["email-backlog"])
    assert r.exit_code == 0 and "Avisos na fila de e-mail: 2" in r.output
    assert _pendentes() == 2                                   # só mostrar não mexe na fila
    r = runner.invoke(args=["email-backlog", "--discard"])
    assert r.exit_code == 0 and "2 aviso(s) descartado(s)" in r.output
    assert _pendentes() == 0
    assert Notification.query.filter(Notification.read_at.is_(None)).count() == 2   # continuam no portal
