"""Plataforma Finder Fee — All Targets."""
from __future__ import annotations

import os

import click
from flask import Flask
from flask_migrate import Migrate

from . import core
from .core import Config, db


MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


def create_app(config: dict | None = None, web: bool = True) -> Flask:
    app = Flask(__name__, instance_path=os.path.join(os.getcwd(), "instance"))
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    os.makedirs(app.instance_path, exist_ok=True)
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("sqlite:///") and not uri.startswith("sqlite:////") and ":memory:" not in uri:
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, uri.replace("sqlite:///", ""))
    if os.environ.get("FF_ENV") == "production":
        key = app.config["SECRET_KEY"] or ""
        if key == "dev-troque-esta-chave" or len(key) < 32:
            # a chave assina o cookie de sessão: curta ou conhecida, dá para forjar
            raise RuntimeError("Defina FF_SECRET_KEY em produção, com ao menos 32 caracteres aleatórios "
                               "(ex.: python -c \"import secrets; print(secrets.token_urlsafe(48))\").")

    if app.config["TRUSTED_PROXIES"] > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    db.init_app(app)
    from . import models  # noqa: F401
    # Migrações do banco (Alembic). render_as_batch: o SQLite só altera coluna recriando a tabela.
    Migrate(app, db, directory=MIGRATIONS_DIR, render_as_batch=True, compare_type=True,
            render_item=core.render_migration_item)
    from .security import init_security
    init_security(app)

    from . import cnpj as _cn
    app.jinja_env.filters.update(brl=core.brl, pct=core.pct, dt=core.dt, cnpj_fmt=_cn.fmt)
    from .services import is_web_link
    app.jinja_env.globals.update(brl=core.brl, pct=core.pct, dt=core.dt, is_web_link=is_web_link)

    if web:
        from .web import register_web
        register_web(app)

    _register_cli(app)
    return app


def _register_cli(app: Flask):
    from . import seed

    @app.cli.command("init-db")
    def init_db():
        """Cria ou atualiza o banco (aplica as migrações pendentes) e grava os dados iniciais.
        Pode rodar a cada implantação: o que já foi aplicado não se repete."""
        from flask_migrate import upgrade
        upgrade()
        seed.seed_defaults()
        click.echo("Banco pronto.")

    @app.cli.command("create-user")
    @click.argument("email")
    @click.argument("name")
    @click.option("--roles", default="admin", help="admin, manager, commercial, finance (separados por vírgula)")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_user(email, name, roles, password):
        """Cria um usuário interno (gestor, comercial, financeiro ou administrador)."""
        seed.create_staff(email, name, roles, password)
        click.echo(f"Usuário {email} criado com perfis: {roles}.")

    @app.cli.command("demo-data")
    def demo_data():
        """Carrega dados de demonstração (NÃO usar em produção)."""
        from flask_migrate import upgrade
        upgrade()
        seed.seed_defaults()
        seed.seed_demo()
        click.echo("Dados de demonstração carregados. Acesso: gestor@demo.test / demo-senha-123")

    @app.cli.command("run-jobs")
    def run_jobs():
        """Rotinas diárias: expira proteções vencidas e envia avisos por e-mail (se SMTP configurado)."""
        from . import services
        click.echo(services.run_daily_jobs())
        click.echo(f"E-mails enviados: {services.send_pending_emails(app)}")

    @app.cli.command("email-backlog")
    @click.option("--discard", is_flag=True, help="Marca todos os avisos da fila como tratados, sem enviar.")
    def email_backlog(discard):
        """Mostra os avisos que ainda sairiam por e-mail. Use --discard antes de ligar o SMTP pela primeira vez."""
        from . import services
        info = services.email_backlog()
        quando = core.dt(info["mais_antigo"], with_time=True) if info["mais_antigo"] else "—"
        click.echo(f"Avisos na fila de e-mail: {info['pendentes']} (mais antigo: {quando}).")
        if discard:
            n = services.discard_email_backlog("cli")
            click.echo(f"{n} aviso(s) descartado(s). Continuam visíveis no portal; não sairão por e-mail.")

    @app.cli.command("retention")
    @click.option("--apply", "do_apply", is_flag=True, help="Aplica os prazos (sem isto, só mostra o que seria feito).")
    def retention(do_apply):
        """Retenção de dados pessoais (LGPD): mostra ou aplica os prazos definidos em Regras e parâmetros."""
        from . import privacidade
        prev = privacidade.retention_preview()
        pol = prev["policy"]
        for k, label in (("retention_referral_months", "Indicações encerradas sem contrato"),
                         ("retention_partner_months", "Parceiros não aprovados ou suspensos"),
                         ("retention_notification_months", "Avisos do portal")):
            click.echo(f"{label}: {str(pol[k]) + ' meses' if pol[k] else 'prazo não definido (não aplica)'}")
        click.echo(f"Seriam anonimizados agora: {len(prev['referrals'])} indicação(ões), {len(prev['partners'])} parceiro(s); "
                   f"{prev['notifications']} aviso(s) apagados.")
        if prev["partners_blocked"]:
            click.echo("Parceiros no prazo, mas com pagamento pendente (ficam para depois): "
                       + ", ".join(p.code for p in prev["partners_blocked"]))
        if do_apply:
            click.echo(f"Aplicado: {privacidade.apply_retention('cli')}")

    @app.cli.command("weekly-report")
    @click.option("--week-end", default=None, help="Domingo de fechamento (AAAA-MM-DD). Padrão: último domingo.")
    def weekly_report(week_end):
        """Emite o relatório semanal do piloto (xlsx + PDF)."""
        from datetime import date
        from . import reports
        end = date.fromisoformat(week_end) if week_end else reports.last_sunday(core.today())
        ed = reports.generate_weekly(end, app.config["REPORT_DIR"])
        click.echo(f"Edição {ed.edition}: {ed.xlsx_path} | {ed.pdf_path}")

    @app.cli.command("simulate-bonus")
    def simulate_bonus():
        """Valida o bônus da Camada 2 com um contrato simulado, em banco temporário (não toca nos dados reais)."""
        from .simulate import run
        click.echo(run())
