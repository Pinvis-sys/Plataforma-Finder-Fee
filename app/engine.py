"""Motor de cálculo do Finder Fee (funções puras, sem banco).

Regras (Programa Finder Fee + Especificação v2, seção 7):
  * Comissão = valor recebido x (1 - impostos / valor da NF) x percentual vigente.
  * Cada parcela recebida dentro da janela gera comissão; a janela vale para parcelas
    com VENCIMENTO dentro dos 12 meses da assinatura (premissa 7.5, confirmada).
  * Bônus da Camada 2 = 50% da comissão de cada parcela do primeiro contrato do recrutado.
  * Pagamento no mês subsequente ao recebimento, contra NF do parceiro dentro do prazo.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 28
CENT = Decimal("0.01")


def D(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x).replace(",", ".")) if isinstance(x, str) else Decimal(str(x))


def q(x) -> Decimal:
    """Arredonda para centavos (meio para cima)."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- datas
def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def next_month(d: date) -> date:
    return add_months(first_of_month(d), 1)


def add_business_days(d: date, n: int, holidays: frozenset = frozenset()) -> date:
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += timedelta(days=step)
        if d.weekday() < 5 and d not in holidays:
            left -= 1
    return d


def business_days_between(a: date, b: date, holidays: frozenset = frozenset()) -> int:
    """Dias úteis entre a (exclusivo) e b (inclusivo). Negativo se b < a."""
    if b < a:
        return -business_days_between(b, a, holidays)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            n += 1
    return n


# ---------------------------------------------------------------- cronograma
@dataclass(frozen=True)
class ScheduleItem:
    description: str
    kind: str            # 'evento_unico' | 'retainer'
    amount: Decimal      # valor bruto (NF) de cada parcela
    first_due: date
    months: int = 1      # nº de mensalidades (retainer)


@dataclass(frozen=True)
class ParcelPlan:
    item_index: int
    description: str
    kind: str
    number: int
    total: int
    due_date: date
    gross_amount: Decimal
    in_window: bool


def in_window(due: date, signature: date, window_months: int, inclusive_end: bool = True) -> bool:
    """Parcela dentro da janela de remuneração (vencimento até N meses da assinatura).

    Borda: com inclusive_end=True (padrão) a parcela que vence exatamente no aniversário
    de N meses ainda conta ("até o 12º mês": 12 mensalidades a partir de um mês após a
    assinatura ficam todas dentro). Com False, a janela é [assinatura, assinatura + N meses).
    Ponto a confirmar com a All Targets: parâmetro `window_end_inclusive` do conjunto de regras."""
    end = add_months(signature, window_months)
    return due <= end if inclusive_end else due < end


def build_schedule(signature: date, items: list, window_months: int = 12,
                   inclusive_end: bool = True) -> list:
    plans: list[ParcelPlan] = []
    for idx, it in enumerate(items):
        if it.amount <= 0:
            raise ValueError(f"Item '{it.description}': valor deve ser positivo")
        if it.kind not in ("evento_unico", "retainer"):
            raise ValueError(f"Item '{it.description}': tipo inválido ({it.kind})")
        n = 1 if it.kind == "evento_unico" else max(1, it.months)
        for k in range(n):
            due = add_months(it.first_due, k)
            plans.append(ParcelPlan(idx, it.description, it.kind, k + 1, n, due, q(it.amount),
                                    in_window(due, signature, window_months, inclusive_end)))
    plans.sort(key=lambda p: (p.due_date, p.item_index))
    return plans


# ---------------------------------------------------------------- comissão
def net_ratio(nf_value, taxes) -> Decimal:
    nf_value, taxes = D(nf_value), D(taxes)
    if nf_value <= 0:
        raise ValueError("Valor da NF deve ser positivo")
    if taxes < 0 or taxes > nf_value:
        raise ValueError("Impostos devem estar entre zero e o valor da NF")
    return 1 - taxes / nf_value


def net_base(received, nf_value, taxes) -> Decimal:
    """Parte líquida do valor recebido (mantém proporção em recebimentos parciais)."""
    return q(D(received) * net_ratio(nf_value, taxes))


def commission(received, nf_value, taxes, pct) -> Decimal:
    """Comissão do parceiro. `received` pode ser negativo (estorno)."""
    return q(D(received) * net_ratio(nf_value, taxes) * D(pct))


def bonus(commission_amount, bonus_pct) -> Decimal:
    """Bônus de expansão: % da comissão já arredondada (reprodutível pelo demonstrativo)."""
    return q(D(commission_amount) * D(bonus_pct))


def estimate_commission(gross, pct, tax_rate) -> Decimal:
    """Projeção para parcelas ainda não recebidas (usa a alíquota configurada)."""
    return q(D(gross) * (1 - D(tax_rate)) * D(pct))


def channel_cost_ratio(first_sale_of_recruited: bool, pct, bonus_pct) -> Decimal:
    """Custo do canal sobre a base líquida: 15% ou 22,5% na primeira venda de um recrutado."""
    pct, bonus_pct = D(pct), D(bonus_pct)
    return pct + pct * bonus_pct if first_sale_of_recruited else pct


def resolve_pct(rules: dict, product, kind, audience) -> Decimal:
    """Percentual vigente: padrão, com exceções por produto/tipo de base/público.
    A exceção mais específica vence; em empate, a primeira da lista."""
    best, best_score = D(rules["commission_pct"]), 0
    for ov in rules.get("commission_overrides", []):
        score, ok = 0, True
        for key, val in (("product", product), ("kind", kind), ("audience", audience)):
            if ov.get(key):
                if ov[key] == val:
                    score += 1
                else:
                    ok = False
                    break
        if ok and score > best_score:
            best, best_score = D(ov["pct"]), score
    return best


# ---------------------------------------------------------------- pagamento
def deadline_date(cycle_month: date, deadline_day):
    if not deadline_day:
        return None
    last = calendar.monthrange(cycle_month.year, cycle_month.month)[1]
    return date(cycle_month.year, cycle_month.month, min(int(deadline_day), last))


def payment_cycle(receipt_date: date, nf_date, deadline_day):
    """1º dia do mês do pagamento. Base: mês seguinte ao recebimento. NF enviada depois
    do prazo do mês passa ao ciclo seguinte. Sem NF: ainda não há ciclo (None)."""
    if nf_date is None:
        return None
    base = next_month(receipt_date)
    m = first_of_month(nf_date)
    dl = deadline_date(m, deadline_day)
    if dl and nf_date > dl:
        m = next_month(m)
    return max(base, m)
