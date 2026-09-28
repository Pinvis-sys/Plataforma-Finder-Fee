import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from app import create_app, seed
from app.core import db


# Por padrão os testes usam SQLite em memória. Para rodar contra PostgreSQL (banco descartável: é apagado a cada teste):
#   FF_TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost/finder_fee_test pytest -q
TEST_DB = os.environ.get("FF_TEST_DATABASE_URL", "sqlite:///:memory:")


def _make(tmp_path, web):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": TEST_DB, "TESTING_SKIP_CSRF": True,
                       "UPLOAD_DIR": str(tmp_path / "uploads"), "REPORT_DIR": str(tmp_path / "reports"),
                       "SECRET_KEY": "teste"}, web=web)


@pytest.fixture()
def app(tmp_path):
    app = _make(tmp_path, web=False)
    with app.app_context():
        db.create_all()
        seed.seed_defaults()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def webapp(tmp_path):
    app = _make(tmp_path, web=True)
    with app.app_context():
        db.create_all()
        seed.seed_defaults()
        yield app
        db.session.remove()
        db.drop_all()
