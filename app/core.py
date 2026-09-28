"""Configuração, extensões e utilitários compartilhados."""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import BigInteger
from sqlalchemy.types import TypeDecorator

db = SQLAlchemy()


class Money(TypeDecorator):
    """Decimal guardado como inteiro em centavos (sem erro de ponto flutuante)."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value) / 100


class Config:
    SECRET_KEY = os.environ.get("FF_SECRET_KEY", "dev-troque-esta-chave")
    SQLALCHEMY_DATABASE_URI = os.environ.get("FF_DATABASE_URL", "sqlite:///finder_fee.db")
    UPLOAD_DIR = os.environ.get("FF_UPLOAD_DIR", os.path.join(os.getcwd(), "instance", "uploads"))
    REPORT_DIR = os.environ.get("FF_REPORT_DIR", os.path.join(os.getcwd(), "instance", "reports"))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024          # NF: até 8 MB
    ALLOWED_UPLOADS = {"pdf", "xml", "png", "jpg", "jpeg"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FF_COOKIE_SECURE", "0") == "1"   # ligar em produção (HTTPS)
    MAX_FAILED_LOGINS = 5
    LOCK_MINUTES = 15
    IP_MAX_FAILURES = int(os.environ.get("FF_IP_MAX_FAILURES", "20"))      # erros por IP dentro da janela
    IP_WINDOW_MINUTES = int(os.environ.get("FF_IP_WINDOW_MINUTES", "15"))
    SESSION_IDLE_MINUTES = int(os.environ.get("FF_SESSION_IDLE_MINUTES", "120"))   # sem uso por esse tempo: sai
    SESSION_MAX_HOURS = int(os.environ.get("FF_SESSION_MAX_HOURS", "12"))          # duração máxima de uma sessão
    TRUSTED_PROXIES = int(os.environ.get("FF_TRUSTED_PROXIES", "0"))       # proxies reversos à frente do app (nginx etc.)
    REQUIRE_2FA_STAFF = os.environ.get("FF_REQUIRE_2FA", "0") == "1"
    PUBLIC_BASE_URL = os.environ.get("FF_PUBLIC_URL", "http://localhost:5000")
    SMTP_HOST = os.environ.get("FF_SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("FF_SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("FF_SMTP_USER", "")
    SMTP_PASS = os.environ.get("FF_SMTP_PASS", "")
    # starttls (porta 587) | ssl (porta 465) | none (só relay local). Vazio: ssl na porta 465, senão starttls.
    SMTP_SECURITY = os.environ.get("FF_SMTP_SECURITY", "")
    SMTP_CA_FILE = os.environ.get("FF_SMTP_CA_FILE", "")      # CA própria (servidor interno); vazio = CAs do sistema
    SMTP_TIMEOUT = int(os.environ.get("FF_SMTP_TIMEOUT", "15"))
    MAIL_FROM = os.environ.get("FF_MAIL_FROM", "programa@alltargets.example")
    TESTING = False


# --------------------------------------------------------------- formatação
def brl(value) -> str:
    if value is None:
        return "—"
    v = Decimal(str(value))
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def pct(value) -> str:
    v = Decimal(str(value)) * 100
    s = f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{s}%"


def dt(value, with_time=False) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def now() -> datetime:
    return datetime.now()


def today() -> date:
    """Data corrente (isolada aqui para os testes poderem simular o calendário)."""
    return date.today()
