"""Auxiliares das telas: trajeto da indicação, rótulos e leitura de formulários."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import flash

from ..models import ELIGIBILITY, FIN_STATES, STAGES, Contract, CommissionLine, Referral
from ..services import ServiceError


def parse_money(raw: str) -> Decimal:
    s = (raw or "").strip().replace("R$", "").replace(" ", "")
    if not s:
        raise ServiceError("Informe o valor.")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ServiceError(f"Valor inválido: {raw}")


def parse_date(raw: str, label: str = "data") -> date:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ServiceError(f"Informe uma {label} válida.")


def fail(e: Exception):
    flash(str(e), "error")


def ok(msg: str):
    flash(msg, "ok")


def referral_status(ref: Referral) -> tuple[str, str]:
    """Rótulo e tom para o parceiro. A fila de espera aparece como 'Aguardando início do atendimento'."""
    if ref.eligibility == "aceita":
        if ref.waiting:
            return "Aguardando início do atendimento", "wait"
        if ref.stage == "perdida":
            return "Encerrada sem contrato", "bad"
        if ref.stage == "ganha":
            return "Contrato assinado", "ok"
        return "Aceita e protegida", "ok"
    tone = {"recebida": "wait", "em_validacao": "wait", "rejeitada": "bad", "expirada": "bad"}[ref.eligibility]
    return ELIGIBILITY[ref.eligibility], tone


def track(ref: Referral) -> list[dict]:
    """Cinco marcos: registrada, aceita, em atendimento, contrato assinado e comissão paga."""
    contracts = [c for c in ref.contracts if not c.cancelled_at]
    paid = CommissionLine.query.filter_by(referral_id=ref.id, kind="cliente", state="paga").count() > 0
    steps = [{"label": "Registrada", "state": "done", "note": ref.created_at.strftime("%d/%m/%Y")}]
    if ref.eligibility in ("rejeitada", "expirada"):
        steps.append({"label": ELIGIBILITY[ref.eligibility], "state": "stopped", "note": ""})
        steps += [{"label": l, "state": "pending", "note": ""} for l in ("Em atendimento", "Contrato assinado", "Comissão paga")]
        return steps
    if ref.eligibility in ("recebida", "em_validacao"):
        steps.append({"label": "Aceita", "state": "current", "note": "Em validação"})
        steps += [{"label": l, "state": "pending", "note": ""} for l in ("Em atendimento", "Contrato assinado", "Comissão paga")]
        return steps
    steps.append({"label": "Aceita", "state": "done", "note": ref.accepted_at.strftime("%d/%m/%Y") if ref.accepted_at else ""})
    if ref.waiting:
        steps.append({"label": "Em atendimento", "state": "current", "note": "Aguardando início"})
    elif ref.stage == "perdida":
        steps.append({"label": "Em atendimento", "state": "stopped", "note": "Encerrada"})
    else:
        steps.append({"label": "Em atendimento", "state": "done", "note": STAGES[ref.stage] if not contracts else ""})
    if contracts:
        steps.append({"label": "Contrato assinado", "state": "done", "note": contracts[0].signed_at.strftime("%d/%m/%Y")})
        steps.append({"label": "Comissão paga", "state": "done" if paid else "current", "note": "" if paid else "Conforme os recebimentos"})
    else:
        cur = ref.stage not in ("perdida",) and not ref.waiting
        steps.append({"label": "Contrato assinado", "state": "current" if cur else "pending", "note": ""})
        steps.append({"label": "Comissão paga", "state": "pending", "note": ""})
    return steps


LINE_TONE = {"aguardando_condicao": "wait", "aprovada": "info", "programada": "info", "paga": "ok", "ajustada": "info", "cancelada": "bad"}
LINE_HELP = {
    "estimada": "Projeção. Não é saldo e depende do recebimento do cliente.",
    "aguardando_condicao": "Evento identificado. Falta conferência, recebimento, nota fiscal ou outra condição da regra.",
    "aprovada": "Cálculo e elegibilidade conferidos. Emita e anexe a nota fiscal.",
    "programada": "Pagamento previsto para o ciclo indicado.",
    "paga": "Pagamento realizado.",
    "ajustada": "Valor alterado por ajuste, estorno ou compensação.",
    "cancelada": "Cancelada. Veja o motivo no demonstrativo.",
}
