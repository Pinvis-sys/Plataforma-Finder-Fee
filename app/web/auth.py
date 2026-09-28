"""Entrada, convites, senha e verificação em duas etapas."""
from __future__ import annotations

from datetime import timedelta

from flask import (Blueprint, current_app, flash, redirect, render_template, request, session, url_for)

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
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if sec.current_user():
        return redirect(_home(sec.current_user()))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pw = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        cfg = current_app.config
        if user and user.locked_until and user.locked_until > now():
            flash("Acesso temporariamente bloqueado por tentativas incorretas. Tente novamente em alguns minutos.", "error")
        elif user and user.active and user.check_password(pw):
            user.failed_logins, user.locked_until = 0, None
            db.session.commit()
            if user.totp_secret:
                session.clear()
                session["pending_uid"] = user.id
                session["next"] = _safe_next(request.args.get("next")) or ""
                return redirect(url_for("auth.verify_2fa"))
            sec.login_user(user)
            user.last_login_at = now()
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or _home(user))
        else:
            if user:
                user.failed_logins += 1
                if user.failed_logins >= cfg["MAX_FAILED_LOGINS"]:
                    user.locked_until = now() + timedelta(minutes=cfg["LOCK_MINUTES"])
                    user.failed_logins = 0
                db.session.commit()
            flash("E-mail ou senha incorretos.", "error")
    return render_template("login.html")


@bp.route("/entrar/verificacao", methods=["GET", "POST"])
def verify_2fa():
    uid = session.get("pending_uid")
    user = db.session.get(User, uid) if uid else None
    if not user:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if sec.verify_totp(user.totp_secret, request.form.get("code", "")):
            nxt = session.get("next")
            sec.login_user(user)
            user.last_login_at = now()
            db.session.commit()
            return redirect(_safe_next(nxt) or _home(user))
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
        if sec.verify_totp(secret, request.form.get("code", "")):
            u.totp_secret = secret
            db.session.commit()
            session.pop("totp_setup", None)
            flash("Verificação em duas etapas ativada.", "ok")
            return redirect(_home(u))
        flash("Código incorreto. Confira o horário do celular e tente de novo.", "error")
    return render_template("dois_fatores.html", secret=secret, uri=sec.provisioning_uri(secret, u.email), ativo=bool(u.totp_secret))
