"""Camada web: rotas e telas."""
from __future__ import annotations

from flask import render_template, request

from ..models import ELIGIBILITY, FIN_STATES, PARTNER_KINDS, PARTNER_STATUS, Notification, STAGES
from ..security import current_user


def register_web(app):
    from .auth import bp as auth_bp
    from .manage import bp as manage_bp
    from .portal import bp as portal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(manage_bp, url_prefix="/gestao")

    @app.context_processor
    def _ctx():
        u = current_user()
        unread = 0
        if u:
            unread = Notification.query.filter_by(user_id=u.id, read_at=None).count()
        return {"me": u, "unread": unread, "PARTNER_STATUS": PARTNER_STATUS, "STAGES": STAGES, "FIN_STATES": FIN_STATES,
                "ELIGIBILITY": ELIGIBILITY, "PARTNER_KINDS": PARTNER_KINDS, "endpoint": request.endpoint or ""}

    @app.errorhandler(403)
    def _403(e):
        return render_template("erro.html", titulo="Acesso não permitido", msg="Seu perfil não tem acesso a esta página."), 403

    @app.errorhandler(404)
    def _404(e):
        return render_template("erro.html", titulo="Página não encontrada", msg="Confira o endereço ou volte ao painel."), 404

    @app.errorhandler(400)
    def _400(e):
        return render_template("erro.html", titulo="Não foi possível concluir", msg=getattr(e, "description", "Requisição inválida.")), 400

    @app.errorhandler(413)
    def _413(e):
        return render_template("erro.html", titulo="Arquivo muito grande", msg="O arquivo pode ter até 8 MB."), 413
