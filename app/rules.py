"""Conjuntos de regras versionados (Tela 10). "Piloto v1" nasce congelado.

Valores marcados como PROVISÓRIO ainda dependem de decisão da All Targets e estão
listados em PENDING_DECISIONS para aparecer na Tela 10.
"""
from __future__ import annotations

import copy
from datetime import date

from .core import db, now

DEFAULT_RULES: dict = {
    # --- remuneração (fechado no Programa Finder Fee / Especificação v2)
    "commission_pct": "0.15",
    "commission_overrides": [],          # {"product","kind","audience","pct"} — pendente de precificação
    "window_months": 12,
    "window_end_inclusive": True,        # borda da janela: a confirmar
    "bonus_pct": "0.50",
    # --- tetos (internos, exceto recrutamento)
    "annual_cap_per_partner": 12,
    "aggregate_cap": None,               # número a definir depois do piloto
    "aggregate_cap_period": "quarter",   # 'quarter' | 'year'  (recomendado: trimestre)
    "recruit_cap_by_year": {"2026": 5, "2027": 5, "default": 3},
    # --- piloto
    "pilot_mode": True,
    "pilot_max_partners": 5,
    "pilot_recruit_limit": 2,
    "pilot_start": "2026-10-01",
    "pilot_end": "2027-06-30",
    "pilot_min_days": 90,
    # --- prazos e proteção
    "sla_review_days": 5,                # aprovação de parceiro e validação de indicação
    "first_contact_days": 2,
    "protection_days": 90,               # PROVISÓRIO: definir no regulamento
    "protection_warning_days": 15,
    "stale_days": 7,
    "nf_deadline_day": 20,               # PROVISÓRIO: prazo mensal da NF a definir
    # --- impostos (estimativa e conferência; o valor real vem da NF)
    "tax_rates": [{"from": "2026-01-01", "total": "0.10", "note": "ilustrativo"}],
    "tax_tolerance": "0.02",
    # --- elegibilidade e conformidade
    "duplicate_scope": "cnpj_base",      # 'cnpj_base' (matriz e filiais) | 'cnpj'
    "consent_required": True,            # LGPD antes do contato comercial
    "eligible_levels": [1, 2],           # Nível 3 não gera comissão direta
    "old_contact_policy": "review",      # 'review' | 'block'
    "high_frequency_threshold": 6,       # aceitas em 12 meses (alerta de recorrência)
    "channel_cost_limit": None,          # a definir com a precificação final
    "holidays": [],
}

PENDING_DECISIONS = {
    "protection_days": "Prazo de proteção comercial (regulamento) ainda não definido.",
    "nf_deadline_day": "Prazo mensal para envio da NF ainda não definido.",
    "aggregate_cap": "Número do teto agregado fica para depois do piloto.",
    "window_end_inclusive": "Confirmar se a parcela que vence no aniversário de 12 meses conta.",
    "duplicate_scope": "Definir tratamento de matriz, filiais e empresas do mesmo grupo.",
    "channel_cost_limit": "Limite de custo do canal depende da precificação final e da margem.",
    "commission_overrides": "Percentual por produto, tipo de base e público depende da precificação.",
    "tax_rates": "Alíquotas são ilustrativas; confirmar com a área tributária.",
}


class RuleSet(db.Model):
    __tablename__ = "rule_set"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    active = db.Column(db.Boolean, default=False, nullable=False)
    frozen = db.Column(db.Boolean, default=False, nullable=False)
    params = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    created_by = db.Column(db.String(120))
    note = db.Column(db.String(300))

    def get(self, key):
        return merged(self.params).get(key)

    @property
    def rules(self) -> dict:
        return merged(self.params)


def merged(params: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_RULES)
    out.update(params or {})
    return out


def active_ruleset() -> RuleSet:
    rs = RuleSet.query.filter_by(active=True).order_by(RuleSet.id.desc()).first()
    if rs is None:
        rs = RuleSet(name="Piloto v1", active=True, frozen=True, params=copy.deepcopy(DEFAULT_RULES),
                     created_by="sistema", note="Regras congeladas do piloto")
        db.session.add(rs)
        db.session.commit()
    return rs


def new_version(base: RuleSet, changes: dict, author: str, note: str = "") -> RuleSet:
    """Cria nova versão (não altera registros existentes: cada indicação guarda a versão da aceitação)."""
    params = merged(base.params)
    params.update(changes)
    n = RuleSet.query.count() + 1
    RuleSet.query.filter_by(active=True).update({"active": False})
    rs = RuleSet(name=f"Regras v{n}", active=True, frozen=False, params=params, created_by=author, note=note)
    db.session.add(rs)
    db.session.flush()
    return rs


def tax_rate_on(rules: dict, on: date):
    from decimal import Decimal
    rates = sorted(rules["tax_rates"], key=lambda r: r["from"])
    chosen = Decimal(rates[0]["total"])
    for r in rates:
        if date.fromisoformat(r["from"]) <= on:
            chosen = Decimal(r["total"])
    return chosen


def recruit_cap_for_year(rules: dict, year: int) -> int:
    caps = rules["recruit_cap_by_year"]
    return int(caps.get(str(year), caps.get("default", 3)))


def holidays(rules: dict | None = None) -> frozenset:
    """Dias que não contam nos prazos em dias úteis: os cadastrados em Gestão > Feriados e, por
    compatibilidade, os que estiverem em rules["holidays"] (lista usada antes da tela existir)."""
    from .models import Holiday
    days = {h.day for h in Holiday.query.all()}
    days |= {date.fromisoformat(d) for d in (rules or {}).get("holidays", [])}
    return frozenset(days)
