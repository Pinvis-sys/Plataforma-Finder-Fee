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
