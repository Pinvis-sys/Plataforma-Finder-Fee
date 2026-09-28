import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from app import create_app, seed
from app.core import db


def _make(tmp_path, web):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING_SKIP_CSRF": True,
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
