"""Feriados: cálculo dos nacionais, cadastro pela tela e efeito nos prazos em dias úteis."""
from datetime import date, datetime, timedelta

import pytest

from app import engine as eng, seed, services as svc
from app.core import db
from app.feriados import easter, national_holidays
from app.models import AuditLog, Holiday, Partner, Referral
from app.rules import active_ruleset, holidays, new_version


def login(client, email, pw):
    return client.post("/entrar", data={"email": email, "password": pw})


@pytest.mark.parametrize("year, expected", [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5)),
                                            (2027, date(2027, 3, 28)), (2038, date(2038, 4, 25)), (2000, date(2000, 4, 23))])
def test_pascoa(year, expected):
    assert easter(year) == expected


def test_feriados_nacionais_de_2026():
    days = dict(national_holidays(2026))
    assert len(days) == 10
    assert days[date(2026, 4, 3)] == "Sexta-feira Santa"
    assert date(2026, 11, 20) in days                       # Consciência Negra, nacional desde 2024
    assert date(2026, 2, 16) not in days                    # Carnaval só com os pontos facultativos
    com = dict(national_holidays(2026, optional=True))
    assert {date(2026, 2, 16), date(2026, 2, 17), date(2026, 6, 4)} <= set(com)
    assert date(2023, 11, 20) not in dict(national_holidays(2023))


def test_feriado_cadastrado_nao_conta_como_dia_util(app):
    sexta, segunda = date(2026, 4, 3), date(2026, 4, 6)
    quinta = date(2026, 4, 2)
    assert eng.business_days_between(quinta, segunda, holidays()) == 2
    svc.add_holiday(sexta, "Sexta-feira Santa", "t")
    assert eng.business_days_between(quinta, segunda, holidays()) == 1


def test_feriado_vale_para_indicacao_de_qualquer_versao_das_regras(app):
    """Feriado não é regra versionada: cadastrar um não cria versão nova, e vale para todas."""
    antes = active_ruleset().id
    svc.add_holiday(date(2026, 12, 24), "Véspera de Natal", "t")
    assert active_ruleset().id == antes
    new_version(active_ruleset(), {"sla_review_days": 4}, "t", "teste")
    db.session.commit()
    assert date(2026, 12, 24) in holidays(active_ruleset().rules)


def test_lista_antiga_nas_regras_continua_valendo(app):
    rs = new_version(active_ruleset(), {"holidays": ["2026-07-09"]}, "t", "feriado estadual antigo")
    db.session.commit()
    assert date(2026, 7, 9) in holidays(rs.rules)


def test_duplicado_nome_vazio_e_remocao(app):
    svc.add_holiday(date(2026, 1, 25), "Aniversário de São Paulo", "t")
    with pytest.raises(svc.ServiceError, match="já está cadastrado"):
        svc.add_holiday(date(2026, 1, 25), "Outro", "t")
    with pytest.raises(svc.ServiceError, match="nome"):
        svc.add_holiday(date(2026, 3, 1), "  ", "t")
    svc.remove_holiday(date(2026, 1, 25), "t")
    assert Holiday.query.count() == 0
    with pytest.raises(svc.ServiceError):
        svc.remove_holiday(date(2026, 1, 25), "t")
    assert [a.action for a in AuditLog.query.order_by(AuditLog.id)] == ["holiday.add", "holiday.remove"]


def test_nacionais_nao_duplicam_e_preservam_o_que_ja_existe(app):
    svc.add_holiday(date(2026, 4, 21), "Tiradentes (nome local)", "t")
    assert svc.add_national_holidays(2026, "t") == 9
    assert svc.add_national_holidays(2026, "t") == 0
    assert db.session.get(Holiday, date(2026, 4, 21)).name == "Tiradentes (nome local)"
    assert svc.add_national_holidays(2026, "t", optional=True) == 3


# ------------------------------------------------------------------ tela
@pytest.fixture()
def world(webapp):
    seed.seed_demo()
    return webapp


def test_tela_cadastra_nacionais_e_remove(world):
    c = world.test_client()
    login(c, "gestor@demo.test", "demo-senha-123")
    assert c.get("/gestao/feriados?ano=2026").status_code == 200
    r = c.post("/gestao/feriados", data={"action": "national", "ano": "2026"}, follow_redirects=True)
    assert "10 feriado(s) nacional(is) de 2026" in r.get_data(as_text=True)
    html = c.get("/gestao/feriados?ano=2026").get_data(as_text=True)
    assert "Sexta-feira Santa" in html and "03/04/2026" in html
    assert "já não conta como dia útil" in html              # 15/11/2026 cai num domingo
    c.post("/gestao/feriados", data={"action": "add", "day": "2026-07-09", "name": "Revolução Constitucionalista"})
    c.post("/gestao/feriados", data={"action": "del", "day": "2026-04-21"})
    assert db.session.get(Holiday, date(2026, 7, 9)) is not None
    assert db.session.get(Holiday, date(2026, 4, 21)) is None
    r = c.post("/gestao/feriados", data={"action": "add", "day": "", "name": "x"}, follow_redirects=True)
    assert "Informe uma data válida" in r.get_data(as_text=True)


def test_so_admin_e_gestor_alteram(world):
    seed.create_staff("fin@t.test", "Fin", "finance", "senha-longa-123")
    c = world.test_client()
    login(c, "fin@t.test", "senha-longa-123")
    r = c.get("/gestao/feriados")
    assert r.status_code == 200 and "Cadastrar feriados nacionais" not in r.get_data(as_text=True)
    assert c.post("/gestao/feriados", data={"action": "national", "ano": "2026"}).status_code == 403
    assert Holiday.query.count() == 0
    p = world.test_client()
    login(p, "parceiro1@demo.test", "senha-segura-123")
    assert p.get("/gestao/feriados").status_code == 403


def test_painel_mostra_prazo_das_indicacoes_em_dias_uteis(world):
    """Indicação registrada na quinta antes da Sexta-feira Santa: na segunda seguinte são 2 dias úteis, não 3."""
    ref = Referral.query.filter(Referral.eligibility.in_(("recebida", "em_validacao"))).first()
    ref.created_at = datetime(2026, 4, 2, 10)
    db.session.commit()
    q = svc.manager_queues(on=date(2026, 4, 6))
    assert q["dias_indicacoes"][ref.id] == 2
    svc.add_holiday(date(2026, 4, 3), "Sexta-feira Santa", "t")
    q = svc.manager_queues(on=date(2026, 4, 6))
    assert q["dias_indicacoes"][ref.id] == 1
    c = world.test_client()
    login(c, "gestor@demo.test", "demo-senha-123")
    assert "de 5 dias úteis" in c.get("/gestao/").get_data(as_text=True)
