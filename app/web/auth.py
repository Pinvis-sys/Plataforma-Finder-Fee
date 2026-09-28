"""Entrada, convites, senha e verificação em duas etapas."""
from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from flask import (Blueprint, current_app, flash, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .. import security as sec
from .. import services as svc
from ..core import db, now
from ..models import PARTNER_KINDS, DirectInvite, Partner, User
from .helpers import fail

bp = Blueprint("auth", __name__)


def _home(user: User):
    if user.partner_id and not user.is_staff:
        return url_for("portal.painel")
    return url_for("manage.painel")


def _safe_next(target: str | None) -> str | None:
    """Só caminhos deste site. Barra '//host', barra invertida e caracteres de controle: o navegador
    descarta tabulação e quebra de linha, e um caminho com tabulação entre as barras vira '//host'."""
    if not target or not target.startswith("/") or "\\" in target:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in target):
        return None
    parts = urlsplit(target)
    if target.startswith("//") or parts.scheme or parts.netloc:
        return None
    return target


# Hash de uma senha qualquer: e-mail inexistente também paga o custo da verificação,
# para o tempo de resposta não revelar quais e-mails têm conta.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))
PENDING_2FA_MINUTES = 5


IP_BLOCKED_MSG = "Muitas tentativas incorretas a partir desta rede. Aguarde alguns minutos e tente novamente."


def _count_failure(user: User | None, kind: str):
    """Erro de senha ou de código: conta para o IP e, se a conta existe, para o bloqueio da conta."""
    sec.record_failed_attempt(kind)
    if user:
        cfg = current_app.config
        user.failed_logins += 1
        if user.failed_logins >= cfg["MAX_FAILED_LOGINS"]:
            user.locked_until = now() + timedelta(minutes=cfg["LOCK_MINUTES"])
            user.failed_logins = 0
    db.session.commit()


def _password_ok(user: User | None, pw: str) -> bool:
    if user is None:
        check_password_hash(_DUMMY_HASH, pw)
        return False
    return user.check_password(pw) and user.active


def _locked(user: User | None) -> bool:
    return bool(user and user.locked_until and user.locked_until > now())


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if sec.current_user():
        return redirect(_home(sec.current_user()))
    if request.method == "POST":
        if sec.ip_blocked():
            flash(IP_BLOCKED_MSG, "error")
            return render_template("login.html"), 429
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if _locked(user):
            sec.record_failed_attempt("senha")
            db.session.commit()
            flash("Acesso temporariamente bloqueado por tentativas incorretas. Tente novamente em alguns minutos.", "error")
        elif _password_ok(user, pw):
            if user.totp_secret:
                # o contador da conta só zera depois do segundo fator
                session.clear()
                session["pending_uid"] = user.id
                session["pending_at"] = now().timestamp()
                session["next"] = _safe_next(request.args.get("next")) or ""
                return redirect(url_for("auth.verify_2fa"))
            user.failed_logins, user.locked_until = 0, None
            sec.login_user(user)
            user.last_login_at = now()
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or _home(user))
        else:
            _count_failure(user, "senha")
            flash("E-mail ou senha incorretos.", "error")
    return render_template("login.html")


@bp.route("/entrar/verificacao", methods=["GET", "POST"])
def verify_2fa():
    uid = session.get("pending_uid")
    user = db.session.get(User, uid) if uid else None
    started = session.get("pending_at") or 0
    if not user or not user.active or now().timestamp() - started > PENDING_2FA_MINUTES * 60:
        session.clear()
        return redirect(url_for("auth.login"))
    if _locked(user):
        session.clear()
        flash("Acesso temporariamente bloqueado por tentativas incorretas. Tente novamente em alguns minutos.", "error")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if sec.ip_blocked():
            flash(IP_BLOCKED_MSG, "error")
            return render_template("verificacao.html"), 429
        step = sec.match_totp(user.totp_secret, request.form.get("code", ""), after_step=user.totp_last_step)
        if step is not None and sec.claim_totp_step(user, step):
            nxt = session.get("next")
            sec.login_user(user)
            user.failed_logins, user.locked_until = 0, None
            user.last_login_at = now()
            db.session.commit()
            return redirect(_safe_next(nxt) or _home(user))
        _count_failure(user, "2fa")
        if _locked(user):
            session.clear()
            flash("Acesso temporariamente bloqueado por tentativas incorretas. Tente novamente em alguns minutos.", "error")
            return redirect(url_for("auth.login"))
        flash("Código incorreto ou expirado.", "error")
    return render_template("verificacao.html")


@bp.route("/sair", methods=["POST"])
def logout():
    sec.logout_user()
    return redirect(url_for("auth.login"))


def _invite_context(token: str | None, direct: str | None):
    inviter = None
    if token:
        inviter = Partner.query.filter_by(invite_token=token).first()
        if not inviter:
            return None, None
    return inviter, direct


@bp.route("/convite/<token>", methods=["GET", "POST"])
def apply_invited(token):
    inviter = Partner.query.filter_by(invite_token=token).first()
    if not inviter:
        return render_template("erro.html", titulo="Convite inválido", msg="Este link de convite não existe."), 404
    open_, _ = svc.recruit_link_state(inviter)
    if not open_:
        return render_template("erro.html", titulo="Convite indisponível", msg="Este convite não está mais disponível. Fale com quem convidou você."), 410
    return _apply(inviter=inviter, token=token, direct=None)


@bp.route("/convite-direto/<token>", methods=["GET", "POST"])
def apply_direct(token):
    inv = DirectInvite.query.filter_by(token=token, used_at=None).first()
    if not inv:
        return render_template("erro.html", titulo="Convite inválido", msg="Este convite não existe ou já foi utilizado."), 404
    return _apply(inviter=None, token=None, direct=token)


def _apply(inviter, token, direct):
    if request.method == "POST":
        f = request.form
        data = {k: f.get(k, "") for k in ("cnpj", "legal_name", "trade_name", "contact_name", "email", "phone", "city", "uf",
                                          "kind", "market_time", "company_size")}
        data["is_mei"] = bool(f.get("is_mei"))
        try:
            if f.get("password") != f.get("password2"):
                raise svc.ServiceError("As senhas não conferem.")
            svc.apply_partner(data, f.get("password", ""), token=token, accept_notice=bool(f.get("accept_notice")), direct_token=direct)
        except svc.ServiceError as e:
            fail(e)
            return render_template("candidatura.html", inviter=inviter, kinds=PARTNER_KINDS, form=f)
        flash("Candidatura enviada. A All Targets vai analisar em até 5 dias úteis. Use seu e-mail e senha para acompanhar.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("candidatura.html", inviter=inviter, kinds=PARTNER_KINDS, form={})


@bp.route("/conta/senha", methods=["GET", "POST"])
@sec.login_required
def change_password():
    u = sec.current_user()
    if request.method == "POST":
        f = request.form
        if not u.check_password(f.get("current", "")):
            flash("Senha atual incorreta.", "error")
        elif len(f.get("new", "")) < 10 or f.get("new") != f.get("new2"):
            flash("A nova senha precisa ter ao menos 10 caracteres e ser digitada duas vezes igual.", "error")
        else:
            u.set_password(f["new"])
            u.must_change_password = False
            sec.end_sessions(u)
            sec.login_user(u)          # sessão nova: nenhum cookie anterior continua valendo
            db.session.commit()
            flash("Senha alterada.", "ok")
            return redirect(_home(u))
    return render_template("senha.html")


@bp.route("/conta/2fa", methods=["GET", "POST"])
@sec.login_required
def setup_2fa():
    u = sec.current_user()
    secret = session.get("totp_setup") or sec.new_totp_secret()
    session["totp_setup"] = secret
    if request.method == "POST":
        current_step = (sec.match_totp(u.totp_secret, request.form.get("current_code", ""), after_step=u.totp_last_step)
                        if u.totp_secret else None)
        new_step = sec.match_totp(secret, request.form.get("code", ""))
        if u.totp_secret and (current_step is None or not sec.claim_totp_step(u, current_step)):
            # trocar o aplicativo exige o código do atual: quem só roubou a sessão não consegue
            flash("Código do aplicativo atual incorreto.", "error")
        elif new_step is not None:
            u.totp_secret, u.totp_last_step = secret, new_step
            sec.end_sessions(u)
            sec.login_user(u)
            db.session.commit()
            flash("Verificação em duas etapas ativada.", "ok")
            return redirect(_home(u))
        flash("Código incorreto. Confira o horário do celular e tente de novo.", "error")
    return render_template("dois_fatores.html", secret=secret, uri=sec.provisioning_uri(secret, u.email), ativo=bool(u.totp_secret))
