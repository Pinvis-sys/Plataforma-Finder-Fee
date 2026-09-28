from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest

from app import cnpj as cn, engine as eng, seed, services as svc
from app.core import db
from app.models import (CommissionLine, DuplicateAttempt, KnownCompany, Partner, Referral, TermDocument, User)
from app.rules import active_ruleset, new_version


def mkref(partner, root, **kw):
    data = {"cnpj": cn.make_valid(root), "company_name": "Empresa " + root, "decisor_name": "Fulano",
            "signal": "Adiamento de CAPEX"}
    data.update(kw)
    return svc.register_referral(partner, data)


@pytest.fixture()
def p1(app):
    return seed.make_active_partner("Contábil Alfa", "10000001", "a@t.test")


@pytest.fixture()
def p2(app):
    return seed.make_active_partner("Mentor Beta", "10000002", "b@t.test", kind="prof_mba_a")


# ------------------------------------------------------------- onboarding
def test_portal_so_libera_apos_aprovacao_workshop_e_termos(app):
    for d in TermDocument.query.all():
        d.legal_approved = True
    inv = svc.create_direct_invite("t")
    p = svc.apply_partner({"cnpj": cn.make_valid("20000001"), "legal_name": "X", "contact_name": "Y", "email": "x@t.test"},
                          "senha-segura-123", direct_token=inv.token)
    assert p.status == "candidato"
    with pytest.raises(svc.ServiceError):
        svc.review_partner(p, True, "gestor")           # CNPJ não confirmado
    p.cnpj_verified = True
    svc.review_partner(p, True, "gestor")
    assert p.status == "aprovado"
    svc.mark_workshop(p, "gestor")
    assert p.status == "aprovado"                        # faltam termos
    for k in list(__import__("app.models", fromlist=["TERM_KEYS"]).TERM_KEYS)[:-1]:
        svc.accept_term(p, k)
    assert p.status == "aprovado"
    svc.accept_term(p, "pos_workshop")
    assert p.status == "ativo"


def test_termo_sem_liberacao_juridica_nao_pode_ser_aceito(app):
    inv = svc.create_direct_invite("t")
    p = svc.apply_partner({"cnpj": cn.make_valid("20000002"), "legal_name": "X", "contact_name": "Y", "email": "y@t.test"},
                          "senha-segura-123", direct_token=inv.token)
    p.cnpj_verified = True
    svc.review_partner(p, True, "gestor")
    with pytest.raises(svc.ServiceError, match="jurídico"):
        svc.accept_term(p, "contrato")


def test_candidatura_exige_convite_e_aviso_do_bonus(app, p1):
    base = {"cnpj": cn.make_valid("20000003"), "legal_name": "R", "contact_name": "S", "email": "r@t.test"}
    with pytest.raises(svc.ServiceError, match="convite"):
        svc.apply_partner(base, "senha-segura-123")
    with pytest.raises(svc.ServiceError, match="bônus"):
        svc.apply_partner(base, "senha-segura-123", token=p1.invite_token, accept_notice=False)
    p = svc.apply_partner(base, "senha-segura-123", token=p1.invite_token, accept_notice=True)
    assert p.invited_by_id == p1.id and p.bonus_notice_accepted_at


def test_piloto_limita_parceiros_iniciais_e_recrutados(app):
    for d in TermDocument.query.all():
        d.legal_approved = True
    ps = [seed.make_active_partner(f"Direto {i}", f"3000000{i}", f"d{i}@t.test") for i in range(5)]
    inv = svc.create_direct_invite("t")
    p6 = svc.apply_partner({"cnpj": cn.make_valid("30000009"), "legal_name": "Sexto", "contact_name": "Z", "email": "z@t.test"},
                           "senha-segura-123", direct_token=inv.token)
    p6.cnpj_verified = True
    with pytest.raises(svc.ServiceError, match="até 5"):
        svc.review_partner(p6, True, "gestor")
    # recrutamento: limite de 2 no total do programa; vaga volta se o candidato for rejeitado
    r1 = svc.apply_partner({"cnpj": cn.make_valid("40000001"), "legal_name": "R1", "contact_name": "a", "email": "r1@t.test"},
                           "senha-segura-123", token=ps[0].invite_token, accept_notice=True)
    r2 = svc.apply_partner({"cnpj": cn.make_valid("40000002"), "legal_name": "R2", "contact_name": "a", "email": "r2@t.test"},
                           "senha-segura-123", token=ps[1].invite_token, accept_notice=True)
    ok, _ = svc.recruit_link_state(ps[2])
    assert not ok
    with pytest.raises(svc.ServiceError):
        svc.apply_partner({"cnpj": cn.make_valid("40000003"), "legal_name": "R3", "contact_name": "a", "email": "r3@t.test"},
                          "senha-segura-123", token=ps[2].invite_token, accept_notice=True)
    r2.cnpj_verified = True
    svc.review_partner(r2, False, "gestor", reason="Fora do perfil")
    assert svc.recruit_link_state(ps[2])[0]


# ------------------------------------------------------------- indicação e prioridade
def test_indicacao_registra_protocolo_e_bloqueia_duplicidade_sem_revelar(app, p1, p2):
    r = mkref(p1, "50000001")
    assert r.protocol.startswith("IND-") and r.eligibility == "recebida" and r.consent_status == "pendente"
    with pytest.raises(svc.Unavailable) as e:
        mkref(p2, "50000001")
    assert str(e.value) == svc.UNAVAILABLE_MSG and "Contábil" not in str(e.value)
    with pytest.raises(svc.Unavailable, match="Você já indicou"):
        mkref(p1, "50000001")
    assert DuplicateAttempt.query.count() == 2


def test_matriz_e_filial_contam_como_mesma_empresa_por_padrao(app, p1, p2):
    svc_data = {"cnpj": cn.make_valid("50000002", "0001")}
    svc.register_referral(p1, {**svc_data, "decisor_name": "F", "signal": "s"})
    with pytest.raises(svc.Unavailable):
        svc.register_referral(p2, {"cnpj": cn.make_valid("50000002", "0002"), "decisor_name": "F", "signal": "s"})


def test_base_comercial_bloqueia_cliente_atual(app, p1):
    num = cn.make_valid("60000001")
    db.session.add(KnownCompany(cnpj=num, cnpj_base=cn.base(num), kind="cliente_atual"))
    db.session.commit()
    with pytest.raises(svc.Unavailable):
        mkref(p1, "60000001")


def test_contato_antigo_vai_para_validacao(app, p1):
    num = cn.make_valid("60000002")
    db.session.add(KnownCompany(cnpj=num, cnpj_base=cn.base(num), kind="contato_antigo"))
    db.session.commit()
    r = mkref(p1, "60000002")
    assert r.eligibility == "em_validacao" and r.old_contact_flag


def test_indice_unico_garante_prioridade_em_registros_simultaneos(app, p1, p2):
    """Mesmo se a checagem prévia passar (corrida), o banco só aceita um registro ativo por empresa."""
    from sqlalchemy.exc import IntegrityError
    a = mkref(p1, "50000003")
    dup = Referral(partner_id=p2.id, cnpj=a.cnpj, cnpj_base=a.cnpj_base, dedupe_key=a.dedupe_key, active=True)
    db.session.add(dup)
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_rejeitada_libera_a_empresa_para_nova_indicacao(app, p1, p2):
    r = mkref(p1, "50000004")
    svc.validate_referral(r, "reject", "gestor", reason="Fora do perfil")
    r2 = mkref(p2, "50000004")
    assert r2.eligibility == "recebida"


def test_rejeicao_exige_motivo(app, p1):
    r = mkref(p1, "50000005")
    with pytest.raises(svc.ServiceError):
        svc.validate_referral(r, "reject", "gestor")


def test_cnpj_invalido_e_campos_obrigatorios(app, p1):
    with pytest.raises(svc.ServiceError, match="inválido"):
        svc.register_referral(p1, {"cnpj": "11.111.111/1111-11", "decisor_name": "F", "signal": "s"})
    with pytest.raises(svc.ServiceError, match="sinal"):
        svc.register_referral(p1, {"cnpj": cn.make_valid("50000006"), "decisor_name": "F", "signal": ""})


# ------------------------------------------------------------- aceite, tetos e espera
def test_aceite_inicia_atendimento_e_protecao(app, p1):
    r = mkref(p1, "51000001")
    svc.validate_referral(r, "accept", "gestor", fits_target=True)
    assert r.eligibility == "aceita" and not r.waiting and r.service_started_at
    assert r.protection_until == date.today() + timedelta(days=90)
    assert r.rule_set_id == active_ruleset().id


def test_teto_agregado_coloca_em_espera_sem_protecao_e_libera_com_motivo(app, p1, p2):
    rs = active_ruleset()
    new_version(rs, {"aggregate_cap": 1}, "t")
    db.session.commit()
    a, b = mkref(p1, "51000002"), mkref(p2, "51000003")
    svc.validate_referral(a, "accept", "g")
    svc.validate_referral(b, "accept", "g")
    assert not a.waiting and b.waiting and b.waiting_reason == "teto_agregado" and b.protection_until is None
    with pytest.raises(svc.ServiceError):
        svc.start_service(b, "g")
    svc.start_service(b, "g", override_reason="Capacidade liberada pelos sócios")
    assert not b.waiting and b.protection_until


def test_teto_anual_do_parceiro_e_controle_interno(app, p1):
    new_version(active_ruleset(), {"annual_cap_per_partner": 2}, "t")
    db.session.commit()
    rs = [mkref(p1, f"5200000{i}") for i in range(3)]
    for r in rs:
        svc.validate_referral(r, "accept", "g")
    assert [r.waiting for r in rs] == [False, False, True]
    assert rs[2].waiting_reason == "teto_parceiro"


def test_protecao_expira_e_prorroga_com_motivo(app, p1):
    r = mkref(p1, "51000004")
    svc.validate_referral(r, "accept", "g")
    with pytest.raises(svc.ServiceError):
        svc.extend_protection(r, 30, "", "g")
    old = r.protection_until
    svc.extend_protection(r, 30, "Reunião marcada", "g")
    assert r.protection_until == max(old, date.today()) + timedelta(days=30)
    r.protection_until = date.today() - timedelta(days=1)
    assert svc.expire_protections() == 1
    assert r.eligibility == "expirada" and not r.active


def test_contato_comercial_exige_consentimento_lgpd(app, p1):
    r = mkref(p1, "51000005")
    svc.validate_referral(r, "accept", "g")
    with pytest.raises(svc.ServiceError, match="consentimento"):
        svc.set_stage(r, "qualificacao", "g")
    svc.register_consent(r, "Aceite eletrônico 01/03", "g")
    svc.set_stage(r, "qualificacao", "g")
    assert r.first_contact_at
    with pytest.raises(svc.ServiceError):
        svc.set_stage(r, "perdida", "g")           # exige motivo
    with pytest.raises(svc.ServiceError):
        svc.set_stage(r, "ganha", "g")             # só via contrato


# ------------------------------------------------------------- contrato, recebimento, comissão e bônus
def won_contract(partner, root="53000001", signed=None):
    r = mkref(partner, root)
    svc.validate_referral(r, "accept", "g", fits_target=True)
    svc.register_consent(r, "ok", "g")
    signed = signed or date.today()
    items = [eng.ScheduleItem("Retainer", "retainer", D("10000"), eng.add_months(signed, 1), months=12)]
    return r, svc.win_contract(r, "CGV", None, signed, items, "g")


def test_comissao_por_parcela_recebida_pelo_exemplo_da_especificacao(app, p1):
    r, c = won_contract(p1)
    assert len(c.installments) == 12 and all(i.in_window for i in c.installments)
    inst = c.installments[0]
    rc, lines, warns = svc.register_receipt(inst, date.today(), D("10000"), "NF-1", D("10000"), D("1000"), "fin")
    assert len(lines) == 1 and lines[0].amount == D("1350.00") and lines[0].base_net == D("9000.00")
    assert lines[0].state == "aguardando_condicao" and lines[0].kind == "cliente"
    assert not warns and inst.status == "recebida"


def test_bonus_so_no_primeiro_contrato_do_recrutado_por_parcela(app, p1):
    p3 = seed.make_active_partner("Recrutada", "10000003", "c@t.test", invited_token=p1.invite_token)
    r, c = won_contract(p3, "53000002")
    assert c.first_for_recruited and c.bonus_recipient_id == p1.id
    rc, lines, _ = svc.register_receipt(c.installments[0], date.today(), D("10000"), "NF-1", D("10000"), D("1000"), "fin")
    kinds = {l.kind: l for l in lines}
    assert kinds["cliente"].amount == D("1350.00") and kinds["bonus"].amount == D("675.00")
    assert kinds["bonus"].beneficiary_id == p1.id and kinds["cliente"].beneficiary_id == p3.id
    # segunda parcela também gera bônus (bônus por parcela do primeiro contrato)
    rc2, lines2, _ = svc.register_receipt(c.installments[1], date.today(), D("10000"), "NF-2", D("10000"), D("1000"), "fin")
    assert {l.kind for l in lines2} == {"cliente", "bonus"}
    # segundo contrato do recrutado NÃO gera bônus
    r2 = mkref(p3, "53000003")
    svc.validate_referral(r2, "accept", "g")
    svc.register_consent(r2, "ok", "g")
    c2 = svc.win_contract(r2, "CGV", None, date.today(), [eng.ScheduleItem("R", "retainer", D("5000"), date.today(), 3)], "g")
    assert not c2.first_for_recruited
    _, l3, _ = svc.register_receipt(c2.installments[0], date.today(), D("5000"), "NF-3", D("5000"), D("500"), "fin")
    assert {l.kind for l in l3} == {"cliente"}


def test_recebimento_parcial_e_limite_pela_nf(app, p1):
    r, c = won_contract(p1, "53000004")
    i = c.installments[0]
    _, l1, _ = svc.register_receipt(i, date.today(), D("4000"), "NF-1", D("10000"), D("1000"), "fin")
    assert i.status == "aberta" and l1[0].amount == D("540.00")
    _, l2, _ = svc.register_receipt(i, date.today(), D("6000"), "NF-1", D("10000"), D("1000"), "fin")
    assert i.status == "recebida" and l2[0].amount == D("810.00")
    with pytest.raises(svc.ServiceError, match="passar do valor"):
        svc.register_receipt(i, date.today(), D("1"), "NF-1", D("10000"), D("1000"), "fin")


def test_parcela_fora_da_janela_nao_gera_comissao(app, p1):
    r = mkref(p1, "53000005")
    svc.validate_referral(r, "accept", "g")
    svc.register_consent(r, "ok", "g")
    signed = date.today()
    c = svc.win_contract(r, "CGV", None, signed, [eng.ScheduleItem("R", "retainer", D("1000"), signed, months=14)], "g")
    fora = [i for i in c.installments if not i.in_window]
    assert fora
    _, lines, _ = svc.register_receipt(fora[0], date.today(), D("1000"), "NF-X", D("1000"), D("100"), "fin")
    assert lines == []


def test_nivel_3_nao_gera_comissao_direta_e_fica_retido(app, p1):
    p1.level = 3
    r, c = won_contract(p1, "53000006")
    _, lines, _ = svc.register_receipt(c.installments[0], date.today(), D("10000"), "NF-1", D("10000"), D("1000"), "fin")
    assert "Nível 3" in lines[0].hold_reason
    with pytest.raises(svc.ServiceError, match="Nível 3"):
        svc.approve_line(lines[0], "fin")


def test_aviso_quando_impostos_divergem_da_aliquota_de_referencia(app, p1):
    r, c = won_contract(p1, "53000007")
    _, _, warns = svc.register_receipt(c.installments[0], date.today(), D("10000"), "NF-1", D("10000"), D("3000"), "fin")
    assert warns and "diferem" in warns[0]


def test_regra_congelada_na_data_de_aceite(app, p1):
    r = mkref(p1, "53000008")
    svc.validate_referral(r, "accept", "g")
    svc.register_consent(r, "ok", "g")
    new_version(active_ruleset(), {"commission_pct": "0.10"}, "t")
    db.session.commit()
    c = svc.win_contract(r, "CGV", None, date.today(), [eng.ScheduleItem("R", "retainer", D("10000"), date.today(), 2)], "g")
    _, lines, _ = svc.register_receipt(c.installments[0], date.today(), D("10000"), "NF-1", D("10000"), D("1000"), "fin")
    assert lines[0].amount == D("1350.00")          # 15% da regra vigente no aceite, não 10%


# ------------------------------------------------------------- NF e pagamento
def approved_line(partner, root="54000001"):
    r, c = won_contract(partner, root)
    _, lines, _ = svc.register_receipt(c.installments[0], date.today(), D("10000"), "NF-1", D("10000"), D("1000"), "fin")
    svc.approve_line(lines[0], "fin")
    return lines[0]


def test_ciclo_nf_valor_deve_bater_e_pagamento_depois_da_conferencia(app, p1):
    line = approved_line(p1)
    with pytest.raises(svc.ServiceError, match="igual ao total"):
        svc.attach_invoice(app.config, p1, "N1", date.today(), D("1000"), None, [line.id])
    inv = svc.attach_invoice(app.config, p1, "N1", date.today(), D("1350"), None, [line.id])
    assert line.state == "programada" and line.cycle >= eng.next_month(date.today())
    with pytest.raises(svc.ServiceError, match="não conferida"):
        svc.pay_cycle(p1, line.cycle, "PIX 123", "fin")
    svc.review_invoice(inv, True, None, "fin")
    out = svc.pay_cycle(p1, line.cycle, "PIX 123", "fin")
    assert out["paid"] == D("1350.00") and line.state == "paga"


def test_pagamento_bloqueado_sem_titularidade_da_conta(app, p1):
    line = approved_line(p1, "54000002")
    inv = svc.attach_invoice(app.config, p1, "N1", date.today(), D("1350"), None, [line.id])
    svc.review_invoice(inv, True, None, "fin")
    p1.bank_holder_doc = cn.make_valid("99999999")
    with pytest.raises(svc.ServiceError, match="CNPJ da empresa"):
        svc.pay_cycle(p1, line.cycle, "PIX", "fin")
    assert svc.receiving_block_reasons(p1)


def test_nf_recusada_devolve_linha_para_aprovada(app, p1):
    line = approved_line(p1, "54000003")
    inv = svc.attach_invoice(app.config, p1, "N1", date.today(), D("1350"), None, [line.id])
    svc.review_invoice(inv, False, "Razão social divergente", "fin")
    assert line.state == "aprovada" and line.invoice_id is None


def test_estorno_negativo_acumula_para_o_mes_seguinte(app, p1):
    r, c = won_contract(p1, "54000004")
    i = c.installments[0]
    _, l1, _ = svc.register_receipt(i, date.today(), D("10000"), "NF-1", D("10000"), D("1000"), "fin")
    _, l2, _ = svc.register_receipt(i, date.today(), D("-10000"), "NF-1", D("10000"), D("1000"), "fin")
    assert l2[0].amount == D("-1350.00") and i.status == "aberta"
    for l in (l1[0], l2[0]):
        svc.approve_line(l, "fin")
    adj = svc.add_adjustment(p1, D("100"), "Ajuste manual", "fin")
    assert adj.state == "aprovada"


def test_upload_de_nf_rejeita_formato_invalido(app, p1):
    import io
    from werkzeug.datastructures import FileStorage
    line = approved_line(p1, "54000005")
    bad = FileStorage(stream=io.BytesIO(b"x"), filename="virus.exe")
    with pytest.raises(svc.ServiceError, match="Formato"):
        svc.attach_invoice(app.config, p1, "N1", date.today(), D("1350"), bad, [line.id])
    good = FileStorage(stream=io.BytesIO(b"%PDF-1.4"), filename="nf.pdf")
    inv = svc.attach_invoice(app.config, p1, "N1", date.today(), D("1350"), good, [line.id])
    assert inv.file_path.endswith("nf.pdf")


def test_projecao_estimada_nao_e_saldo(app, p1):
    r, c = won_contract(p1, "54000006")
    proj = svc.projected_lines(p1)
    assert len(proj) == 12 and proj[0]["amount"] == D("1350.00")   # 10.000 × (1 − 10%) × 15%


def test_alerta_de_frequencia(app, p1):
    new_version(active_ruleset(), {"high_frequency_threshold": 2}, "t")
    db.session.commit()
    for i in range(2):
        r = mkref(p1, f"5500000{i}")
        svc.validate_referral(r, "accept", "g")
    assert svc.frequency_alerts(active_ruleset().rules)[0][1] == 2


def test_saldo_liquido_negativo_acumula_para_o_mes_seguinte(app, p1):
    line = approved_line(p1, "54000007")                      # comissão de 1.350 aprovada
    inv = svc.attach_invoice(app.config, p1, "N1", date.today(), D("1350"), None, [line.id])
    svc.review_invoice(inv, True, None, "fin")
    # estorno posterior maior que a comissão programada: saldo do ciclo fica negativo
    adj = svc.add_adjustment(p1, D("-2000"), "Estorno de parcela já paga", "fin")
    adj.state, adj.cycle, adj.invoice_id = "programada", line.cycle, inv.id
    db.session.commit()
    original = line.cycle
    out = svc.pay_cycle(p1, original, "PIX", "fin")
    assert out["carried"] and out["paid"] == D("0")
    assert line.state == "programada" and line.cycle == eng.next_month(original)
    assert adj.cycle == eng.next_month(original)
