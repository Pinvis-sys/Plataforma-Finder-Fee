"""Autenticação, permissões por perfil, proteção CSRF e TOTP (autenticação reforçada)."""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import secrets
import struct
import time

from flask import abort, current_app, flash, g, redirect, request, session, url_for

from .core import db
from .models import User


# ----------------------------------------------------------------- TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((at or time.time()) // 30))


def verify_totp(secret: str, code: str, at: float | None = None) -> bool:
    code = (code or "").strip().replace(" ", "")
    t = int((at or time.time()) // 30)
    return any(hmac.compare_digest(_hotp(secret, t + d), code) for d in (-1, 0, 1))


def provisioning_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/AllTargets%20Finder%20Fee:{email}?secret={secret}&issuer=AllTargets"


# ----------------------------------------------------------------- sessão
def current_user() -> User | None:
    """Usuário da requisição. O cache fica no ambiente da requisição (não em `g`), para nunca
    vazar entre requisições quando existe um contexto de aplicação mais externo (testes, CLI)."""
    env = request.environ
    if "ff.user" not in env:
        uid = session.get("uid")
        user = db.session.get(User, uid) if uid else None
        env["ff.user"] = user if user is not None and user.active else None
    return env["ff.user"]


def login_user(user: User):
    request.environ.pop("ff.user", None)
    session.clear()
    session["uid"] = user.id
    session["csrf"] = secrets.token_hex(16)
    session.permanent = False


def logout_user():
    request.environ.pop("ff.user", None)
    session.clear()


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def init_security(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _csrf_protect():
        if app.config.get("TESTING_SKIP_CSRF"):
            return
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Sessão expirada. Recarregue a página e tente de novo.")

    @app.before_request
    def _staff_2fa_gate():
        u = current_user()
        if not u or not current_app.config["REQUIRE_2FA_STAFF"] or not u.is_staff:
            return
        if u.totp_secret or request.endpoint in ("auth.setup_2fa", "auth.logout", "static"):
            return
        return redirect(url_for("auth.setup_2fa"))

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("auth.login", next=request.path))
        if u.must_change_password and request.endpoint not in ("auth.change_password", "auth.logout"):
            return redirect(url_for("auth.change_password"))
        return fn(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(fn):
        @functools.wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if not current_user().has_role(*roles):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def active_partner_required(fn):
    """Portal do parceiro: só depois de aprovação, workshop e termos."""
    @functools.wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        u = current_user()
        p = u.partner
        if not p:
            abort(403)
        if p.status != "ativo":
            return redirect(url_for("portal.onboarding"))
        g.partner = p
        return fn(*a, **kw)
    return wrapper
