from datetime import date
from decimal import Decimal as D

import pytest

from app import cnpj, engine as e


# ------------------------------------------------------------------ CNPJ
def test_cnpj_valido_e_invalido():
    ok = cnpj.make_valid("11222333")
    assert cnpj.is_valid(ok)
    assert cnpj.is_valid(cnpj.fmt(ok))
    assert not cnpj.is_valid(ok[:-1] + ("0" if ok[-1] != "0" else "1"))
    assert not cnpj.is_valid("11111111111111")
    assert not cnpj.is_valid("123")


def test_cnpj_matriz_filial_e_raiz():
    m = cnpj.make_valid("11222333", "0001")
    f = cnpj.make_valid("11222333", "0002")
    assert cnpj.is_headquarters(m) and not cnpj.is_headquarters(f)
    assert cnpj.base(m) == cnpj.base(f) == "11222333"


# ------------------------------------------------------------------ comissão
def test_exemplo_da_especificacao_7_3():
    # NF 10.000, impostos 1.000, comissão 15% => 1.350; bônus 50% => 675; custo 2.025 = 22,5% de 9.000
    c = e.commission(D("10000"), D("10000"), D("1000"), D("0.15"))
    b = e.bonus(c, D("0.5"))
    assert c == D("1350.00") and b == D("675.00")
    assert c + b == D("2025.00")
    assert (c + b) / e.net_base(D("10000"), D("10000"), D("1000")) == D("0.225")
    assert e.channel_cost_ratio(True, D("0.15"), D("0.5")) == D("0.225")
    assert e.channel_cost_ratio(False, D("0.15"), D("0.5")) == D("0.15")


def test_recebimento_parcial_mantem_proporcao_de_impostos():
    # NF 10.000 com 1.000 de impostos; cliente paga 4.000 e depois 6.000
    c1 = e.commission(D("4000"), D("10000"), D("1000"), D("0.15"))
    c2 = e.commission(D("6000"), D("10000"), D("1000"), D("0.15"))
    assert c1 == D("540.00") and c2 == D("810.00")
    assert c1 + c2 == e.commission(D("10000"), D("10000"), D("1000"), D("0.15"))


def test_estorno_gera_comissao_negativa_simetrica():
    pos = e.commission(D("5000"), D("10000"), D("1000"), D("0.15"))
    neg = e.commission(D("-5000"), D("10000"), D("1000"), D("0.15"))
    assert pos == -neg


def test_arredondamento_meio_para_cima():
    assert e.q("2.675") == D("2.68")
    assert e.commission(D("100.10"), D("100.10"), D("0"), D("0.15")) == D("15.02")  # 15,015 -> 15,02


def test_validacoes_de_impostos_e_nf():
    with pytest.raises(ValueError):
        e.commission(D("1"), D("0"), D("0"), D("0.15"))
    with pytest.raises(ValueError):
        e.commission(D("1"), D("100"), D("101"), D("0.15"))
    with pytest.raises(ValueError):
        e.commission(D("1"), D("100"), D("-1"), D("0.15"))


def test_ilustracao_cgv_sobre_base_liquida():
    # Setup 40k + retainer 18k x 12 = 256k bruto; com 10% de impostos, base líquida 230.400
    gross = D("40000") + D("18000") * 12
    net = e.q(gross * D("0.9"))
    com = e.q(net * D("0.15"))
    assert net == D("230400.00")
    assert com == D("34560.00") and e.bonus(com, D("0.5")) == D("17280.00")


# ------------------------------------------------------------------ cronograma / janela
def test_janela_12_meses_inclui_o_aniversario_por_padrao():
    sig = date(2027, 1, 15)
    items = [e.ScheduleItem("Retainer CGV", "retainer", D("18000"), date(2027, 2, 15), months=14)]
    plan = e.build_schedule(sig, items, 12)
    assert len(plan) == 14
    dentro = [p for p in plan if p.in_window]
    # vencimentos de fev/27 a jan/28 (12 mensalidades, a 12ª no aniversário) estão dentro;
    # fev/28 em diante, fora
    assert len(dentro) == 12
    assert dentro[-1].due_date == date(2028, 1, 15)
    assert not plan[-1].in_window


def test_janela_com_borda_exclusiva_deixa_o_aniversario_de_fora():
    sig = date(2027, 1, 15)
    items = [e.ScheduleItem("Retainer CGV", "retainer", D("18000"), date(2027, 2, 15), months=12)]
    assert sum(p.in_window for p in e.build_schedule(sig, items, 12, inclusive_end=False)) == 11
    assert sum(p.in_window for p in e.build_schedule(sig, items, 12, inclusive_end=True)) == 12


def test_evento_unico_e_item_misto_uma_linha_por_item():
    sig = date(2027, 3, 1)
    items = [
        e.ScheduleItem("Setup", "evento_unico", D("40000"), date(2027, 3, 10)),
        e.ScheduleItem("Retainer", "retainer", D("18000"), date(2027, 4, 10), months=12),
    ]
    plan = e.build_schedule(sig, items, 12)
    assert sum(1 for p in plan if p.item_index == 0) == 1
    assert sum(1 for p in plan if p.item_index == 1) == 12
    # retainer: 1ª parcela abr/27 ... 12ª mar/28; a de 10/03/2028 vence depois do aniversário
    # (01/03/2028) e fica fora, com qualquer regra de borda
    assert [p.in_window for p in plan if p.item_index == 1].count(True) == 11


def test_parcela_vencida_dentro_da_janela_mas_paga_depois_continua_valida():
    sig = date(2027, 1, 10)
    assert e.in_window(date(2027, 12, 10), sig, 12) is True    # vence dentro
    assert e.in_window(date(2028, 1, 11), sig, 12) is False    # vence depois do aniversário
    assert e.in_window(date(2028, 1, 10), sig, 12) is True     # no aniversário (borda inclusiva)
    assert e.in_window(date(2028, 1, 10), sig, 12, inclusive_end=False) is False


def test_add_months_dia_31():
    assert e.add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert e.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_schedule_rejeita_valor_invalido():
    with pytest.raises(ValueError):
        e.build_schedule(date(2027, 1, 1), [e.ScheduleItem("x", "retainer", D("0"), date(2027, 1, 1), 3)])
    with pytest.raises(ValueError):
        e.build_schedule(date(2027, 1, 1), [e.ScheduleItem("x", "outro", D("1"), date(2027, 1, 1), 3)])


# ------------------------------------------------------------------ percentuais
def test_resolve_pct_exceção_mais_especifica_vence():
    rules = {"commission_pct": "0.15", "commission_overrides": [
        {"product": "Valuation", "pct": "0.10"},
        {"product": "Valuation", "kind": "evento_unico", "pct": "0.08"},
        {"audience": "contador", "pct": "0.12"},
    ]}
    assert e.resolve_pct(rules, "CGV", "retainer", "prof_mba") == D("0.15")
    assert e.resolve_pct(rules, "Valuation", "retainer", None) == D("0.10")
    assert e.resolve_pct(rules, "Valuation", "evento_unico", None) == D("0.08")
    assert e.resolve_pct(rules, "CGV", "retainer", "contador") == D("0.12")


# ------------------------------------------------------------------ pagamento
def test_ciclo_mes_subsequente_ao_recebimento():
    # cliente paga em março; NF enviada em abril, antes do prazo (dia 20) -> paga em abril
    assert e.payment_cycle(date(2027, 3, 12), date(2027, 4, 5), 20) == date(2027, 4, 1)
    # NF enviada ainda em março (antes do recebimento do mês seguinte) -> continua abril
    assert e.payment_cycle(date(2027, 3, 12), date(2027, 3, 30), 20) == date(2027, 4, 1)


def test_nf_fora_do_prazo_passa_ao_ciclo_seguinte():
    assert e.payment_cycle(date(2027, 3, 12), date(2027, 4, 25), 20) == date(2027, 5, 1)


def test_sem_nf_sem_ciclo_e_sem_prazo_configurado():
    assert e.payment_cycle(date(2027, 3, 12), None, 20) is None
    assert e.payment_cycle(date(2027, 3, 12), date(2027, 4, 28), None) == date(2027, 4, 1)


def test_dias_uteis():
    seg = date(2027, 3, 1)  # segunda-feira
    assert e.add_business_days(seg, 5) == date(2027, 3, 8)
    assert e.business_days_between(seg, date(2027, 3, 8)) == 5
    assert e.business_days_between(date(2027, 3, 5), date(2027, 3, 8)) == 1  # sex -> seg
