"""Envios simultâneos contra um banco real (PostgreSQL). Pulado no SQLite em memória, que não admite conexões paralelas.

    FF_TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost/finder_fee_test pytest -q tests/test_concorrencia.py
"""
import threading

import pytest

from app import cnpj as cn, seed, services as svc
from app.core import db
from app.models import Partner, Referral

from .conftest import TEST_DB

pytestmark = pytest.mark.skipif(not TEST_DB.startswith("postgresql"), reason="exige FF_TEST_DATABASE_URL com PostgreSQL")


def _simultaneo(app, partner_ids, cnpjs):
    barrier, out = threading.Barrier(len(partner_ids)), []

    def worker(pid, num):
        with app.app_context():
            p = db.session.get(Partner, pid)
            barrier.wait()
            try:
                svc.register_referral(p, {"cnpj": num, "decisor_name": "F", "signal": "s"})
                out.append("ok")
            except svc.Unavailable:
                out.append("indisponivel")
            except Exception as e:                      # noqa: BLE001 - o teste quer ver qualquer falha
                out.append(type(e).__name__)
            finally:
                db.session.remove()

    ts = [threading.Thread(target=worker, args=a) for a in zip(partner_ids, cnpjs)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return sorted(out)


@pytest.fixture()
def parceiros(app):
    ids = [seed.make_active_partner(f"Parceiro {i}", f"7{i:07d}", f"r{i}@t.test").id for i in range(5)]
    db.session.commit()
    db.session.remove()          # libera a conexão do teste antes das threads
    return ids


def test_mesma_empresa_ao_mesmo_tempo_so_um_parceiro_leva(app, parceiros):
    r = _simultaneo(app, parceiros, [cn.make_valid("81000001")] * len(parceiros))
    assert r == ["indisponivel"] * 4 + ["ok"]
    assert Referral.query.count() == 1


def test_empresas_diferentes_ao_mesmo_tempo_todas_gravadas(app, parceiros):
    r = _simultaneo(app, parceiros, [cn.make_valid(f"82{i:06d}") for i in range(len(parceiros))])
    assert r == ["ok"] * len(parceiros)
    codes = [x.code for x in Referral.query.all()]
    assert len(set(codes)) == len(parceiros) and all(codes)


def test_mesmo_codigo_de_duas_etapas_ao_mesmo_tempo_so_um_entra(webapp):
    """Cinco logins simultâneos com a mesma senha e o mesmo código: só um abre sessão."""
    import time
    from app import security as sec
    from app.models import User, UserSession
    u = seed.create_staff("s@t.test", "Staff", "admin", "senha-longa-123")
    u.totp_secret = sec.new_totp_secret()
    db.session.commit()
    uid, code = u.id, sec.totp_now(u.totp_secret, time.time())
    db.session.remove()

    clients = [webapp.test_client() for _ in range(5)]
    for c in clients:
        assert c.post("/entrar", data={"email": "s@t.test", "password": "senha-longa-123"}).status_code == 302
    barrier, out = threading.Barrier(len(clients)), []

    def worker(c):
        barrier.wait()
        out.append(c.post("/entrar/verificacao", data={"code": code}).status_code)

    ts = [threading.Thread(target=worker, args=(c,)) for c in clients]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [200] * 4 + [302]
    assert UserSession.query.filter_by(user_id=uid).count() == 1
