"""Autenticação, permissões por perfil, proteção CSRF e TOTP (autenticação reforçada)."""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta

from flask import abort, current_app, flash, g, redirect, request, session, url_for

from .core import db, now
from .models import LoginAttempt, User, UserSession


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


def match_totp(secret: str, code: str, at: float | None = None, after_step: int | None = None) -> int | None:
    """Intervalo de 30 s em que o código confere (tolera um intervalo de relógio para cada lado), ou None.
    Com `after_step`, só aceita intervalos posteriores: um código já usado não entra de novo."""
    code = (code or "").strip().replace(" ", "")
    t = int((at or time.time()) // 30)
    for step in (t - 1, t, t + 1):
        if hmac.compare_digest(_hotp(secret, step), code) and (after_step is None or step > after_step):
            return step
    return None


def verify_totp(secret: str, code: str, at: float | None = None) -> bool:
    return match_totp(secret, code, at) is not None


def claim_totp_step(user: User, step: int) -> bool:
    """Marca o intervalo como usado, só se for posterior ao último. É um UPDATE condicional: de duas
    requisições simultâneas com o mesmo código, só uma consegue. Não faz commit."""
    n = (User.query.filter(User.id == user.id, (User.totp_last_step.is_(None)) | (User.totp_last_step < step))
         .update({"totp_last_step": step}, synchronize_session=False))
    if n:
        user.totp_last_step = step
    return bool(n)


def provisioning_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/AllTargets%20Finder%20Fee:{email}?secret={secret}&issuer=AllTargets"


# ----------------------------------------------------------------- limite por IP
def client_ip() -> str:
    """Endereço do cliente. Atrás de proxy reverso, o app aplica ProxyFix (FF_TRUSTED_PROXIES)."""
    return (request.remote_addr or "desconhecido")[:64]


def ip_blocked(ip: str | None = None) -> bool:
    cfg = current_app.config
    since = now() - timedelta(minutes=cfg["IP_WINDOW_MINUTES"])
    n = LoginAttempt.query.filter(LoginAttempt.ip == (ip or client_ip()), LoginAttempt.at >= since).count()
    return n >= cfg["IP_MAX_FAILURES"]


def record_failed_attempt(kind: str, ip: str | None = None):
    db.session.add(LoginAttempt(ip=ip or client_ip(), kind=kind))


def prune_login_attempts(older_than: timedelta = timedelta(days=1)) -> int:
    n = LoginAttempt.query.filter(LoginAttempt.at < now() - older_than).delete(synchronize_session=False)
    db.session.commit()
    return n


# ----------------------------------------------------------------- sessão
# O cookie assinado do Flask não pode ser revogado. Por isso ele leva só um token aleatório, e a sessão
# vale enquanto existir a linha correspondente em `user_session`.
_TOUCH_EVERY = timedelta(minutes=1)      # atualiza o "visto por último" no máximo uma vez por minuto


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _session_row() -> UserSession | None:
    token = session.get("sid")
    if not isinstance(token, str) or not token:
        return None
    row = UserSession.query.filter_by(token_hash=_token_hash(token)).first()
    if row is None:
        return None
    cfg, t = current_app.config, now()
    if (t - row.last_seen_at > timedelta(minutes=cfg["SESSION_IDLE_MINUTES"])
            or t - row.created_at > timedelta(hours=cfg["SESSION_MAX_HOURS"])):
        db.session.delete(row)
        db.session.commit()
        return None
    if t - row.last_seen_at > _TOUCH_EVERY:
        row.last_seen_at = t
        db.session.commit()
    return row


def current_user() -> User | None:
    """Usuário da requisição. O cache fica no ambiente da requisição (não em `g`), para nunca
    vazar entre requisições quando existe um contexto de aplicação mais externo (testes, CLI)."""
    env = request.environ
    if "ff.user" not in env:
        row = _session_row()
        user = db.session.get(User, row.user_id) if row else None
        env["ff.user"] = user if user is not None and user.active else None
    return env["ff.user"]


def login_user(user: User):
    """Abre a sessão. Não faz commit: quem chama grava junto com o restante do login."""
    request.environ.pop("ff.user", None)
    session.clear()
    token = secrets.token_urlsafe(32)
    db.session.add(UserSession(token_hash=_token_hash(token), user_id=user.id))
    session["sid"] = token
    session["csrf"] = secrets.token_hex(16)
    session.permanent = False


def logout_user():
    token = session.get("sid")
    if isinstance(token, str) and token:
        UserSession.query.filter_by(token_hash=_token_hash(token)).delete(synchronize_session=False)
        db.session.commit()
    request.environ.pop("ff.user", None)
    session.clear()


def end_sessions(user: User) -> int:
    """Encerra todas as sessões do usuário (troca de senha ou de aplicativo, senha redefinida, acesso desativado).
    Não faz commit."""
    return UserSession.query.filter(UserSession.user_id == user.id).delete(synchronize_session=False)


def prune_sessions() -> int:
    cfg = current_app.config
    t = now()
    n = UserSession.query.filter((UserSession.last_seen_at < t - timedelta(minutes=cfg["SESSION_IDLE_MINUTES"]))
                                 | (UserSession.created_at < t - timedelta(hours=cfg["SESSION_MAX_HOURS"]))
                                 ).delete(synchronize_session=False)
    db.session.commit()
    return n


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


# Scripts só do próprio site. Estilo inline continua liberado (atributos style= nas telas) e as fontes vêm do Google Fonts.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


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
        resp.headers.setdefault("Content-Security-Policy", CSP)
        if app.config["SESSION_COOKIE_SECURE"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
