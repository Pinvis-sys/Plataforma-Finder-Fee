"""Migrações do banco: o esquema gerado bate com os modelos e bancos antigos são atualizados sem perder dados."""
import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import downgrade, upgrade

from app import create_app, seed
from app.core import db
from app.models import User

from .conftest import TEST_DB


def _diffs():
    with db.engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, db.metadata)


def _tables():
    return set(sa.inspect(db.engine).get_table_names())


def _drop_everything():
    db.session.remove()
    db.drop_all()
    with db.engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture()
def blank(tmp_path):
    """App com banco vazio, sem create_all: quem cria as tabelas é a migração."""
    uri = TEST_DB if not TEST_DB.endswith(":memory:") else f"sqlite:///{tmp_path / 'mig.db'}"
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": uri, "SECRET_KEY": "teste"}, web=False)
    with app.app_context():
        _drop_everything()
        yield app
        _drop_everything()


def test_migracoes_criam_o_mesmo_esquema_dos_modelos(blank):
    upgrade()
    assert _diffs() == []
    seed.seed_defaults()                      # o banco migrado funciona com o app


def test_banco_antigo_do_init_db_e_atualizado_sem_perder_dados(blank):
    """Banco criado pelo init-db anterior (create_all), sem registro de versão e sem as mudanças mais novas."""
    upgrade(revision="0001")
    with db.engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE alembic_version"))
        conn.execute(sa.text("DROP TABLE user_session"))
        conn.execute(sa.text("INSERT INTO \"user\" (email, name, password_hash, roles, active, must_change_password, "
                             "failed_logins, created_at) VALUES ('velho@t.test', 'Velho', 'x', 'admin', true, false, 0, "
                             "CURRENT_TIMESTAMP)"))
    assert "user_session" not in _tables()

    upgrade()

    assert {"user_session", "alembic_version"} <= _tables()
    assert _diffs() == []
    assert User.query.filter_by(email="velho@t.test").one().totp_last_step is None


def test_desfazer_e_refazer_as_migracoes(blank):
    upgrade()
    downgrade(revision="base")
    assert _tables() <= {"alembic_version"}
    upgrade()
    assert _diffs() == []


def test_init_db_aplica_as_migracoes(blank):
    result = blank.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    assert _diffs() == []
    result = blank.test_cli_runner().invoke(args=["init-db"])     # rodar de novo não repete nada
    assert result.exit_code == 0, result.output


def test_migracoes_nao_importam_o_app():
    """Migração antiga precisa continuar rodando mesmo depois que o código do app mudar."""
    import pathlib
    for f in (pathlib.Path(__file__).parent.parent / "migrations" / "versions").glob("*.py"):
        text = f.read_text()
        assert "import app" not in text and "from app" not in text, f.name
